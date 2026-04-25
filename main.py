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
        self.window.geometry("560x560")
        self.window.attributes("-topmost", True)

        self.is_active = False
        self.last_combo_signature = None

        # Зоны захвата (ориентированы под 1600x900)
        self.arrow_zone = {"top": 650, "left": 400, "width": 800, "height": 250}
        self.perfect_zone = {"top": 540, "left": 760, "width": 40, "height": 170}

        # Логика распознавания и таймингов
        self.match_threshold = 0.8
        self.perfect_brightness_threshold = 230
        self.space_delay_ms = 20
        self.space_cooldown_sec = 1.0
        self.wait_for_flash_timeout = 1.25

        # Переключатели режима
        self.auto_arrows_var = ctk.BooleanVar(value=True)
        self.auto_space_var = ctk.BooleanVar(value=True)

        self.awaiting_perfect_flash = False
        self.awaiting_since = 0.0
        self.last_space_press_at = 0.0

        self.arrow_templates = self._load_arrow_templates()
        pyautogui.PAUSE = 0
        pyautogui.FAILSAFE = False

        self._build_ui()

    def _build_ui(self):
        self.start_button = ctk.CTkButton(self.window, text="СТАРТ", command=self.toggle)
        self.start_button.pack(pady=(14, 8))

        self.status_label = ctk.CTkLabel(self.window, text="Статус: Выключен")
        self.status_label.pack(pady=(0, 10))

        self.auto_arrows_switch = ctk.CTkSwitch(
            self.window,
            text="Авто-стрелки",
            variable=self.auto_arrows_var,
            onvalue=True,
            offvalue=False,
        )
        self.auto_arrows_switch.pack(pady=(0, 6))

        self.auto_space_switch = ctk.CTkSwitch(
            self.window,
            text="Авто-пробел",
            variable=self.auto_space_var,
            onvalue=True,
            offvalue=False,
        )
        self.auto_space_switch.pack(pady=(0, 10))

        delay_frame = ctk.CTkFrame(self.window)
        delay_frame.pack(padx=14, pady=(0, 10), fill="x")

        ctk.CTkLabel(delay_frame, text="Задержка пробела (ms)").pack(anchor="w", padx=10, pady=(8, 2))

        self.delay_slider = ctk.CTkSlider(
            delay_frame,
            from_=0,
            to=250,
            number_of_steps=250,
            command=self._on_delay_slider,
        )
        self.delay_slider.pack(fill="x", padx=10, pady=(0, 8))
        self.delay_slider.set(self.space_delay_ms)

        self.delay_entry = ctk.CTkEntry(delay_frame)
        self.delay_entry.pack(fill="x", padx=10, pady=(0, 10))
        self.delay_entry.insert(0, str(self.space_delay_ms))
        self.delay_entry.bind("<Return>", self._on_delay_entry)
        self.delay_entry.bind("<FocusOut>", self._on_delay_entry)

        arrow_text = (
            f"arrow_zone: top={self.arrow_zone['top']}, left={self.arrow_zone['left']}, "
            f"w={self.arrow_zone['width']}, h={self.arrow_zone['height']}"
        )
        perfect_text = (
            f"perfect_zone: top={self.perfect_zone['top']}, left={self.perfect_zone['left']}, "
            f"w={self.perfect_zone['width']}, h={self.perfect_zone['height']}"
        )

        self.zone_label = ctk.CTkLabel(self.window, text=f"{arrow_text}\n{perfect_text}")
        self.zone_label.pack(pady=(0, 10))

        self.log_box = ctk.CTkTextbox(self.window, width=525, height=255)
        self.log_box.pack(padx=14, pady=(0, 14), fill="both", expand=True)
        self.log_box.insert("end", "Лог Smart Dancer\n")
        self.log_box.configure(state="disabled")

    def _on_delay_slider(self, value):
        ms = int(round(float(value)))
        self.space_delay_ms = ms
        self.delay_entry.delete(0, "end")
        self.delay_entry.insert(0, str(ms))

    def _on_delay_entry(self, _event=None):
        raw = self.delay_entry.get().strip()
        if not raw:
            raw = "0"

        try:
            ms = int(raw)
        except ValueError:
            ms = self.space_delay_ms

        ms = max(0, min(250, ms))
        self.space_delay_ms = ms
        self.delay_slider.set(ms)

        self.delay_entry.delete(0, "end")
        self.delay_entry.insert(0, str(ms))

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
        self.awaiting_perfect_flash = False
        self.awaiting_since = 0.0
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

    def _enter_wait_for_flash(self):
        self.awaiting_perfect_flash = True
        self.awaiting_since = time.perf_counter()
        self._append_log("Режим: ожидание вспышки Perfect")

    def _maybe_press_space_on_flash(self, perfect_gray):
        if not self.awaiting_perfect_flash:
            return

        now = time.perf_counter()
        if (now - self.awaiting_since) > self.wait_for_flash_timeout:
            self.awaiting_perfect_flash = False
            self._append_log("Таймаут ожидания Perfect")
            return

        max_intensity = int(np.max(perfect_gray))
        if max_intensity < self.perfect_brightness_threshold:
            return

        if (now - self.last_space_press_at) < self.space_cooldown_sec:
            self.awaiting_perfect_flash = False
            self._append_log("Perfect пойман, но Space в кулдауне")
            return

        delay_sec = self.space_delay_ms / 1000.0
        if delay_sec > 0:
            time.sleep(delay_sec)

        pyautogui.keyDown("space")
        time.sleep(0.045)
        pyautogui.keyUp("space")

        self.last_space_press_at = time.perf_counter()
        self.awaiting_perfect_flash = False
        self._append_log(
            f"Space нажат по вспышке (max={max_intensity}, порог={self.perfect_brightness_threshold}, delay={self.space_delay_ms}ms)"
        )

    def run_logic(self):
        with mss.mss() as sct:
            while self.is_active:
                if self.awaiting_perfect_flash:
                    perfect_frame = np.array(sct.grab(self.perfect_zone))
                    perfect_gray = cv2.cvtColor(perfect_frame, cv2.COLOR_BGRA2GRAY)
                    self._maybe_press_space_on_flash(perfect_gray)
                    time.sleep(0.001)
                    continue

                arrows_frame = np.array(sct.grab(self.arrow_zone))
                arrows_gray = cv2.cvtColor(arrows_frame, cv2.COLOR_BGRA2GRAY)

                combo = self.find_combo(arrows_gray)
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

                if self.auto_arrows_var.get():
                    self.press_combo(combo)

                if self.auto_space_var.get():
                    self._enter_wait_for_flash()
                else:
                    self.awaiting_perfect_flash = False

                time.sleep(0.004)


if __name__ == "__main__":
    dancer = SmartDancer()
    dancer.window.mainloop()
