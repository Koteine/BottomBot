import threading
import time
from datetime import datetime
from pathlib import Path

import customtkinter as ctk
import cv2
import keyboard
import mss
import numpy as np

# Зашитые координаты (идеальные)
SCAN_AREA = {"top": 690, "left": 450, "width": 742, "height": 65}
PERFECT_ZONE = {"top": 715, "left": 810, "width": 20, "height": 20}

TEMPLATE_THRESHOLD = 0.70
PERFECT_BRIGHTNESS = 230
KEY_PRESS_DELAY = 0.02


class SmartDancerBot:
    def __init__(self, log_callback):
        self.log = log_callback
        self._running = False
        self._thread = None
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

        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        self.log("Бот запущен")

    def stop(self) -> None:
        with self._lock:
            self._running = False
        self.log("Бот остановлен")

    def _load_templates(self) -> dict[str, np.ndarray]:
        assets_dir = Path(__file__).resolve().parent / "assets"
        files = {
            "left": assets_dir / "left.png",
            "down": assets_dir / "down.png",
            "up": assets_dir / "up.png",
            "right": assets_dir / "right.png",
        }

        templates: dict[str, np.ndarray] = {}
        for direction, path in files.items():
            image = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
            if image is None:
                self.log(f"Ошибка assets: не найден файл {path.name}")
                continue
            templates[direction] = image

        if not templates:
            self.log("Ошибка assets: шаблоны не загружены (bot не сможет искать стрелки)")

        return templates

    @staticmethod
    def _detect_arrows(gray_frame: np.ndarray, templates: dict[str, np.ndarray]) -> list[str]:
        candidates: list[tuple[int, str, float]] = []

        for direction, template in templates.items():
            result = cv2.matchTemplate(gray_frame, template, cv2.TM_CCOEFF_NORMED)
            ys, xs = np.where(result >= TEMPLATE_THRESHOLD)
            for y, x in zip(ys, xs):
                candidates.append((int(x), direction, float(result[y, x])))

        # Убираем дубли, сохраняя самые сильные срабатывания в близких позициях.
        unique_by_bucket: dict[int, tuple[int, str]] = {}
        for x, direction, score in sorted(candidates, key=lambda item: item[2], reverse=True):
            bucket = x // 8
            if bucket not in unique_by_bucket:
                unique_by_bucket[bucket] = (x, direction)

        sorted_hits = sorted(unique_by_bucket.values(), key=lambda item: item[0])
        return [direction for _, direction in sorted_hits]

    @staticmethod
    def _frame_to_gray(sct: mss.mss, region: dict[str, int]) -> np.ndarray:
        frame = np.array(sct.grab(region))
        return cv2.cvtColor(frame, cv2.COLOR_BGRA2GRAY)

    def _tap_chain(self, chain: list[str]) -> None:
        self.log("Нажимаю цепочку: " + " ".join(chain))
        for key_name in chain:
            keyboard.press_and_release(key_name)
            time.sleep(KEY_PRESS_DELAY)

    def _wait_and_hit_perfect(self, sct: mss.mss) -> None:
        while self.is_running:
            perfect_gray = self._frame_to_gray(sct, PERFECT_ZONE)
            if float(np.mean(perfect_gray)) > PERFECT_BRIGHTNESS:
                keyboard.press_and_release("space")
                self.log("Удар в Perfect")
                return
            time.sleep(0.005)

    def _loop(self) -> None:
        if not self.templates:
            self.log("Остановка: нет шаблонов для распознавания")
            with self._lock:
                self._running = False
            return

        try:
            with mss.mss() as sct:
                while self.is_running:
                    scan_gray = self._frame_to_gray(sct, SCAN_AREA)
                    chain = self._detect_arrows(scan_gray, self.templates)

                    if not chain:
                        time.sleep(0.005)
                        continue

                    self.log("Вижу стрелки: " + " ".join(chain))
                    self._tap_chain(chain)
                    self._wait_and_hit_perfect(sct)

        except Exception as error:
            self.log(f"Ошибка в рабочем потоке: {error}")
        finally:
            with self._lock:
                self._running = False


class AppUI:
    def __init__(self):
        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("dark-blue")

        self.root = ctk.CTk()
        self.root.title("SmartDancer BottomBot")
        self.root.geometry("560x420")
        self.root.minsize(520, 360)

        self.bot = SmartDancerBot(self.log)

        self.header = ctk.CTkLabel(
            self.root,
            text="SmartDancer",
            font=ctk.CTkFont(size=24, weight="bold"),
        )
        self.header.pack(pady=(14, 10))

        self.controls = ctk.CTkFrame(self.root)
        self.controls.pack(fill="x", padx=14, pady=(0, 10))

        self.start_button = ctk.CTkButton(
            self.controls,
            text="СТАРТ",
            width=180,
            height=40,
            command=self.start_bot,
            font=ctk.CTkFont(size=15, weight="bold"),
        )
        self.start_button.pack(side="left", padx=10, pady=10)

        self.stop_button = ctk.CTkButton(
            self.controls,
            text="СТОП",
            width=180,
            height=40,
            command=self.stop_bot,
            font=ctk.CTkFont(size=15, weight="bold"),
            fg_color="#8B1E2D",
            hover_color="#A32537",
        )
        self.stop_button.pack(side="left", padx=10, pady=10)

        self.log_box = ctk.CTkTextbox(self.root, wrap="word")
        self.log_box.pack(fill="both", expand=True, padx=14, pady=(0, 14))
        self.log_box.configure(state="disabled")

        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        self.log("Готов к работе")

    def log(self, message: str) -> None:
        timestamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]
        line = f"[{timestamp}] {message}\n"

        def _append():
            self.log_box.configure(state="normal")
            self.log_box.insert("end", line)
            self.log_box.see("end")
            self.log_box.configure(state="disabled")

        self.root.after(0, _append)

    def start_bot(self) -> None:
        self.bot.start()

    def stop_bot(self) -> None:
        self.bot.stop()

    def on_close(self) -> None:
        self.bot.stop()
        self.root.after(100, self.root.destroy)


if __name__ == "__main__":
    app = AppUI()
    app.root.mainloop()
