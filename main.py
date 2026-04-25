import threading
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable

import customtkinter as ctk
import cv2
import mss
import numpy as np
import pydirectinput

try:
    import pygetwindow as gw
except ImportError:
    gw = None

WINDOW_TITLE = "FreeStreet"
WINDOW_MIN_SIZE = (200, 200)
DEBUG_SCAN_PATH = Path("last_scan.png")

# Координаты относительно окна игры (1080p)
ARROW_ZONE_REL = {"top": 750, "left": 450, "width": 1100, "height": 120}
PERFECT_ZONE_REL = {"top": 750, "left": 995, "width": 30, "height": 120}

RATING_PRESETS = {
    "Идеал": {"perfect_brightness": 235},
    "Круто": {"perfect_brightness": 225},
    "Хорошо": {"perfect_brightness": 210},
}


@dataclass(frozen=True)
class CaptureZones:
    arrow_zone: dict
    perfect_zone: dict


class BotBackend:
    def __init__(self, logger: Callable[[str], None], status: Callable[[str], None], action: Callable[[str], None]):
        self.log = logger
        self.set_status = status
        self.set_action = action

        self.auto_keys_enabled = True
        self.auto_space_enabled = True
        self.template_threshold = 0.65
        self.perfect_brightness_threshold = 225
        self.scan_cooldown_sec = 0.02
        self.is_active = False

        self._worker: threading.Thread | None = None
        self._stop_event = threading.Event()

        self.templates = self._load_arrow_templates(Path("assets"))

        pydirectinput.FAILSAFE = False
        pydirectinput.PAUSE = 0

    @staticmethod
    def _load_arrow_templates(assets_dir: Path) -> dict[str, np.ndarray]:
        files = {
            "left": "left.png",
            "down": "down.png",
            "up": "up.png",
            "right": "right.png",
        }
        templates = {}
        for key, filename in files.items():
            full_path = assets_dir / filename
            template = cv2.imread(str(full_path), cv2.IMREAD_GRAYSCALE)
            if template is None:
                raise FileNotFoundError(f"Не найден шаблон: {full_path}")
            templates[key] = cv2.GaussianBlur(template, (3, 3), 0)
        return templates

    @staticmethod
    def _apply_window_offset(rel_zone: dict, window_left: int, window_top: int) -> dict:
        return {
            "top": int(window_top + rel_zone["top"]),
            "left": int(window_left + rel_zone["left"]),
            "width": int(rel_zone["width"]),
            "height": int(rel_zone["height"]),
        }

    def _resolve_game_window(self):
        if gw is None:
            self.set_status("Статус: pygetwindow не установлен")
            return None
        try:
            windows = gw.getWindowsWithTitle(WINDOW_TITLE)
        except Exception as exc:
            self.set_status("Статус: Ошибка поиска окна FreeStreet")
            self.log(f"Ошибка pygetwindow: {exc}")
            return None

        candidates = [
            w
            for w in windows
            if getattr(w, "width", 0) >= WINDOW_MIN_SIZE[0] and getattr(w, "height", 0) >= WINDOW_MIN_SIZE[1]
        ]
        return candidates[0] if candidates else None

    @staticmethod
    def _is_window_active(window) -> bool:
        attr = getattr(window, "isActive", None)
        if callable(attr):
            try:
                return bool(attr())
            except Exception:
                return False
        return bool(attr)

    def _get_capture_zones(self) -> CaptureZones | None:
        window = self._resolve_game_window()
        if window is None:
            self.set_status("Статус: Окно FreeStreet не найдено")
            self.set_action("Ожидание окна игры")
            return None

        if not self._is_window_active(window):
            self.set_status("Статус: Окно FreeStreet не активно")
            self.set_action("Активируйте окно игры")
            return None

        arrow_zone = self._apply_window_offset(ARROW_ZONE_REL, window.left, window.top)
        perfect_zone = self._apply_window_offset(PERFECT_ZONE_REL, window.left, window.top)
        return CaptureZones(arrow_zone=arrow_zone, perfect_zone=perfect_zone)

    def _detect_keys(self, gray_frame: np.ndarray) -> list[str]:
        blurred_frame = cv2.GaussianBlur(gray_frame, (3, 3), 0)
        detections = []
        for direction, template in self.templates.items():
            result = cv2.matchTemplate(blurred_frame, template, cv2.TM_CCOEFF_NORMED)
            ys, xs = np.where(result >= self.template_threshold)
            h, w = template.shape
            for y, x in zip(ys, xs):
                detections.append((direction, int(x + w / 2), int(y + h / 2)))

        detections.sort(key=lambda item: item[1])
        return [item[0] for item in detections]

    @staticmethod
    def _press_keys_instant(keys: list[str]) -> None:
        for key in keys:
            pydirectinput.press(key, _pause=False)

    def _wait_perfect_and_space(self, sct, zones: CaptureZones) -> bool:
        timeout_sec = 1.0
        start = time.perf_counter()
        while self.is_active and not self._stop_event.is_set() and (time.perf_counter() - start) <= timeout_sec:
            perfect_frame = np.array(sct.grab(zones.perfect_zone))
            bgr = cv2.cvtColor(perfect_frame, cv2.COLOR_BGRA2BGR)
            brightness = int(np.max(bgr))
            if brightness >= self.perfect_brightness_threshold:
                pydirectinput.press("space", _pause=False)
                self.set_action(f"SPACE ({brightness})")
                self.log(f"Perfect зона активна ({brightness}) -> SPACE")
                return True
            time.sleep(0.001)
        return False

    def update_settings(self, auto_keys: bool, auto_space: bool, rating_mode: str, precision_threshold: float) -> None:
        self.auto_keys_enabled = auto_keys
        self.auto_space_enabled = auto_space

        preset = RATING_PRESETS.get(rating_mode, RATING_PRESETS["Круто"])
        self.template_threshold = max(0.1, min(1.0, float(precision_threshold)))
        self.perfect_brightness_threshold = preset["perfect_brightness"]

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
            self._worker.join(timeout=1.5)
        self._worker = None
        self.set_status("Статус: Остановлен")
        self.set_action("Ожидание")
        self.log("Бот остановлен")

    def _worker_loop(self) -> None:
        with mss.mss() as sct:
            while self.is_active and not self._stop_event.is_set():
                zones = self._get_capture_zones()
                if zones is None:
                    time.sleep(0.3)
                    continue

                arrow_frame = np.array(sct.grab(zones.arrow_zone))
                gray = cv2.cvtColor(arrow_frame, cv2.COLOR_BGRA2GRAY)
                keys = self._detect_keys(gray)

                if not keys:
                    bgr_scan = cv2.cvtColor(arrow_frame, cv2.COLOR_BGRA2BGR)
                    cv2.imwrite(str(DEBUG_SCAN_PATH), bgr_scan)
                    self.set_action("Стрелки не найдены")
                    time.sleep(self.scan_cooldown_sec)
                    continue

                if self.auto_keys_enabled:
                    self._press_keys_instant(keys)
                    self.set_action(f"Комбо: {', '.join(k.upper() for k in keys)}")
                    self.log(f"Нажаты стрелки: {keys}")

                if self.auto_space_enabled:
                    if not self._wait_perfect_and_space(sct, zones):
                        self.set_action("Perfect окно не найдено")

                time.sleep(self.scan_cooldown_sec)


class AristocratUI:
    def __init__(self) -> None:
        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("dark-blue")

        self.root = ctk.CTk()
        self.root.title("BottomBot — Аристократ")
        self.root.geometry("600x400")
        self.root.resizable(True, True)
        self.root.minsize(600, 400)

        self._setup_styles()

        self.auto_keys_var = ctk.BooleanVar(value=True)
        self.auto_space_var = ctk.BooleanVar(value=True)
        self.rating_mode_var = ctk.StringVar(value="Круто")
        self.precision_threshold_var = ctk.DoubleVar(value=0.65)

        self.backend = BotBackend(self.append_log, self.set_status, self.set_last_action)

        self._build_layout()
        self._sync_backend()

    def _setup_styles(self) -> None:
        self.root.configure(fg_color="#11081f")

    def _build_layout(self) -> None:
        self.root.grid_columnconfigure(0, weight=1)
        self.root.grid_rowconfigure(1, weight=1)

        top = ctk.CTkFrame(self.root, fg_color="#1b0f2f", corner_radius=16)
        top.grid(row=0, column=0, sticky="ew", padx=14, pady=(14, 10))

        self.start_btn = ctk.CTkButton(
            top,
            text="Запустить",
            fg_color="#5d2e8c",
            hover_color="#7a3cb7",
            command=self.toggle_bot,
        )
        self.start_btn.pack(side="left", padx=12, pady=10)

        self.status_label = ctk.CTkLabel(top, text="Статус: Остановлен", text_color="#e8ddff")
        self.status_label.pack(side="left", padx=10)

        self.action_label = ctk.CTkLabel(top, text="Последнее действие: Ожидание", text_color="#ccb8ff")
        self.action_label.pack(side="left", padx=10)

        tabs = ctk.CTkTabview(
            self.root,
            segmented_button_fg_color="#2c1845",
            segmented_button_selected_color="#5d2e8c",
            segmented_button_selected_hover_color="#7a3cb7",
            fg_color="#1b0f2f",
        )
        tabs.grid(row=1, column=0, sticky="nsew", padx=14, pady=(0, 14))

        main_tab = tabs.add("Основное")
        ui_tab = tabs.add("Настройки UI")
        logs_tab = tabs.add("Логи")

        self._build_main_tab(main_tab)
        self._build_ui_tab(ui_tab)
        self._build_logs_tab(logs_tab)

    def _build_main_tab(self, tab) -> None:
        block = ctk.CTkFrame(tab, fg_color="#22133a")
        block.pack(fill="both", expand=True, padx=12, pady=12)

        self.auto_keys_switch = ctk.CTkSwitch(
            block,
            text="Авто-клавиши",
            variable=self.auto_keys_var,
            command=self._sync_backend,
        )
        self.auto_keys_switch.pack(anchor="w", padx=18, pady=(18, 10))

        self.auto_space_switch = ctk.CTkSwitch(
            block,
            text="Авто-пробел (Perfect)",
            variable=self.auto_space_var,
            command=self._sync_backend,
        )
        self.auto_space_switch.pack(anchor="w", padx=18, pady=10)

        row = ctk.CTkFrame(block, fg_color="transparent")
        row.pack(fill="x", padx=18, pady=(12, 6))

        ctk.CTkLabel(row, text="Режим оценки").pack(side="left")
        self.rating_menu = ctk.CTkOptionMenu(
            row,
            values=["Идеал", "Круто", "Хорошо"],
            variable=self.rating_mode_var,
            fg_color="#5d2e8c",
            button_color="#5d2e8c",
            button_hover_color="#7a3cb7",
            command=lambda _: self._sync_backend(),
        )
        self.rating_menu.pack(side="right")

    def _build_ui_tab(self, tab) -> None:
        frame = ctk.CTkFrame(tab, fg_color="#22133a")
        frame.pack(fill="both", expand=True, padx=12, pady=12)

        self.topmost_var = ctk.BooleanVar(value=False)
        self.autoscroll_var = ctk.BooleanVar(value=True)

        ctk.CTkSwitch(frame, text="Поверх всех окон", variable=self.topmost_var, command=self._toggle_topmost).pack(
            anchor="w", padx=18, pady=(18, 10)
        )
        ctk.CTkSwitch(frame, text="Автопрокрутка логов", variable=self.autoscroll_var).pack(
            anchor="w", padx=18, pady=10
        )

        precision_row = ctk.CTkFrame(frame, fg_color="transparent")
        precision_row.pack(fill="x", padx=18, pady=(10, 2))
        ctk.CTkLabel(precision_row, text="Точность (0.1 - 1.0)").pack(side="left")
        self.precision_value_label = ctk.CTkLabel(precision_row, text=f"{self.precision_threshold_var.get():.2f}")
        self.precision_value_label.pack(side="right")

        self.precision_slider = ctk.CTkSlider(
            frame,
            from_=0.1,
            to=1.0,
            number_of_steps=90,
            variable=self.precision_threshold_var,
            progress_color="#8e44d9",
            button_color="#c77dff",
            button_hover_color="#e0a3ff",
            command=self._on_precision_change,
        )
        self.precision_slider.pack(fill="x", padx=18, pady=(2, 16))

        ctk.CTkLabel(
            frame,
            text="Тема: Аристократ (тёмно-фиолетовая)\nМодульная структура: вкладки можно расширять новыми блоками.",
            justify="left",
            text_color="#cdb6ff",
        ).pack(anchor="w", padx=18, pady=(4, 10))

    def _build_logs_tab(self, tab) -> None:
        tab.grid_rowconfigure(0, weight=1)
        tab.grid_columnconfigure(0, weight=1)

        self.log_box = ctk.CTkTextbox(tab, fg_color="#120a22", text_color="#e7d9ff")
        self.log_box.grid(row=0, column=0, sticky="nsew", padx=12, pady=12)
        self.log_box.insert("end", "[INIT] Логи BottomBot\n")
        self.log_box.configure(state="disabled")

    def _toggle_topmost(self) -> None:
        self.root.attributes("-topmost", bool(self.topmost_var.get()))

    def _on_precision_change(self, value: float) -> None:
        self.precision_value_label.configure(text=f"{value:.2f}")
        self._sync_backend()

    def _sync_backend(self) -> None:
        self.backend.update_settings(
            auto_keys=bool(self.auto_keys_var.get()),
            auto_space=bool(self.auto_space_var.get()),
            rating_mode=self.rating_mode_var.get(),
            precision_threshold=float(self.precision_threshold_var.get()),
        )

    def append_log(self, message: str) -> None:
        timestamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]
        line = f"[{timestamp}] {message}\n"
        self.log_box.configure(state="normal")
        self.log_box.insert("end", line)
        if self.autoscroll_var.get():
            self.log_box.see("end")
        self.log_box.configure(state="disabled")

    def set_status(self, status_text: str) -> None:
        self.root.after(0, lambda: self.status_label.configure(text=status_text))

    def set_last_action(self, action_text: str) -> None:
        self.root.after(0, lambda: self.action_label.configure(text=f"Последнее действие: {action_text}"))

    def toggle_bot(self) -> None:
        if self.backend.is_active:
            self.backend.stop()
            self.start_btn.configure(text="Запустить", fg_color="#5d2e8c", hover_color="#7a3cb7")
        else:
            self._sync_backend()
            self.backend.start()
            self.start_btn.configure(text="Остановить", fg_color="#8b1e3f", hover_color="#b02a5a")

    def run(self) -> None:
        self.root.mainloop()


if __name__ == "__main__":
    AristocratUI().run()
