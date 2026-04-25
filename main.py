import random
import threading
import time
from datetime import datetime
from pathlib import Path

import customtkinter as ctk
import cv2
import mss
import numpy as np
import pyautogui


class SmartDancer:
    def __init__(self):
        self.window = ctk.CTk()
        self.window.title("Smart Dancer 1600x900")
        self.window.geometry("520x420")
        self.window.attributes("-topmost", True)

        self.is_active = False
        self.last_combo_signature = None

        self.capture_zone = {"top": 650, "left": 400, "width": 800, "height": 250}
        self.match_threshold = 0.8
        self.perfect_brightness_threshold = 235
        self.space_offset = 0.02
        self.perfect_zone_left = 770
        self.perfect_zone_width = 130

        self.arrow_templates = self._load_arrow_templates()
        pyautogui.PAUSE = 0
        pyautogui.FAILSAFE = False

        self._build_ui()

    def _build_ui(self):
        self.start_button = ctk.CTkButton(self.window, text="СТАРТ", command=self.toggle)
        self.start_button.pack(pady=(16, 8))

        self.status_label = ctk.CTkLabel(self.window, text="Статус: Выключен")
        self.status_label.pack(pady=(0, 8))

        zone_text = (
            f"Зона поиска: top={self.capture_zone['top']}, left={self.capture_zone['left']}, "
            f"width={self.capture_zone['width']}, height={self.capture_zone['height']}"
        )
        self.zone_label = ctk.CTkLabel(self.window, text=zone_text)
        self.zone_label.pack(pady=(0, 10))

        self.log_box = ctk.CTkTextbox(self.window, width=490, height=270)
        self.log_box.pack(padx=14, pady=(0, 14))
        self.log_box.insert("end", "Лог Smart Dancer\n")
        self.log_box.configure(state="disabled")

    def _load_arrow_templates(self):
        assets_dir = Path("assets")
        files = {
            "up": "up.png",
            "down": "down.png",
            "left": "left.png",
            "right": "right.png",
        }

        templates = {}
        for key, file_name in files.items():
            template_path = assets_dir / file_name
            template = cv2.imread(str(template_path), cv2.IMREAD_GRAYSCALE)
            if template is None:
                raise FileNotFoundError(f"Не найден шаблон стрелки: {template_path}")
            templates[key] = template
        return templates

    def _append_log(self, message):
        timestamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]
        line = f"[{timestamp}] {message}\n"

        def update_ui():
            self.log_box.configure(state="normal")
            self.log_box.insert("end", line)
            self.log_box.see("end")
            self.log_box.configure(state="disabled")

        self.window.after(0, update_ui)

    def toggle(self):
        if self.is_active:
            self.is_active = False
            self.start_button.configure(text="СТАРТ", fg_color=["#3a7ebf", "#1f538d"])
            self.status_label.configure(text="Статус: Выключен")
            self._append_log("Бот остановлен")
            return

        self.is_active = True
        self.last_combo_signature = None
        self.start_button.configure(text="СТОП", fg_color="red")
        self.status_label.configure(text="Статус: Работает")
        self._append_log("Бот запущен")
        threading.Thread(target=self.run_logic, daemon=True).start()

    def find_combo(self, gray_frame):
        detections = []

        for direction, template in self.arrow_templates.items():
            result = cv2.matchTemplate(gray_frame, template, cv2.TM_CCOEFF_NORMED)
            locations = np.where(result >= self.match_threshold)
            template_h, template_w = template.shape

            for y, x in zip(*locations):
                score = float(result[y, x])
                detections.append(
                    {
                        "direction": direction,
                        "x": int(x),
                        "y": int(y),
                        "center_x": int(x + template_w / 2),
                        "center_y": int(y + template_h / 2),
                        "w": template_w,
                        "h": template_h,
                        "score": score,
                    }
                )

        if not detections:
            return []

        detections.sort(key=lambda item: item["score"], reverse=True)
        filtered = []
        for det in detections:
            is_duplicate = False
            for kept in filtered:
                if abs(det["center_x"] - kept["center_x"]) <= max(det["w"], kept["w"]) * 0.55 and abs(
                    det["center_y"] - kept["center_y"]
                ) <= max(det["h"], kept["h"]) * 0.55:
                    is_duplicate = True
                    break
            if not is_duplicate:
                filtered.append(det)

        filtered.sort(key=lambda item: item["center_x"])
        return filtered

    def press_combo(self, combo):
        for item in combo:
            pyautogui.press(item["direction"])
            time.sleep(random.uniform(0.02, 0.05))

    def wait_and_press_space(self, color_frame, timeout=1.25):
        height = color_frame.shape[0]
        width = color_frame.shape[1]

        zone_top = max(0, int(height * 0.22))
        zone_bottom = max(zone_top + 1, int(height * 0.38))
        zone_left = self.perfect_zone_left - self.capture_zone["left"]
        zone_left = max(0, min(width - self.perfect_zone_width, zone_left))
        zone_right = zone_left + self.perfect_zone_width

        monitor = {
            "top": self.capture_zone["top"] + zone_top,
            "left": self.capture_zone["left"] + zone_left,
            "width": zone_right - zone_left,
            "height": zone_bottom - zone_top,
        }

        start = time.perf_counter()
        threshold_reached = False
        prev_max_intensity = None
        with mss.mss() as sct:
            while self.is_active and (time.perf_counter() - start) < timeout:
                perfect_region = np.array(sct.grab(monitor))
                gray = cv2.cvtColor(perfect_region, cv2.COLOR_BGRA2GRAY)
                avg_intensity = float(np.mean(gray))
                max_intensity = int(np.max(gray))

                if max_intensity >= self.perfect_brightness_threshold:
                    threshold_reached = True

                if threshold_reached and prev_max_intensity is not None and max_intensity < prev_max_intensity:
                    time.sleep(self.space_offset)
                    pyautogui.keyDown("space")
                    time.sleep(0.05)
                    pyautogui.keyUp("space")
                    self._append_log(
                        f"Space нажат по пику (max={max_intensity}, prev={prev_max_intensity}, avg={avg_intensity:.1f}, порог={self.perfect_brightness_threshold}, offset={self.space_offset:.3f})"
                    )
                    return True

                prev_max_intensity = max_intensity

                time.sleep(0.001)

        return False

    def run_logic(self):
        with mss.mss() as sct:
            while self.is_active:
                frame = np.array(sct.grab(self.capture_zone))
                gray = cv2.cvtColor(frame, cv2.COLOR_BGRA2GRAY)

                combo = self.find_combo(gray)
                if not combo:
                    time.sleep(0.002)
                    continue

                signature = tuple((item["direction"], item["center_x"]) for item in combo)
                if signature == self.last_combo_signature:
                    time.sleep(0.002)
                    continue

                self.last_combo_signature = signature
                combo_text = " ".join(item["direction"] for item in combo)
                self._append_log(f"Комбинация: {combo_text}")

                self.press_combo(combo)
                self.wait_and_press_space(frame)

                time.sleep(0.005)


if __name__ == "__main__":
    dancer = SmartDancer()
    dancer.window.mainloop()
