# Требуется установить зависимости:
# pip install pydirectinput mss numpy opencv-python customtkinter

import threading
import time
from datetime import datetime
from pathlib import Path

import customtkinter as ctk
import cv2
import mss
import numpy as np
import pydirectinput

SCAN_AREA = {"left": 450, "top": 690, "width": 742, "height": 65}
PERFECT_ZONE = {"left": 810, "top": 715, "width": 20, "height": 20}

TEMPLATE_THRESHOLD = 0.70
PERFECT_BRIGHTNESS = 230.0
KEY_DOWN_TIME = 0.05
POST_CHAIN_SLEEP = 1.0
IDLE_SLEEP = 0.01

KEY_MAP = {
    "left": "left",
    "down": "down",
    "up": "up",
    "right": "right",
}


class BottomBot:
    def __init__(self, log_callback):
        self.log = log_callback
        self._running = False
        self._worker: threading.Thread | None = None
        self._lock = threading.Lock()
        self.templates = self._load_templates()

    @property
    def is_running(self) -> bool:
        with self._lock:
            return self._running

    def start(self) -> None:
        with self._lock:
            if self._running:
                return
            self._running = True

        self._worker = threading.Thread(target=self._run, daemon=True)
        self._worker.start()
        self.log("Бот запущен")

    def stop(self) -> None:
        with self._lock:
            self._running = False
        self.log("Бот остановлен")

    def _load_templates(self) -> dict[str, np.ndarray]:
        assets_dir = Path(__file__).resolve().parent / "assets"
        template_files = {
            "left": assets_dir / "left.png",
            "down": assets_dir / "down.png",
            "up": assets_dir / "up.png",
            "right": assets_dir / "right.png",
        }

        templates: dict[str, np.ndarray] = {}
        for name, path in template_files.items():
            image = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
            if image is None:
                self.log(f"Не найден шаблон: {path.name}")
                continue
            templates[name] = image

        if not templates:
            self.log("Шаблоны не загружены")
        return templates

    @staticmethod
    def _to_gray(sct: mss.mss, region: dict[str, int]) -> np.ndarray:
        frame = np.array(sct.grab(region))
        return cv2.cvtColor(frame, cv2.COLOR_BGRA2GRAY)

    def _detect_arrows(self, gray_frame: np.ndarray) -> list[str]:
        found: list[tuple[int, str, float]] = []

        for direction, template in self.templates.items():
            result = cv2.matchTemplate(gray_frame, template, cv2.TM_CCOEFF_NORMED)
            ys, xs = np.where(result >= TEMPLATE_THRESHOLD)
            for y, x in zip(ys, xs):
                found.append((int(x), direction, float(result[y, x])))

        # Убираем повторы по соседним позициям и берём самые уверенные совпадения.
        buckets: dict[int, tuple[int, str]] = {}
        for x, direction, score in sorted(found, key=lambda it: it[2], reverse=True):
            bucket = x // 8
            if bucket not in buckets:
                buckets[bucket] = (x, direction)

        ordered = sorted(buckets.values(), key=lambda it: it[0])
        return [direction for _, direction in ordered]

    @staticmethod
    def _press_direct_key(key: str) -> None:
        pydirectinput.keyDown(key)
        time.sleep(KEY_DOWN_TIME)
        pydirectinput.keyUp(key)

    def _press_chain(self, chain: list[str]) -> None:
        keys = [KEY_MAP[d] for d in chain if d in KEY_MAP]
        if not keys:
            return

        for key in keys:
            self._press_direct_key(key)

        self.log(f"Нажал: {keys}")
        self.log("Жду вспышку...")
        time.sleep(POST_CHAIN_SLEEP)

    def _wait_for_perfect(self, sct: mss.mss) -> None:
        while self.is_running:
            perfect = self._to_gray(sct, PERFECT_ZONE)
            if float(np.mean(perfect)) >= PERFECT_BRIGHTNESS:
                self._press_direct_key("space")
                self.log("Perfect!")
                return
            time.sleep(0.005)

    def _run(self) -> None:
        if not self.templates:
            self.stop()
            return

        with mss.mss() as sct:
            while self.is_running:
                try:
                    self.log("Ищу стрелки...")
                    scan = self._to_gray(sct, SCAN_AREA)
                    chain = self._detect_arrows(scan)

                    if not chain:
                        time.sleep(IDLE_SLEEP)
                        continue

                    self._press_chain(chain)
                    self._wait_for_perfect(sct)
                except Exception as exc:
                    self.log(f"Ошибка в цикле: {exc}")
                    time.sleep(0.1)


class App:
    def __init__(self):
        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("dark-blue")

        self.root = ctk.CTk()
        self.root.title("BottomBot")
        self.root.geometry("560x380")

        self.bot = BottomBot(self.log)

        controls = ctk.CTkFrame(self.root)
        controls.pack(fill="x", padx=12, pady=12)

        self.start_btn = ctk.CTkButton(controls, text="СТАРТ", command=self.bot.start, width=200)
        self.start_btn.pack(side="left", padx=8, pady=8)

        self.stop_btn = ctk.CTkButton(controls, text="СТОП", command=self.bot.stop, width=200)
        self.stop_btn.pack(side="left", padx=8, pady=8)

        self.log_box = ctk.CTkTextbox(self.root, wrap="word")
        self.log_box.pack(fill="both", expand=True, padx=12, pady=(0, 12))
        self.log_box.configure(state="disabled")

        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        self.log("Готов")

    def log(self, text: str) -> None:
        timestamp = datetime.now().strftime("%H:%M:%S")
        line = f"[{timestamp}] {text}\n"

        def append():
            self.log_box.configure(state="normal")
            self.log_box.insert("end", line)
            self.log_box.see("end")
            self.log_box.configure(state="disabled")

        self.root.after(0, append)

    def on_close(self):
        self.bot.stop()
        self.root.after(100, self.root.destroy)


if __name__ == "__main__":
    app = App()
    app.root.mainloop()
