import json
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
from pynput.keyboard import Controller, Key

CONFIG_PATH = Path("overlay_position.json")
DEFAULT_REGION = {"x": 450, "y": 760, "width": 1100, "height": 120}
DEFAULT_PERFECT_REGION = {"x": 980, "y": 740, "width": 10, "height": 10}

ARROW_THRESHOLD = 0.7
PRESS_HOLD_SEC = 0.01
BETWEEN_ARROWS_SEC = 0.012
PERFECT_FLASH_THRESHOLD = 230
PERFECT_MONITOR_SIZE = 10

RATING_PRESETS = {
    "Идеал": {"perfect_brightness": 235},
    "Круто": {"perfect_brightness": 225},
    "Хорошо": {"perfect_brightness": 210},
}

KEYBOARD = Controller()
ARROW_KEYS = {
    "up": Key.up,
    "down": Key.down,
    "left": Key.left,
    "right": Key.right,
}


@dataclass(frozen=True)
class CaptureRegion:
    left: int
    top: int
    width: int
    height: int


def execute_sequence(arrows: list[str]) -> None:
    """Классическая SmartDancer-подача: мгновенный проход очереди стрелок."""
    for idx, arrow in enumerate(arrows):
        key = ARROW_KEYS.get(arrow)
        if key is None:
            continue
        KEYBOARD.press(key)
        time.sleep(PRESS_HOLD_SEC)
        KEYBOARD.release(key)
        if idx < len(arrows) - 1:
            time.sleep(BETWEEN_ARROWS_SEC)


class OverlayWindow:
    """Прозрачный always-on-top оверлей с рамкой зоны захвата."""

    def __init__(self, root: ctk.CTk) -> None:
        self.root = root
        self.color_default = "#1f78ff"
        self.color_success = "#4da3ff"
        self._flash_job = None

        self.window = ctk.CTkToplevel(root)
        self.window.overrideredirect(True)
        self.window.attributes("-topmost", True)
        self.window.configure(fg_color="grey")

        try:
            self.window.attributes("-transparentcolor", "grey")
        except Exception:
            pass

        try:
            self.window.attributes("-disabled", True)
        except Exception:
            pass

        self.canvas = ctk.CTkCanvas(self.window, bg="grey", bd=0, highlightthickness=0)
        self.canvas.pack(fill="both", expand=True)
        self.rect_id = self.canvas.create_rectangle(1, 1, 10, 10, outline=self.color_default, width=2)

    def update_region(self, region: CaptureRegion) -> None:
        width = max(20, int(region.width))
        height = max(20, int(region.height))
        left = int(region.left)
        top = int(region.top)

        self.window.geometry(f"{width}x{height}+{left}+{top}")
        self.canvas.configure(width=width, height=height)
        self.canvas.coords(self.rect_id, 1, 1, width - 2, height - 2)

    def set_color(self, color: str) -> None:
        self.canvas.itemconfigure(self.rect_id, outline=color)

    def get_screen_region(self) -> CaptureRegion:
        self.window.update_idletasks()
        return CaptureRegion(
            left=int(self.window.winfo_rootx()),
            top=int(self.window.winfo_rooty()),
            width=max(20, int(self.window.winfo_width())),
            height=max(20, int(self.window.winfo_height())),
        )

    def mark_failure(self) -> None:
        self.set_color(self.color_default)

    def flash_success(self, duration_ms: int = 500) -> None:
        self.set_color(self.color_success)
        if self._flash_job is not None:
            self.root.after_cancel(self._flash_job)
        self._flash_job = self.root.after(duration_ms, self.mark_failure)


class BotBackend:
    def __init__(
        self,
        logger: Callable[[str], None],
        status: Callable[[str], None],
        action: Callable[[str], None],
        vision_feedback: Callable[[bool], None],
    ):
        self.log = logger
        self.set_status = status
        self.set_action = action
        self.vision_feedback = vision_feedback

        self.auto_keys_enabled = True
        self.auto_space_enabled = True
        self.game_window_title = ""
        self.template_threshold = ARROW_THRESHOLD
        self.perfect_brightness_threshold = PERFECT_FLASH_THRESHOLD

        self.is_active = False

        self._region_lock = threading.Lock()
        self._region = CaptureRegion(
            left=DEFAULT_REGION["x"],
            top=DEFAULT_REGION["y"],
            width=DEFAULT_REGION["width"],
            height=DEFAULT_REGION["height"],
        )
        self._perfect_region = CaptureRegion(
            left=DEFAULT_PERFECT_REGION["x"],
            top=DEFAULT_PERFECT_REGION["y"],
            width=DEFAULT_PERFECT_REGION["width"],
            height=DEFAULT_PERFECT_REGION["height"],
        )

        self._worker: threading.Thread | None = None
        self._stop_event = threading.Event()

        self.templates = self._load_arrow_templates("assets")

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
            templates[key] = BotBackend._to_binary(template)
        return templates

    @staticmethod
    def _to_binary(gray_image: np.ndarray, threshold: int = 200) -> np.ndarray:
        _ret, binary = cv2.threshold(gray_image, threshold, 255, cv2.THRESH_BINARY)
        return binary

    def set_capture_region(self, left: int, top: int, width: int, height: int) -> None:
        with self._region_lock:
            self._region = CaptureRegion(
                left=int(left),
                top=int(top),
                width=max(20, int(width)),
                height=max(20, int(height)),
            )

    def set_perfect_region(self, left: int, top: int, width: int, height: int) -> None:
        with self._region_lock:
            self._perfect_region = CaptureRegion(
                left=int(left),
                top=int(top),
                width=max(5, int(width)),
                height=max(5, int(height)),
            )

    def get_capture_region(self) -> CaptureRegion:
        with self._region_lock:
            return self._region

    def get_perfect_region(self) -> CaptureRegion:
        with self._region_lock:
            return self._perfect_region

    def update_settings(
        self,
        auto_keys: bool,
        auto_space: bool,
        game_window_title: str,
        rating_mode: str,
        precision_threshold: float,
    ) -> None:
        self.auto_keys_enabled = auto_keys
        self.auto_space_enabled = auto_space
        self.game_window_title = game_window_title.strip()
        self.template_threshold = max(0.60, min(0.99, float(precision_threshold)))

        preset = RATING_PRESETS.get(rating_mode, RATING_PRESETS["Круто"])
        self.perfect_brightness_threshold = max(PERFECT_FLASH_THRESHOLD, preset["perfect_brightness"])

    @staticmethod
    def _group_matches(matches: list[tuple[int, int, int, int, str, float]]) -> list[tuple[int, int, int, int, str, float]]:
        groups: list[list[tuple[int, int, int, int, str, float]]] = []
        sorted_by_score = sorted(matches, key=lambda item: item[5], reverse=True)

        for candidate in sorted_by_score:
            x, y, w, h, _direction, _score = candidate
            cx = x + (w / 2)
            cy = y + (h / 2)
            merged = False

            for group in groups:
                gx, gy, gw, gh, _gdirection, _gscore = group[0]
                gcx = gx + (gw / 2)
                gcy = gy + (gh / 2)
                near_x = abs(cx - gcx) <= max(12, int(max(w, gw) * 0.65))
                near_y = abs(cy - gcy) <= max(10, int(max(h, gh) * 0.65))
                if near_x and near_y:
                    group.append(candidate)
                    merged = True
                    break

            if not merged:
                groups.append([candidate])

        unique = [max(group, key=lambda item: item[5]) for group in groups]
        return sorted(unique, key=lambda item: item[0])

    def _scan_arrows(self, gray_frame: np.ndarray) -> tuple[list[str], list[tuple[int, int, int, int, str, float]]]:
        binary = self._to_binary(gray_frame)
        raw_matches: list[tuple[int, int, int, int, str, float]] = []

        for direction, template in self.templates.items():
            h, w = template.shape[:2]
            result = cv2.matchTemplate(binary, template, cv2.TM_CCOEFF_NORMED)
            ys, xs = np.where(result >= self.template_threshold)
            for y, x in zip(ys, xs):
                score = float(result[y, x])
                raw_matches.append((int(x), int(y), int(w), int(h), direction, score))

        if not raw_matches:
            return [], []

        ordered = self._group_matches(raw_matches)
        arrows = [item[4] for item in ordered]
        return arrows, ordered

    def _wait_perfect_and_space(self, sct: mss.mss) -> bool:
        perfect = self.get_perfect_region()
        center_x = perfect.left + (perfect.width // 2)
        center_y = perfect.top + (perfect.height // 2)
        size = PERFECT_MONITOR_SIZE
        monitor = {
            "left": center_x - (size // 2),
            "top": center_y - (size // 2),
            "width": size,
            "height": size,
        }

        timeout_sec = 1.8
        started = time.perf_counter()
        last_debug_log = 0.0

        while self.is_active and not self._stop_event.is_set() and (time.perf_counter() - started) <= timeout_sec:
            frame = np.array(sct.grab(monitor))
            gray = cv2.cvtColor(frame, cv2.COLOR_BGRA2GRAY)
            brightness = int(np.mean(gray))
            now = time.perf_counter()
            if (now - last_debug_log) >= 0.08:
                self.log(f"[Debug] Текущая яркость Perfect: {brightness}")
                last_debug_log = now
            if brightness >= self.perfect_brightness_threshold:
                KEYBOARD.press(Key.space)
                KEYBOARD.release(Key.space)
                self.log(f"[SmartMode] SPACE в Perfect-зоне, яркость={brightness}")
                self.set_action(f"SPACE ({brightness})")
                return True
        return False

    def run_notepad_input_test(self) -> None:
        self.log("ТЕСТ В БЛОКНОТЕ: через 3 секунды отправлю 'UP DOWN LEFT RIGHT'...")

        def worker() -> None:
            time.sleep(3.0)
            KEYBOARD.type("UP DOWN LEFT RIGHT")
            self.log("ТЕСТ В БЛОКНОТЕ завершен.")

        threading.Thread(target=worker, daemon=True).start()

    @staticmethod
    def _save_debug_match(frame_bgr: np.ndarray, matches: list[tuple[int, int, int, int, str, float]]) -> None:
        debug = frame_bgr.copy()
        for x, y, w, h, direction, score in matches:
            cv2.rectangle(debug, (x, y), (x + w, y + h), (57, 255, 20), 2)
            cv2.putText(
                debug,
                f"{direction}:{score:.2f}",
                (x, max(12, y - 6)),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.45,
                (57, 255, 20),
                1,
                cv2.LINE_AA,
            )
        cv2.imwrite("debug_match.png", debug)

    def start(self) -> None:
        if self.is_active:
            return
        self.is_active = True
        self._stop_event.clear()
        self._worker = threading.Thread(target=self._worker_loop, daemon=True)
        self._worker.start()
        self.set_status("Статус: Запущен")
        self.set_action("Сканирование")
        self.log("[SmartMode] Очередь готова, жду стрелки...")

    def stop(self) -> None:
        if not self.is_active:
            return
        self.is_active = False
        self._stop_event.set()
        if self._worker and self._worker.is_alive():
            self._worker.join(timeout=1.0)
        self._worker = None
        self.set_status("Статус: Остановлен")
        self.set_action("Ожидание")
        self.log("Бот остановлен")

    def _worker_loop(self) -> None:
        with mss.mss() as sct:
            while self.is_active and not self._stop_event.is_set():
                region = self.get_capture_region()
                monitor = {
                    "left": region.left,
                    "top": region.top,
                    "width": region.width,
                    "height": region.height,
                }

                frame = np.array(sct.grab(monitor))
                bgr = cv2.cvtColor(frame, cv2.COLOR_BGRA2BGR)
                gray = cv2.cvtColor(frame, cv2.COLOR_BGRA2GRAY)

                arrows, matches = self._scan_arrows(gray)
                if not arrows:
                    self.vision_feedback(False)
                    time.sleep(0.01)
                    continue

                self.vision_feedback(True)
                self._save_debug_match(bgr, matches)

                if self.auto_keys_enabled:
                    execute_sequence(arrows)
                    self.log(f"[SmartMode] Нажимаю: {', '.join(a.upper() for a in arrows)}")
                    self.set_action("Комбо: " + ", ".join(k.upper() for k in arrows))

                if self.auto_space_enabled:
                    self.log("[SmartMode] Очередь введена, жду Perfect...")
                    self._wait_perfect_and_space(sct)

                time.sleep(0.02)


class AristocratUI:
    def __init__(self) -> None:
        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("dark-blue")

        self.root = ctk.CTk()
        self.root.title("BottomBot DX")
        self.root.geometry("540x520")
        self.root.minsize(500, 470)
        self.root.resizable(True, True)
        self.root.configure(fg_color="#11081f")
        self.root.attributes("-topmost", True)

        self.auto_keys_var = ctk.BooleanVar(value=True)
        self.auto_space_var = ctk.BooleanVar(value=True)
        self.game_window_title_var = ctk.StringVar(value="")
        self.rating_mode_var = ctk.StringVar(value="Круто")
        self.precision_threshold_var = ctk.DoubleVar(value=0.70)

        saved = self._load_region_config()
        self.region_x_var = ctk.IntVar(value=saved["x"])
        self.region_y_var = ctk.IntVar(value=saved["y"])
        self.region_w_var = ctk.IntVar(value=saved["width"])
        self.region_h_var = ctk.IntVar(value=saved["height"])

        self.perfect_x_var = ctk.IntVar(value=saved["perfect_x"])
        self.perfect_y_var = ctk.IntVar(value=saved["perfect_y"])
        self.perfect_w_var = ctk.IntVar(value=saved["perfect_width"])
        self.perfect_h_var = ctk.IntVar(value=saved["perfect_height"])

        self.overlay = OverlayWindow(self.root)
        self.backend = BotBackend(self.append_log, self.set_status, self.set_last_action, self.on_vision_feedback)

        self._build_layout()
        self._bind_region_traces()
        self.game_window_title_var.trace_add("write", lambda *_: self._sync_backend())
        self._sync_backend()
        self._apply_regions_to_backend()

    def _build_layout(self) -> None:
        self.root.grid_rowconfigure(2, weight=1)
        self.root.grid_columnconfigure(0, weight=1)

        top = ctk.CTkFrame(self.root, fg_color="#1b0f2f", corner_radius=12)
        top.grid(row=0, column=0, sticky="ew", padx=10, pady=(10, 8))

        self.start_btn = ctk.CTkButton(top, text="Запустить", command=self.toggle_bot, fg_color="#5d2e8c")
        self.start_btn.pack(side="left", padx=8, pady=8)

        save_btn = ctk.CTkButton(top, text="Сохранить позицию", command=self.save_region_config, fg_color="#136f48")
        save_btn.pack(side="left", padx=8, pady=8)

        self.notepad_test_btn = ctk.CTkButton(
            top,
            text="ТЕСТ В БЛОКНОТЕ",
            command=self.backend.run_notepad_input_test,
            fg_color="#2563eb",
        )
        self.notepad_test_btn.pack(side="left", padx=8, pady=8)

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

        game_row = ctk.CTkFrame(frame, fg_color="transparent")
        game_row.pack(fill="x", padx=14, pady=6)
        ctk.CTkLabel(game_row, text="Фильтр окна (опц.)").pack(side="left")
        ctk.CTkEntry(game_row, textvariable=self.game_window_title_var).pack(side="right", fill="x", expand=True, padx=(10, 0))

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
        ctk.CTkLabel(precision_row, text="Порог matchTemplate").pack(side="left")
        ctk.CTkSlider(
            precision_row,
            from_=0.60,
            to=0.95,
            number_of_steps=35,
            variable=self.precision_threshold_var,
            command=lambda _v: self._sync_backend(),
        ).pack(side="right", fill="x", expand=True, padx=(10, 0))

        ctk.CTkLabel(frame, text="Синяя рамка: зона стрелок", text_color="#cdb6ff").pack(anchor="w", padx=14, pady=(10, 6))
        sliders = ctk.CTkFrame(frame, fg_color="transparent")
        sliders.pack(fill="x", padx=14, pady=(0, 8))
        self._add_region_control(sliders, "X", self.region_x_var, 0, 0, 5000)
        self._add_region_control(sliders, "Y", self.region_y_var, 1, 0, 3000)
        self._add_region_control(sliders, "Ширина", self.region_w_var, 2, 20, 5000)
        self._add_region_control(sliders, "Высота", self.region_h_var, 3, 20, 3000)

        ctk.CTkLabel(frame, text="Perfect-зона (маленький квадрат)", text_color="#cdb6ff").pack(anchor="w", padx=14, pady=(10, 6))
        p_sliders = ctk.CTkFrame(frame, fg_color="transparent")
        p_sliders.pack(fill="x", padx=14, pady=(0, 8))
        self._add_region_control(p_sliders, "PX", self.perfect_x_var, 0, 0, 5000)
        self._add_region_control(p_sliders, "PY", self.perfect_y_var, 1, 0, 3000)
        self._add_region_control(p_sliders, "P-Шир", self.perfect_w_var, 2, 5, 300)
        self._add_region_control(p_sliders, "P-Выс", self.perfect_h_var, 3, 5, 300)

    def _add_region_control(
        self,
        parent,
        title: str,
        var: ctk.IntVar,
        row: int,
        min_value: int,
        max_value: int,
    ) -> None:
        line = ctk.CTkFrame(parent, fg_color="transparent")
        line.grid(row=row, column=0, sticky="ew", pady=4)
        line.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(line, text=title, width=70).grid(row=0, column=0, sticky="w")

        slider = ctk.CTkSlider(
            line,
            from_=min_value,
            to=max_value,
            number_of_steps=max_value - min_value,
            command=lambda value, v=var: v.set(int(value)),
        )
        slider.grid(row=0, column=1, sticky="ew", padx=(8, 8))
        slider.set(var.get())

        entry = ctk.CTkEntry(line, textvariable=var, width=80)
        entry.grid(row=0, column=2, sticky="e")

    def _build_logs_tab(self, tab) -> None:
        tab.grid_rowconfigure(0, weight=1)
        tab.grid_columnconfigure(0, weight=1)
        self.log_box = ctk.CTkTextbox(tab, fg_color="#120a22", text_color="#e7d9ff")
        self.log_box.grid(row=0, column=0, sticky="nsew", padx=10, pady=10)
        self.log_box.insert("end", "[INIT] BottomBot DX logs\n")
        self.log_box.configure(state="disabled")

    def _bind_region_traces(self) -> None:
        vars_to_bind = (
            self.region_x_var,
            self.region_y_var,
            self.region_w_var,
            self.region_h_var,
            self.perfect_x_var,
            self.perfect_y_var,
            self.perfect_w_var,
            self.perfect_h_var,
        )
        for var in vars_to_bind:
            var.trace_add("write", self._on_region_change)

    def _on_region_change(self, *_args) -> None:
        self._apply_regions_to_backend()

    def _apply_regions_to_backend(self) -> None:
        region = CaptureRegion(
            left=int(self.region_x_var.get()),
            top=int(self.region_y_var.get()),
            width=max(20, int(self.region_w_var.get())),
            height=max(20, int(self.region_h_var.get())),
        )
        self.overlay.update_region(region)
        screen_region = self.overlay.get_screen_region()
        self.backend.set_capture_region(screen_region.left, screen_region.top, screen_region.width, screen_region.height)

        self.backend.set_perfect_region(
            int(self.perfect_x_var.get()),
            int(self.perfect_y_var.get()),
            max(5, int(self.perfect_w_var.get())),
            max(5, int(self.perfect_h_var.get())),
        )

    def _sync_backend(self) -> None:
        self.backend.update_settings(
            auto_keys=bool(self.auto_keys_var.get()),
            auto_space=bool(self.auto_space_var.get()),
            game_window_title=self.game_window_title_var.get(),
            rating_mode=self.rating_mode_var.get(),
            precision_threshold=float(self.precision_threshold_var.get()),
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

    def on_vision_feedback(self, has_arrows: bool) -> None:
        if has_arrows:
            self.root.after(0, self.overlay.flash_success)
        else:
            self.root.after(0, self.overlay.mark_failure)

    def save_region_config(self) -> None:
        data = {
            "x": int(self.region_x_var.get()),
            "y": int(self.region_y_var.get()),
            "width": max(20, int(self.region_w_var.get())),
            "height": max(20, int(self.region_h_var.get())),
            "perfect_x": int(self.perfect_x_var.get()),
            "perfect_y": int(self.perfect_y_var.get()),
            "perfect_width": max(5, int(self.perfect_w_var.get())),
            "perfect_height": max(5, int(self.perfect_h_var.get())),
        }
        CONFIG_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        self.append_log(f"Позиция сохранена: {data}")

    def _load_region_config(self) -> dict:
        defaults = {
            "x": DEFAULT_REGION["x"],
            "y": DEFAULT_REGION["y"],
            "width": DEFAULT_REGION["width"],
            "height": DEFAULT_REGION["height"],
            "perfect_x": DEFAULT_PERFECT_REGION["x"],
            "perfect_y": DEFAULT_PERFECT_REGION["y"],
            "perfect_width": DEFAULT_PERFECT_REGION["width"],
            "perfect_height": DEFAULT_PERFECT_REGION["height"],
        }

        if not CONFIG_PATH.exists():
            return defaults
        try:
            raw = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            return {
                "x": int(raw.get("x", defaults["x"])),
                "y": int(raw.get("y", defaults["y"])),
                "width": max(20, int(raw.get("width", defaults["width"]))),
                "height": max(20, int(raw.get("height", defaults["height"]))),
                "perfect_x": int(raw.get("perfect_x", defaults["perfect_x"])),
                "perfect_y": int(raw.get("perfect_y", defaults["perfect_y"])),
                "perfect_width": max(5, int(raw.get("perfect_width", defaults["perfect_width"]))),
                "perfect_height": max(5, int(raw.get("perfect_height", defaults["perfect_height"]))),
            }
        except Exception:
            return defaults

    def toggle_bot(self) -> None:
        if self.backend.is_active:
            self.backend.stop()
            self.start_btn.configure(text="Запустить", fg_color="#5d2e8c")
        else:
            self._sync_backend()
            self._apply_regions_to_backend()
            self.backend.start()
            self.start_btn.configure(text="Остановить", fg_color="#8b1e3f")

    def run(self) -> None:
        self.root.mainloop()


if __name__ == "__main__":
    AristocratUI().run()
