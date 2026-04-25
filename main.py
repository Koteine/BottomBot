import ctypes
import threading
import time
from ctypes import wintypes
from dataclasses import dataclass
from datetime import datetime
from typing import Callable

import customtkinter as ctk
import cv2
import mss
import numpy as np
import pydirectinput

# ----------------------------- WinAPI -----------------------------
user32 = ctypes.windll.user32


class POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


class RECT(ctypes.Structure):
    _fields_ = [
        ("left", ctypes.c_long),
        ("top", ctypes.c_long),
        ("right", ctypes.c_long),
        ("bottom", ctypes.c_long),
    ]


# ----------------------------- Настройки -----------------------------
WINDOW_TITLE_PART = "freestreet"
WINDOW_CLASS_HINTS = ("UnityWndClass", "UnrealWindow", "FreeStreet")
MIN_CLIENT_SIZE = (300, 200)

# Базовые зоны относительно клиентской области 1920x1080
ARROW_ZONE_REL = {"top": 760, "left": 450, "width": 1100, "height": 120}
PERFECT_ZONE_REL = {"top": 745, "left": 808, "width": 30, "height": 120}

# Узкая зона быстрого триггера (Smartdancer)
TRIGGER_SLICE_WIDTH = 180

RATING_PRESETS = {
    "Идеал": {"perfect_brightness": 235},
    "Круто": {"perfect_brightness": 225},
    "Хорошо": {"perfect_brightness": 210},
}


@dataclass(frozen=True)
class WindowClientArea:
    hwnd: int
    title: str
    left: int
    top: int
    width: int
    height: int


@dataclass(frozen=True)
class CaptureZones:
    arrow_zone: dict
    trigger_zone: dict
    perfect_zone: dict


class CalibrationOverlay:
    """Простая красная рамка из 4 top-level окон (без прозрачной магии)."""

    def __init__(self, root: ctk.CTk) -> None:
        self.root = root
        self.windows: list[ctk.CTkToplevel] = []

    def _make_bar(self, x: int, y: int, w: int, h: int) -> ctk.CTkToplevel:
        win = ctk.CTkToplevel(self.root)
        win.overrideredirect(True)
        win.attributes("-topmost", True)
        win.configure(fg_color="#ff2d2d")
        win.geometry(f"{max(1, w)}x{max(1, h)}+{x}+{y}")
        return win

    def show(self, left: int, top: int, width: int, height: int, duration_ms: int = 1800) -> None:
        self.hide()
        thickness = 3
        self.windows.append(self._make_bar(left, top, width, thickness))
        self.windows.append(self._make_bar(left, top + height - thickness, width, thickness))
        self.windows.append(self._make_bar(left, top, thickness, height))
        self.windows.append(self._make_bar(left + width - thickness, top, thickness, height))
        self.root.after(duration_ms, self.hide)

    def hide(self) -> None:
        for win in self.windows:
            try:
                win.destroy()
            except Exception:
                pass
        self.windows.clear()


class BotBackend:
    def __init__(self, logger: Callable[[str], None], status: Callable[[str], None], action: Callable[[str], None]):
        self.log = logger
        self.set_status = status
        self.set_action = action

        self.auto_keys_enabled = True
        self.auto_space_enabled = True
        self.template_threshold = 0.82
        self.perfect_brightness_threshold = 225
        self.scan_cooldown_sec = 0.001
        self.beat_lock_sec = 0.16
        self.is_active = False

        self.zone_offset_x = 0
        self.zone_offset_y = 0

        self._worker: threading.Thread | None = None
        self._stop_event = threading.Event()

        self.templates = self._load_arrow_templates("assets")
        self.last_window: WindowClientArea | None = None

        pydirectinput.FAILSAFE = False
        pydirectinput.PAUSE = 0

    @staticmethod
    def _load_arrow_templates(assets_dir: str) -> dict[str, np.ndarray]:
        files = {
            "left": "left.png",
            "down": "down.png",
            "up": "up.png",
            "right": "right.png",
        }
        templates: dict[str, np.ndarray] = {}
        for key, filename in files.items():
            template = cv2.imread(f"{assets_dir}/{filename}", cv2.IMREAD_GRAYSCALE)
            if template is None:
                raise FileNotFoundError(f"Не найден шаблон: {assets_dir}/{filename}")
            templates[key] = cv2.GaussianBlur(template, (3, 3), 0)
        return templates

    @staticmethod
    def _window_text(hwnd: int) -> str:
        length = user32.GetWindowTextLengthW(hwnd)
        buffer = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buffer, length + 1)
        return buffer.value

    @staticmethod
    def _window_class(hwnd: int) -> str:
        buffer = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(hwnd, buffer, 256)
        return buffer.value

    @staticmethod
    def _is_minimized(hwnd: int) -> bool:
        return bool(user32.IsIconic(hwnd))

    def find_game_window(self) -> WindowClientArea | None:
        found: list[int] = []

        @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        def enum_proc(hwnd, _lparam):
            if not user32.IsWindowVisible(hwnd):
                return True
            title = self._window_text(hwnd)
            if not title:
                return True
            class_name = self._window_class(hwnd)

            title_match = WINDOW_TITLE_PART in title.lower()
            class_match = any(hint.lower() in class_name.lower() for hint in WINDOW_CLASS_HINTS)
            if title_match or class_match:
                found.append(int(hwnd))
            return True

        user32.EnumWindows(enum_proc, 0)

        for hwnd in found:
            client = self.get_client_coords(hwnd)
            if client is None:
                continue
            if client.width < MIN_CLIENT_SIZE[0] or client.height < MIN_CLIENT_SIZE[1]:
                continue
            title = self._window_text(hwnd)
            return WindowClientArea(
                hwnd=hwnd,
                title=title,
                left=client.left,
                top=client.top,
                width=client.width,
                height=client.height,
            )
        return None

    def get_client_coords(self, hwnd: int) -> WindowClientArea | None:
        """
        Возвращает координаты именно клиентской области окна (без рамки и заголовка).
        Это критично для точного попадания по элементам на 1920x1080.
        """
        rect = RECT()
        if not user32.GetClientRect(hwnd, ctypes.byref(rect)):
            return None

        tl = POINT(rect.left, rect.top)
        br = POINT(rect.right, rect.bottom)
        if not user32.ClientToScreen(hwnd, ctypes.byref(tl)):
            return None
        if not user32.ClientToScreen(hwnd, ctypes.byref(br)):
            return None

        width = max(0, br.x - tl.x)
        height = max(0, br.y - tl.y)
        title = self._window_text(hwnd)
        return WindowClientArea(hwnd=hwnd, title=title, left=tl.x, top=tl.y, width=width, height=height)

    def _apply_zone(self, rel_zone: dict, client: WindowClientArea) -> dict:
        return {
            "left": int(client.left + rel_zone["left"] + self.zone_offset_x),
            "top": int(client.top + rel_zone["top"] + self.zone_offset_y),
            "width": int(rel_zone["width"]),
            "height": int(rel_zone["height"]),
        }

    def _resolve_zones(self) -> CaptureZones | None:
        window = self.find_game_window()
        if window is None:
            self.set_status("Статус: Окно не найдено")
            self.set_action("Ожидание окна FreeStreet")
            self.last_window = None
            return None

        self.last_window = window
        self.set_status("Статус: Окно найдено")
        self.log(
            f"Окно: '{window.title}' | client(left={window.left}, top={window.top}, "
            f"w={window.width}, h={window.height})"
        )

        if self._is_minimized(window.hwnd):
            self.set_status("Статус: Окно найдено (свернуто)")
            self.set_action("Разверните окно игры")
            return None

        fg = user32.GetForegroundWindow()
        if int(fg) != window.hwnd:
            self.set_action("Пытаюсь активировать окно")
            user32.ShowWindow(window.hwnd, 9)  # SW_RESTORE
            user32.SetForegroundWindow(window.hwnd)
            time.sleep(0.03)
            if int(user32.GetForegroundWindow()) != window.hwnd:
                self.set_status("Статус: Окно найдено (не активно)")
                self.set_action("Кликните по окну игры")
                return None

        self.set_status("Статус: Готов к работе")
        self.set_action("Сканирование")

        arrow_zone = self._apply_zone(ARROW_ZONE_REL, window)
        perfect_zone = self._apply_zone(PERFECT_ZONE_REL, window)
        trigger_zone = {
            "left": arrow_zone["left"],
            "top": arrow_zone["top"],
            "width": min(TRIGGER_SLICE_WIDTH, arrow_zone["width"]),
            "height": arrow_zone["height"],
        }
        return CaptureZones(arrow_zone=arrow_zone, trigger_zone=trigger_zone, perfect_zone=perfect_zone)

    def update_settings(
        self,
        auto_keys: bool,
        auto_space: bool,
        rating_mode: str,
        precision_threshold: float,
        zone_offset_x: int,
        zone_offset_y: int,
    ) -> None:
        self.auto_keys_enabled = auto_keys
        self.auto_space_enabled = auto_space
        self.template_threshold = max(0.75, min(0.99, float(precision_threshold)))

        preset = RATING_PRESETS.get(rating_mode, RATING_PRESETS["Круто"])
        self.perfect_brightness_threshold = preset["perfect_brightness"]

        self.zone_offset_x = int(zone_offset_x)
        self.zone_offset_y = int(zone_offset_y)

    def _detect_keys(self, gray_frame: np.ndarray) -> list[str]:
        blurred = cv2.GaussianBlur(gray_frame, (3, 3), 0)
        detections = []
        for direction, template in self.templates.items():
            res = cv2.matchTemplate(blurred, template, cv2.TM_CCOEFF_NORMED)
            ys, xs = np.where(res >= self.template_threshold)
            h, w = template.shape
            for y, x in zip(ys, xs):
                detections.append((direction, int(x + w / 2), int(y + h / 2)))

        detections.sort(key=lambda t: t[1])
        return [d[0] for d in detections]

    def _trigger_has_arrow(self, gray_trigger: np.ndarray) -> bool:
        blurred = cv2.GaussianBlur(gray_trigger, (3, 3), 0)
        for template in self.templates.values():
            res = cv2.matchTemplate(blurred, template, cv2.TM_CCOEFF_NORMED)
            if float(np.max(res)) >= (self.template_threshold - 0.03):
                return True
        return False

    @staticmethod
    def _press_combo(keys: list[str]) -> None:
        for key in keys:
            pydirectinput.keyDown(key, _pause=False)
            time.sleep(0.02)
            pydirectinput.keyUp(key, _pause=False)

    def _wait_perfect_and_space(self, sct: mss.mss, zones: CaptureZones) -> bool:
        timeout_sec = 1.8
        started = time.perf_counter()
        while self.is_active and not self._stop_event.is_set() and (time.perf_counter() - started) <= timeout_sec:
            frame = np.array(sct.grab(zones.perfect_zone))
            bgr = cv2.cvtColor(frame, cv2.COLOR_BGRA2BGR)
            brightness = int(np.max(bgr))
            if brightness >= self.perfect_brightness_threshold:
                pydirectinput.keyDown("space", _pause=False)
                time.sleep(0.02)
                pydirectinput.keyUp("space", _pause=False)
                self.log(f"Perfect: {brightness} -> SPACE")
                self.set_action(f"SPACE ({brightness})")
                return True
            time.sleep(0.001)
        return False

    def get_calibration_zone(self) -> tuple[int, int, int, int] | None:
        zones = self._resolve_zones()
        if zones is None:
            return None
        z = zones.arrow_zone
        return z["left"], z["top"], z["width"], z["height"]

    def start(self) -> None:
        if self.is_active:
            return
        self.is_active = True
        self._stop_event.clear()
        self._worker = threading.Thread(target=self._worker_loop, daemon=True)
        self._worker.start()
        self.set_status("Статус: Запущен")
        self.set_action("Сканирование")
        self.log("Бот запущен")

    def stop(self) -> None:
        if not self.is_active:
            return
        self.is_active = False
        self._stop_event.set()
        if self._worker and self._worker.is_alive():
            self._worker.join(timeout=1.2)
        self._worker = None
        self.set_status("Статус: Остановлен")
        self.set_action("Ожидание")
        self.log("Бот остановлен")

    def _worker_loop(self) -> None:
        with mss.mss() as sct:
            vision_locked_until = 0.0
            while self.is_active and not self._stop_event.is_set():
                zones = self._resolve_zones()
                if zones is None:
                    time.sleep(0.15)
                    continue

                now = time.perf_counter()
                if now < vision_locked_until:
                    time.sleep(self.scan_cooldown_sec)
                    continue

                # Smartdancer: быстрый просмотр узкой полоски
                trig = np.array(sct.grab(zones.trigger_zone))
                trig_gray = cv2.cvtColor(trig, cv2.COLOR_BGRA2GRAY)
                if not self._trigger_has_arrow(trig_gray):
                    time.sleep(self.scan_cooldown_sec)
                    continue

                # Мгновенный снимок всей полосы
                full = np.array(sct.grab(zones.arrow_zone))
                full_gray = cv2.cvtColor(full, cv2.COLOR_BGRA2GRAY)
                keys = self._detect_keys(full_gray)

                if not keys:
                    time.sleep(self.scan_cooldown_sec)
                    continue

                vision_locked_until = time.perf_counter() + self.beat_lock_sec

                if self.auto_keys_enabled:
                    self._press_combo(keys)
                    self.log(f"Комбо: {keys}")
                    self.set_action("Комбо: " + ", ".join(k.upper() for k in keys))

                if self.auto_space_enabled:
                    if not self._wait_perfect_and_space(sct, zones):
                        self.set_action("Perfect не найден")


class AristocratUI:
    def __init__(self) -> None:
        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("dark-blue")

        self.root = ctk.CTk()
        self.root.title("BottomBot DX")
        self.root.geometry("520x360")
        self.root.minsize(480, 320)
        self.root.resizable(True, True)
        self.root.configure(fg_color="#11081f")

        self.auto_keys_var = ctk.BooleanVar(value=True)
        self.auto_space_var = ctk.BooleanVar(value=True)
        self.rating_mode_var = ctk.StringVar(value="Круто")
        self.precision_threshold_var = ctk.DoubleVar(value=0.82)
        self.offset_x_var = ctk.IntVar(value=0)
        self.offset_y_var = ctk.IntVar(value=0)

        self.backend = BotBackend(self.append_log, self.set_status, self.set_last_action)
        self.overlay = CalibrationOverlay(self.root)

        self._build_layout()
        self._sync_backend()

    def _build_layout(self) -> None:
        self.root.grid_rowconfigure(2, weight=1)
        self.root.grid_columnconfigure(0, weight=1)

        top = ctk.CTkFrame(self.root, fg_color="#1b0f2f", corner_radius=12)
        top.grid(row=0, column=0, sticky="ew", padx=10, pady=(10, 8))

        self.start_btn = ctk.CTkButton(top, text="Запустить", command=self.toggle_bot, fg_color="#5d2e8c")
        self.start_btn.pack(side="left", padx=8, pady=8)

        self.calib_btn = ctk.CTkButton(top, text="Калибровка", command=self.show_calibration, fg_color="#9b2335")
        self.calib_btn.pack(side="left", padx=8, pady=8)

        self.status_label = ctk.CTkLabel(top, text="Статус: Остановлен", text_color="#e8ddff")
        self.status_label.pack(side="left", padx=10)

        self.action_label = ctk.CTkLabel(self.root, text="Последнее действие: Ожидание", text_color="#ccb8ff")
        self.action_label.grid(row=1, column=0, sticky="w", padx=16, pady=(0, 6))

        tabs = ctk.CTkTabview(self.root, fg_color="#1b0f2f")
        tabs.grid(row=2, column=0, sticky="nsew", padx=10, pady=(0, 10))

        main_tab = tabs.add("Основное")
        logs_tab = tabs.add("Логи")

        self._build_main_tab(main_tab)
        self._build_logs_tab(logs_tab)

    def _build_main_tab(self, tab) -> None:
        frame = ctk.CTkFrame(tab, fg_color="#22133a")
        frame.pack(fill="both", expand=True, padx=10, pady=10)

        ctk.CTkSwitch(frame, text="Авто-клавиши", variable=self.auto_keys_var, command=self._sync_backend).pack(
            anchor="w", padx=14, pady=(12, 6)
        )
        ctk.CTkSwitch(frame, text="Авто-пробел", variable=self.auto_space_var, command=self._sync_backend).pack(
            anchor="w", padx=14, pady=6
        )

        mode_row = ctk.CTkFrame(frame, fg_color="transparent")
        mode_row.pack(fill="x", padx=14, pady=(8, 6))
        ctk.CTkLabel(mode_row, text="Режим оценки").pack(side="left")
        ctk.CTkOptionMenu(
            mode_row,
            values=["Идеал", "Круто", "Хорошо"],
            variable=self.rating_mode_var,
            command=lambda _: self._sync_backend(),
        ).pack(side="right")

        precision_row = ctk.CTkFrame(frame, fg_color="transparent")
        precision_row.pack(fill="x", padx=14, pady=6)
        ctk.CTkLabel(precision_row, text="Точность").pack(side="left")
        ctk.CTkSlider(
            precision_row,
            from_=0.75,
            to=0.99,
            number_of_steps=24,
            variable=self.precision_threshold_var,
            command=lambda _v: self._sync_backend(),
        ).pack(side="right", fill="x", expand=True, padx=(10, 0))

        offset_row = ctk.CTkFrame(frame, fg_color="transparent")
        offset_row.pack(fill="x", padx=14, pady=(8, 8))

        ctk.CTkLabel(offset_row, text="Смещение X").grid(row=0, column=0, sticky="w")
        ctk.CTkEntry(offset_row, textvariable=self.offset_x_var, width=70).grid(row=0, column=1, padx=6)

        ctk.CTkLabel(offset_row, text="Смещение Y").grid(row=0, column=2, sticky="w", padx=(14, 0))
        ctk.CTkEntry(offset_row, textvariable=self.offset_y_var, width=70).grid(row=0, column=3, padx=6)

        ctk.CTkButton(offset_row, text="Применить", width=90, command=self._sync_backend).grid(row=0, column=4, padx=(8, 0))

        ctk.CTkLabel(
            frame,
            text="Нажми 'Калибровка' для красной рамки зоны стрелок.",
            text_color="#cdb6ff",
        ).pack(anchor="w", padx=14, pady=(4, 10))

    def _build_logs_tab(self, tab) -> None:
        tab.grid_rowconfigure(0, weight=1)
        tab.grid_columnconfigure(0, weight=1)
        self.log_box = ctk.CTkTextbox(tab, fg_color="#120a22", text_color="#e7d9ff")
        self.log_box.grid(row=0, column=0, sticky="nsew", padx=10, pady=10)
        self.log_box.insert("end", "[INIT] BottomBot DX logs\n")
        self.log_box.configure(state="disabled")

    def _sync_backend(self) -> None:
        self.backend.update_settings(
            auto_keys=bool(self.auto_keys_var.get()),
            auto_space=bool(self.auto_space_var.get()),
            rating_mode=self.rating_mode_var.get(),
            precision_threshold=float(self.precision_threshold_var.get()),
            zone_offset_x=int(self.offset_x_var.get()),
            zone_offset_y=int(self.offset_y_var.get()),
        )

    def append_log(self, message: str) -> None:
        timestamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]
        line = f"[{timestamp}] {message}\n"

        def write():
            self.log_box.configure(state="normal")
            self.log_box.insert("end", line)
            self.log_box.see("end")
            self.log_box.configure(state="disabled")

        self.root.after(0, write)

    def set_status(self, status_text: str) -> None:
        self.root.after(0, lambda: self.status_label.configure(text=status_text))

    def set_last_action(self, action_text: str) -> None:
        self.root.after(0, lambda: self.action_label.configure(text=f"Последнее действие: {action_text}"))

    def show_calibration(self) -> None:
        self._sync_backend()
        zone = self.backend.get_calibration_zone()
        if zone is None:
            self.append_log("Калибровка: окно не найдено")
            self.set_status("Статус: Окно не найдено")
            return
        left, top, width, height = zone
        self.overlay.show(left, top, width, height)
        self.append_log(f"Калибровка: рамка left={left}, top={top}, width={width}, height={height}")

    def toggle_bot(self) -> None:
        if self.backend.is_active:
            self.backend.stop()
            self.start_btn.configure(text="Запустить", fg_color="#5d2e8c")
        else:
            self._sync_backend()
            self.backend.start()
            self.start_btn.configure(text="Остановить", fg_color="#8b1e3f")

    def run(self) -> None:
        self.root.mainloop()


if __name__ == "__main__":
    AristocratUI().run()
