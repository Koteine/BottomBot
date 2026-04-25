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


@dataclass(frozen=True)
class Zones:
    arrow_zone: dict
    perfect_zone: dict


class InstantSmartDancer:
    def __init__(self):
        self.window = ctk.CTk()
        self.window.title("Instant Smart Dancer (1600x900)")
        self.window.geometry("620x520")
        self.window.attributes("-topmost", True)

        self.zones = Zones(
            arrow_zone={"top": 740, "left": 400, "width": 800, "height": 100},
            perfect_zone={"top": 728, "left": 810, "width": 15, "height": 25},
        )

        self.threshold = 0.9
        self.dedupe_radius_px = 10

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
        self.status_label.pack(pady=(0, 10))

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
            f"threshold(gray): {self.threshold}"
        )
        self.zone_label = ctk.CTkLabel(self.window, text=zone_text)
        self.zone_label.pack(pady=(0, 10))

        self.log_box = ctk.CTkTextbox(self.window, width=580, height=330)
        self.log_box.pack(padx=14, pady=(0, 14), fill="both", expand=True)
        self.log_box.insert("end", "Instant Smart Dancer log\n")
        self.log_box.configure(state="disabled")

    def _append_log(self, message):
        timestamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]
        line = f"[{timestamp}] {message}\n"
        self.log_box.configure(state="normal")
        self.log_box.insert("end", line)
        self.log_box.see("end")
        self.log_box.configure(state="disabled")

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

    def detect_combo(self, gray_frame):
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
                        "score": float(result[y, x]),
                    }
                )

        if not detections:
            return []

        detections.sort(key=lambda item: item["score"], reverse=True)
        deduped = []
        for item in detections:
            duplicate = False
            for kept in deduped:
                if (
                    abs(item["center_x"] - kept["center_x"]) <= self.dedupe_radius_px
                    and abs(item["center_y"] - kept["center_y"]) <= self.dedupe_radius_px
                ):
                    duplicate = True
                    break
            if not duplicate:
                deduped.append(item)

        deduped.sort(key=lambda item: item["center_x"])
        return deduped

    @staticmethod
    def press_combo_instant(combo):
        start = time.perf_counter()
        for item in combo:
            pydirectinput.press(item["direction"])
            time.sleep(0.001)
        return time.perf_counter() - start

    def wait_for_perfect_and_space(self, sct):
        while self.is_active and not self.stop_event.is_set() and self.auto_space_enabled:
            perfect_frame = np.array(sct.grab(self.zones.perfect_zone))
            perfect_bgr = cv2.cvtColor(perfect_frame, cv2.COLOR_BGRA2BGR)
            max_val = int(np.max(perfect_bgr))
            if max_val > 245:
                pydirectinput.press("space")
                self.window.after(0, self._append_log, "Perfect detected (max>245) -> SPACE")
                break
            time.sleep(0.0005)

    def worker_loop(self):
        last_signature = None
        with mss.mss() as sct:
            while self.is_active and not self.stop_event.is_set():
                arrow_frame = np.array(sct.grab(self.zones.arrow_zone))
                gray_frame = cv2.cvtColor(arrow_frame, cv2.COLOR_BGRA2GRAY)
                combo = self.detect_combo(gray_frame)

                if not combo:
                    time.sleep(0.0008)
                    continue

                signature = tuple((item["direction"], item["center_x"]) for item in combo)
                if signature == last_signature:
                    time.sleep(0.0008)
                    continue
                last_signature = signature

                combo_names = [item["direction"].upper() for item in combo]
                input_time = 0.0

                if self.auto_keys_enabled:
                    input_time = self.press_combo_instant(combo)

                self.window.after(
                    0,
                    self._append_log,
                    f"Комбинация {combo_names} введена за {input_time:.2f} сек. Ожидаю Perfect...",
                )

                if self.auto_space_enabled:
                    self.wait_for_perfect_and_space(sct)

                time.sleep(0.0008)

    def toggle(self):
        if self.is_active:
            self.stop()
            return
        self.start()

    def start(self):
        self._sync_runtime_settings()
        self.is_active = True
        self.stop_event.clear()
        self.worker_thread = threading.Thread(target=self.worker_loop, daemon=True)
        self.worker_thread.start()

        self.start_button.configure(text="СТОП", fg_color="red")
        self.status_label.configure(text="Статус: Работает")
        self._append_log("Бот запущен (Low Latency режим)")

    def stop(self):
        self.is_active = False
        self.stop_event.set()
        if self.worker_thread and self.worker_thread.is_alive():
            self.worker_thread.join(timeout=1.5)
        self.worker_thread = None

        self.start_button.configure(text="СТАРТ", fg_color=["#3a7ebf", "#1f538d"])
        self.status_label.configure(text="Статус: Выключен")
        self._append_log("Бот остановлен")


if __name__ == "__main__":
    app = InstantSmartDancer()
    app.window.mainloop()
