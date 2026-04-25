import threading
import time
import tkinter as tk
from datetime import datetime

import cv2
import keyboard
import mss
import numpy as np

SCAN_AREA = {"top": 690, "left": 450, "width": 742, "height": 65}
PERFECT_ZONE = {"top": 715, "left": 810, "width": 20, "height": 20}
THRESHOLD = 0.7
PERFECT_BRIGHTNESS = 230


def load_templates() -> dict[str, np.ndarray]:
    files = {"left": "assets/left.png", "down": "assets/down.png", "up": "assets/up.png", "right": "assets/right.png"}
    out: dict[str, np.ndarray] = {}
    for name, path in files.items():
        img = cv2.imread(path, cv2.IMREAD_GRAYSCALE)
        if img is None:
            raise FileNotFoundError(path)
        out[name] = img
    return out


def detect_arrows(gray: np.ndarray, templates: dict[str, np.ndarray]) -> list[str]:
    hits = []
    for direction, template in templates.items():
        result = cv2.matchTemplate(gray, template, cv2.TM_CCOEFF_NORMED)
        ys, xs = np.where(result >= THRESHOLD)
        for y, x in zip(ys, xs):
            hits.append((int(x), direction, float(result[y, x])))
    uniq = {}
    for x, direction, score in sorted(hits, key=lambda i: i[2], reverse=True):
        bucket = x // 8
        if bucket not in uniq:
            uniq[bucket] = (x, direction)
    return [d for x, d in sorted(uniq.values(), key=lambda i: i[0])]


class Bot:
    def __init__(self, log):
        self.log = log
        self.run_flag = False
        self.templates = load_templates()

    def start(self):
        if self.run_flag:
            return
        self.run_flag = True
        threading.Thread(target=self.loop, daemon=True).start()

    def stop(self):
        self.run_flag = False

    def loop(self):
        with mss.mss() as sct:
            while self.run_flag:
                gray = cv2.cvtColor(np.array(sct.grab(SCAN_AREA)), cv2.COLOR_BGRA2GRAY)
                chain = detect_arrows(gray, self.templates)
                if not chain:
                    continue
                for key in chain:
                    keyboard.press_and_release(key)
                self.log("Стрелки: " + " ".join(chain))
                while self.run_flag:
                    if np.mean(cv2.cvtColor(np.array(sct.grab(PERFECT_ZONE)), cv2.COLOR_BGRA2GRAY)) > PERFECT_BRIGHTNESS:
                        keyboard.press_and_release("space")
                        self.log("SPACE")
                        break


class UI:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("BottomBot")
        self.root.geometry("420x260")
        self.bot = Bot(self.log)

        self.btn = tk.Button(self.root, text="СТАРТ", command=self.toggle, font=("Arial", 12, "bold"))
        self.btn.pack(fill="x", padx=8, pady=8)

        self.box = tk.Text(self.root, height=12)
        self.box.pack(fill="both", expand=True, padx=8, pady=(0, 8))

    def log(self, text: str):
        line = f"[{datetime.now().strftime('%H:%M:%S.%f')[:-3]}] {text}\n"
        self.root.after(0, lambda: (self.box.insert("end", line), self.box.see("end")))

    def toggle(self):
        if self.bot.run_flag:
            self.bot.stop()
            self.btn.config(text="СТАРТ")
            self.log("Остановлено")
        else:
            self.bot.start()
            self.btn.config(text="СТОП")
            self.log("Запущено")


if __name__ == "__main__":
    UI().root.mainloop()
