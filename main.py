import threading
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

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
WINDOW_ARROW_ZONE_REL = {"top": 670, "left": 290, "width": 700, "height": 120}
WINDOW_PERFECT_ZONE_REL = {"top": 655, "left": 648, "width": 10, "height": 20}
FALLBACK_ARROW_ZONE = {"top": 760, "left": 450, "width": 700, "height": 120}
FALLBACK_PERFECT_ZONE = {"top": 745, "left": 808, "width": 10, "height": 20}


@dataclass(frozen=True)
class Zones:
    arrow_zone: dict
    perfect_zone: dict


class InstantSmartDancer:
    def __init__(self):
        self.window = ctk.CTk()
        self.window.title("Instant Smart Dancer (Window mode)")
        self.window.geometry("660x580")
        self.window.attributes("-topmost", True)

        self.zones = self.resolve_capture_zones()
        self.threshold = 0.85
        self.perfect_brightness_threshold = 230
        self.scan_cooldown_sec = 2.0

        self.auto_keys_var = ctk.BooleanVar(value=True)
        self.auto_space_var = ctk.BooleanVar(value=True)

        self.auto_keys_enabled = True
        self.auto_space_enabled = True
        self.is_active = False
        self.stop_event = threading.Event()
        self.worker_thread = None

        self.templates = self.load_arrow_templates(Path("assets"))

        pydirectinput.FAILSAFE = False
        pydirectinput.PAUSE = 0

        self._build_ui()

    def _build_ui(self):
        self.start_button = ctk.CTkButton(self.window, text="СТАРТ", command=self.toggle)
        self.start_button.pack(pady=(14, 8))

        self.status_label = ctk.CTkLabel(self.window, text="Статус: Выключен")
        self.status_label.pack(pady=(0, 6))

        self.last_action_label = ctk.CTkLabel(self.window, text="Последнее действие: Ожидание")
        self.last_action_label.pack(pady=(0, 10))

        self.auto_keys_switch = ctk.CTkSwitch(
            self.window,
            text="Auto-Keys",
            variable=self.auto_keys_var,
            command=self._sync_runtime_settings,
        )
        self.auto_keys_switch.pack(pady=(0, 6))

        self.auto_space_switch = ctk.CTkSwitch(
            self.window,
            text="Auto-Space",
            variable=self.auto_space_var,
            command=self._sync_runtime_settings,
        )
        self.auto_space_switch.pack(pady=(0, 10))

        zone_text = (
            f"arrow_zone: {self.zones.arrow_zone}\n"
            f"perfect_zone: {self.zones.perfect_zone}\n"
            f"threshold(gray): {self.threshold}\n"
            f"perfect_brightness: {self.perfect_brightness_threshold}\n"
            f"scan_cooldown: {self.scan_cooldown_sec}s"
        )
        self.zone_label = ctk.CTkLabel(self.window, text=zone_text)
        self.zone_label.pack(pady=(0, 10))

        self.log_box = ctk.CTkTextbox(self.window, width=620, height=340)
        self.log_box.pack(padx=14, pady=(0, 14), fill="both", expand=True)
        self.log_box.insert("end", "Instant Smart Dancer log\n")
        self.log_box.configure(state="disabled")

    @staticmethod
    def _apply_window_offset(zone_rel: dict, window_left: int, window_top: int):
        return {
            "top": int(window_top + zone_rel["top"]),
            "left": int(window_left + zone_rel["left"]),
            "width": int(zone_rel["width"]),
            "height": int(zone_rel["height"]),
        }

    def resolve_capture_zones(self):
        if gw is None:
            return Zones(arrow_zone=FALLBACK_ARROW_ZONE, perfect_zone=FALLBACK_PERFECT_ZONE)

        try:
            windows = gw.getWindowsWithTitle(WINDOW_TITLE)
        except Exception:
            windows = []

        visible_windows = [w for w in windows if getattr(w, "width", 0) > 0 and getattr(w, "height", 0) > 0]
        if not visible_windows:
            return Zones(arrow_zone=FALLBACK_ARROW_ZONE, perfect_zone=FALLBACK_PERFECT_ZONE)

        target = visible_windows[0]
        arrow_zone = self._apply_window_offset(WINDOW_ARROW_ZONE_REL, target.left, target.top)
        perfect_zone = self._apply_window_offset(WINDOW_PERFECT_ZONE_REL, target.left, target.top)
        return Zones(arrow_zone=arrow_zone, perfect_zone=perfect_zone)

    def _append_log(self, message):
        timestamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]
        line = f"[{timestamp}] {message}\n"
        self.log_box.configure(state="normal")
        self.log_box.insert("end", line)
        self.log_box.see("end")
        self.log_box.configure(state="disabled")

    def _set_last_action(self, message):
        self.last_action_label.configure(text=f"Последнее действие: {message}")

    def _sync_runtime_settings(self):
        self.auto_keys_enabled = bool(self.auto_keys_var.get())
        self.auto_space_enabled = bool(self.auto_space_var.get())

    @staticmethod
    def load_arrow_templates(assets_dir: Path):
        files = {
            "left": "left.png",
            "down": "down.png",
            "up": "up.png",
            "right": "right.png",
        }
        templates = {}
        for direction, filename in files.items():
            template_path = assets_dir / filename
            template = cv2.imread(str(template_path), cv2.IMREAD_GRAYSCALE)
            if template is None:
                raise FileNotFoundError(f"Не найден шаблон стрелки: {template_path}")
            templates[direction] = template
        return templates

    def detect_keys(self, gray_frame):
        detections = []
        for direction, template in self.templates.items():
            result = cv2.matchTemplate(gray_frame, template, cv2.TM_CCOEFF_NORMED)
            ys, xs = np.where(result >= self.threshold)
            h, w = template.shape
            for y, x in zip(ys, xs):
                detections.append(
                    {
                        "direction": direction,
                        "center_x": int(x + w / 2),
                        "center_y": int(y + h / 2),
                    }
                )

        detections.sort(key=lambda item: item["center_x"])
        return detections

    @staticmethod
    def press_keys_instant(found_keys):
        if found_keys:
            pydirectinput.typewrite(found_keys, interval=0)

    def wait_for_perfect_and_space(self, sct):
        timeout_sec = 2.0
        start = time.perf_counter()

        while (
            self.is_active
            and not self.stop_event.is_set()
            and self.auto_space_enabled
            and (time.perf_counter() - start) <= timeout_sec
        ):
            perfect_frame = np.array(sct.grab(self.zones.perfect_zone))
            perfect_bgr = cv2.cvtColor(perfect_frame, cv2.COLOR_BGRA2BGR)
            brightness = int(np.max(perfect_bgr))

            if brightness > self.perfect_brightness_threshold:
                pydirectinput.press("space", _pause=False)
                self.window.after(0, self._set_last_action, f"Perfect > {self.perfect_brightness_threshold} -> SPACE")
                self.window.after(0, self._append_log, f"Perfect detected ({brightness}) -> SPACE")
                return

            time.sleep(0.001)

        self.window.after(0, self._set_last_action, "Perfect не найден за 2с")

    def worker_loop(self):
        with mss.mss() as sct:
            while self.is_active and not self.stop_event.is_set():
                arrow_frame = np.array(sct.grab(self.zones.arrow_zone))
                gray_frame = cv2.cvtColor(arrow_frame, cv2.COLOR_BGRA2GRAY)
                combo = self.detect_keys(gray_frame)

                if not combo:
                    self.window.after(0, self._set_last_action, "Стрелки не найдены")
                    time.sleep(0.01)
                    continue

                found_keys = [item["direction"] for item in combo]
                combo_names = [key.upper() for key in found_keys]

                self.window.after(0, self._set_last_action, f"Найдены стрелки: {combo_names}")

                if self.auto_keys_enabled:
                    self.press_keys_instant(found_keys)
                    self.window.after(0, self._set_last_action, f"Нажата цепочка: {combo_names}")
                    self.window.after(0, self._append_log, f"Цепочка нажата одним вызовом: {combo_names}")

                if self.auto_space_enabled:
                    self.window.after(0, self._set_last_action, "Мониторинг Perfect (до 2с)")
                    self.wait_for_perfect_and_space(sct)

                self.window.after(0, self._append_log, f"Cooldown {self.scan_cooldown_sec:.1f}s перед новым сканированием")
                time.sleep(self.scan_cooldown_sec)

    def toggle(self):
        if self.is_active:
            self.stop()
            return
        self.start()

    def start(self):
        self._sync_runtime_settings()
        self.zones = self.resolve_capture_zones()
        self.is_active = True
        self.stop_event.clear()
        self.worker_thread = threading.Thread(target=self.worker_loop, daemon=True)
        self.worker_thread.start()

        self.start_button.configure(text="СТОП", fg_color="red")
        self.status_label.configure(text="Статус: Работает")
        self._set_last_action("Бот запущен")
        self._append_log(f"Бот запущен (Window режим, threshold={self.threshold}, cooldown={self.scan_cooldown_sec}s)")

    def stop(self):
        self.is_active = False
        self.stop_event.set()
        if self.worker_thread and self.worker_thread.is_alive():
            self.worker_thread.join(timeout=1.5)
        self.worker_thread = None

        self.start_button.configure(text="СТАРТ", fg_color=["#3a7ebf", "#1f538d"])
        self.status_label.configure(text="Статус: Выключен")
        self._set_last_action("Бот остановлен")
        self._append_log("Бот остановлен")


if __name__ == "__main__":
    app = InstantSmartDancer()
    app.window.mainloop()
