import ctypes
import os
import sys
import threading
import time
from datetime import datetime

import customtkinter as ctk
import cv2
import keyboard
import mss
import numpy as np
import psutil

# Hardcoded scan area
SCAN_X = 450
SCAN_Y = 690
SCAN_W = 742
SCAN_H = 65

# Hardcoded perfect area
PERF_X = 810
PERF_Y = 715
PERF_W = 20
PERF_H = 20

BRIGHTNESS_SPACE_THRESHOLD = 235
ARROW_THRESHOLD = 0.70
WHITE_PIXEL_THRESHOLD = 245
HOLD_ARROW_SEC = 0.04
BETWEEN_ARROWS_SEC = 0.01
POST_SPACE_COOLDOWN_SEC = 1.5

SCAN_MONITOR = {"left": SCAN_X, "top": SCAN_Y, "width": SCAN_W, "height": SCAN_H}
PERF_MONITOR = {"left": PERF_X, "top": PERF_Y, "width": PERF_W, "height": PERF_H}

KEY_MAP = {
    "left": "left",
    "down": "down",
    "up": "up",
    "right": "right",
}


def ensure_admin() -> None:
    if os.name != "nt":
        return
    try:
        if not ctypes.windll.shell32.IsUserAnAdmin():
            sys.exit()
    except Exception:
        sys.exit()


def set_high_priority() -> None:
    try:
        process = psutil.Process(os.getpid())
        if os.name == "nt":
            process.nice(psutil.HIGH_PRIORITY_CLASS)
        else:
            process.nice(-10)
    except Exception:
        pass


def load_arrow_templates(assets_dir: str) -> dict[str, np.ndarray]:
    files = {
        "left": "left.png",
        "down": "down.png",
        "up": "up.png",
        "right": "right.png",
    }
    templates: dict[str, np.ndarray] = {}
    for direction, filename in files.items():
        template = cv2.imread(f"{assets_dir}/{filename}", cv2.IMREAD_GRAYSCALE)
        if template is None:
            raise FileNotFoundError(f"Не найден шаблон: {assets_dir}/{filename}")
        _, binary = cv2.threshold(template, 200, 255, cv2.THRESH_BINARY)
        templates[direction] = binary
    return templates


def group_matches(matches: list[tuple[int, int, int, int, str, float]]) -> list[tuple[int, int, int, int, str, float]]:
    groups: list[list[tuple[int, int, int, int, str, float]]] = []
    for candidate in sorted(matches, key=lambda item: item[5], reverse=True):
        x, y, w, h, _direction, _score = candidate
        cx = x + (w / 2)
        cy = y + (h / 2)
        merged = False

        for group in groups:
            gx, gy, gw, gh, _gdirection, _gscore = group[0]
            gcx = gx + (gw / 2)
            gcy = gy + (gh / 2)
            if abs(cx - gcx) <= max(12, int(max(w, gw) * 0.65)) and abs(cy - gcy) <= max(10, int(max(h, gh) * 0.65)):
                group.append(candidate)
                merged = True
                break

        if not merged:
            groups.append([candidate])

    unique = [max(group, key=lambda item: item[5]) for group in groups]
    return sorted(unique, key=lambda item: item[0])


def detect_arrows(gray_frame: np.ndarray, templates: dict[str, np.ndarray]) -> list[str]:
    if not np.any(gray_frame >= WHITE_PIXEL_THRESHOLD):
        return []

    _, binary = cv2.threshold(gray_frame, 200, 255, cv2.THRESH_BINARY)
    raw_matches: list[tuple[int, int, int, int, str, float]] = []

    for direction, template in templates.items():
        h, w = template.shape[:2]
        result = cv2.matchTemplate(binary, template, cv2.TM_CCOEFF_NORMED)
        ys, xs = np.where(result >= ARROW_THRESHOLD)
        for y, x in zip(ys, xs):
            raw_matches.append((int(x), int(y), int(w), int(h), direction, float(result[y, x])))

    if not raw_matches:
        return []

    return [item[4] for item in group_matches(raw_matches)]


def send_chain(arrows: list[str]) -> None:
    for direction in arrows:
        key = KEY_MAP.get(direction)
        if key is None:
            continue
        keyboard.press(key)
        time.sleep(HOLD_ARROW_SEC)
        keyboard.release(key)
        time.sleep(BETWEEN_ARROWS_SEC)


class DanceBot:
    def __init__(self, logger) -> None:
        self.log = logger
        self.is_active = False
        self._stop_event = threading.Event()
        self._worker: threading.Thread | None = None
        self.templates = load_arrow_templates("assets")

    def start(self) -> None:
        if self.is_active:
            return
        self.is_active = True
        self._stop_event.clear()
        self._worker = threading.Thread(target=self._loop, daemon=True)
        self._worker.start()

    def stop(self) -> None:
        if not self.is_active:
            return
        self.is_active = False
        self._stop_event.set()
        if self._worker and self._worker.is_alive():
            self._worker.join(timeout=1.0)
        self._worker = None

    def _loop(self) -> None:
        with mss.mss() as sct:
            while self.is_active and not self._stop_event.is_set():
                frame = np.array(sct.grab(SCAN_MONITOR))
                gray = cv2.cvtColor(frame, cv2.COLOR_BGRA2GRAY)
                arrows = detect_arrows(gray, self.templates)
                if not arrows:
                    continue

                send_chain(arrows)
                chain = " ".join(arrow.upper() for arrow in arrows)
                self.log(f"Ввел: {chain}")

                while self.is_active and not self._stop_event.is_set():
                    perf_frame = np.array(sct.grab(PERF_MONITOR))
                    perf_gray = cv2.cvtColor(perf_frame, cv2.COLOR_BGRA2GRAY)
                    brightness = int(np.mean(perf_gray))
                    if brightness >= BRIGHTNESS_SPACE_THRESHOLD:
                        keyboard.send("space")
                        self.log(f"Удар: {brightness}")
                        time.sleep(POST_SPACE_COOLDOWN_SEC)
                        break


class BotUI:
    def __init__(self) -> None:
        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("dark-blue")

        self.root = ctk.CTk()
        self.root.title("BottomBot")
        self.root.geometry("360x240")
        self.root.minsize(360, 240)
        self.root.resizable(False, False)
        self.root.attributes("-topmost", True)

        self.bot = DanceBot(self.append_log)

        self.start_btn = ctk.CTkButton(self.root, text="ЗАПУСТИТЬ ТАНЕЦ", command=self.toggle)
        self.start_btn.pack(fill="x", padx=10, pady=(10, 8))

        self.log_box = ctk.CTkTextbox(self.root, width=340, height=180)
        self.log_box.pack(fill="both", expand=True, padx=10, pady=(0, 10))
        self.log_box.configure(state="disabled")

    def append_log(self, message: str) -> None:
        line = f"[{datetime.now().strftime('%H:%M:%S.%f')[:-3]}] {message}\n"

        def write() -> None:
            self.log_box.configure(state="normal")
            self.log_box.insert("end", line)
            self.log_box.see("end")
            self.log_box.configure(state="disabled")

        self.root.after(0, write)

    def toggle(self) -> None:
        if self.bot.is_active:
            self.bot.stop()
            self.start_btn.configure(text="ЗАПУСТИТЬ ТАНЕЦ")
        else:
            self.bot.start()
            self.start_btn.configure(text="ОСТАНОВИТЬ ТАНЕЦ")

    def run(self) -> None:
        self.root.mainloop()


if __name__ == "__main__":
    ensure_admin()
    set_high_priority()
    BotUI().run()
