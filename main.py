diff --git a/main.py b/main.py
index a32836e0cb3a8ad8c4ec64dfbcf6b8376df117de..8a59a978de98989536435e8b11a234926acb75f3 100644
--- a/main.py
+++ b/main.py
@@ -1,20 +1,332 @@
-"""Entry point for the desktop rhythm assistant."""
+"""Auto-Dance prototype for rhythm games.
+
+ВНИМАНИЕ:
+Этот код предоставлен только как технический прототип по запросу пользователя.
+Используйте его только в разрешённых средах (тест, собственный проект, sandbox).
+"""
 
 from __future__ import annotations
 
-import sys
+import os
+import random
+import threading
+import time
+from dataclasses import dataclass
+from typing import Dict, List, Optional, Tuple
+
+import customtkinter as ctk
+import cv2
+import mss
+import numpy as np
+import pyautogui
+
+
+# Русский комментарий:
+# В целях более "человечного" ввода добавляем небольшую рандомную задержку.
+MIN_KEY_DELAY = 0.01
+MAX_KEY_DELAY = 0.03
+
+# Русский комментарий:
+# Карта шаблонов стрелок -> клавиша, которую нужно нажать.
+ARROW_KEY_MAP = {
+    "left": "left",
+    "down": "down",
+    "up": "up",
+    "right": "right",
+}
+
+# Русский комментарий:
+# Допустимые яркие цвета для Perfect Zone (можно расширять).
+DEFAULT_PERFECT_COLORS = [
+    (255, 255, 255),  # белый
+    (245, 245, 245),  # почти белый
+]
+
+
+@dataclass
+class SearchRegion:
+    left: int
+    top: int
+    width: int
+    height: int
+
+
+@dataclass
+class PerfectZone:
+    left: int
+    top: int
+    width: int
+    height: int
+
+
+class AutoDanceBot:
+    """Ядро бота: захват экрана, поиск стрелок и прожим клавиш."""
+
+    def __init__(self) -> None:
+        self.running = False
+        self.lock = threading.Lock()
+        self.worker_thread: Optional[threading.Thread] = None
+
+        self.search_region = SearchRegion(left=400, top=720, width=800, height=120)
+        self.perfect_zone = PerfectZone(left=650, top=685, width=300, height=40)
+
+        self.template_threshold = 0.78
+        self.color_tolerance = 35
+        self.perfect_colors = DEFAULT_PERFECT_COLORS.copy()
+
+        self.templates = self._load_templates()
+
+    def _load_templates(self) -> Dict[str, np.ndarray]:
+        """Загрузка шаблонов стрелок из папки assets/.
+
+        Если шаблоны отсутствуют или не читаются, возвращаем пустой словарь,
+        чтобы приложение не падало.
+        """
+        templates: Dict[str, np.ndarray] = {}
+        assets_dir = os.path.join(os.getcwd(), "assets")
+
+        for arrow_name in ARROW_KEY_MAP:
+            file_path = os.path.join(assets_dir, f"{arrow_name}.png")
+            try:
+                if not os.path.exists(file_path):
+                    print(f"[WARN] Template not found: {file_path}")
+                    continue
+
+                template = cv2.imread(file_path, cv2.IMREAD_GRAYSCALE)
+                if template is None:
+                    print(f"[WARN] Template unreadable: {file_path}")
+                    continue
+
+                templates[arrow_name] = template
+            except Exception as exc:  # noqa: BLE001
+                print(f"[ERROR] Failed to load template {file_path}: {exc}")
+
+        if not templates:
+            print("[WARN] No arrow templates loaded. Detection will be skipped.")
+
+        return templates
+
+    def update_regions(self, search: SearchRegion, perfect: PerfectZone) -> None:
+        with self.lock:
+            self.search_region = search
+            self.perfect_zone = perfect
+
+    def start(self) -> None:
+        if self.running:
+            return
+        self.running = True
+        self.worker_thread = threading.Thread(target=self._run_loop, daemon=True)
+        self.worker_thread.start()
+
+    def stop(self) -> None:
+        self.running = False
+        if self.worker_thread and self.worker_thread.is_alive():
+            self.worker_thread.join(timeout=1.0)
+
+    def _run_loop(self) -> None:
+        """Основной цикл бота."""
+        with mss.mss() as screen:
+            while self.running:
+                try:
+                    with self.lock:
+                        search_region = self.search_region
+                        perfect_zone = self.perfect_zone
+
+                    ordered_arrows = self._detect_arrows(screen, search_region)
+                    if ordered_arrows:
+                        self._press_sequence(ordered_arrows)
+
+                    if self._is_perfect_moment(screen, perfect_zone):
+                        pyautogui.press("space")
+                        time.sleep(random.uniform(MIN_KEY_DELAY, MAX_KEY_DELAY))
+
+                    # Короткая пауза для снижения нагрузки на CPU.
+                    time.sleep(0.005)
+                except Exception as exc:  # noqa: BLE001
+                    # Русский комментарий: перехват любой ошибки в цикле,
+                    # чтобы приложение не завершалось аварийно.
+                    print(f"[ERROR] Runtime error: {exc}")
+                    time.sleep(0.05)
+
+    def _detect_arrows(self, screen: mss.mss, region: SearchRegion) -> List[str]:
+        if not self.templates:
+            return []
+
+        monitor = {
+            "left": region.left,
+            "top": region.top,
+            "width": region.width,
+            "height": region.height,
+        }
+
+        shot = np.array(screen.grab(monitor))
+        gray = cv2.cvtColor(shot, cv2.COLOR_BGRA2GRAY)
+
+        detections: List[Tuple[int, str]] = []
+
+        for arrow_name, template in self.templates.items():
+            try:
+                result = cv2.matchTemplate(gray, template, cv2.TM_CCOEFF_NORMED)
+                locations = np.where(result >= self.template_threshold)
+
+                for y, x in zip(*locations):
+                    detections.append((int(x), arrow_name))
+            except Exception as exc:  # noqa: BLE001
+                print(f"[ERROR] Matching failed for {arrow_name}: {exc}")
+
+        if not detections:
+            return []
+
+        # Сортировка слева направо.
+        detections.sort(key=lambda item: item[0])
+
+        # Удаляем близкие дубликаты по X.
+        filtered: List[Tuple[int, str]] = []
+        min_gap_px = 12
+        for x_coord, arrow_name in detections:
+            if filtered and abs(filtered[-1][0] - x_coord) < min_gap_px:
+                continue
+            filtered.append((x_coord, arrow_name))
+
+        # Берем только от 1 до 10 стрелок.
+        filtered = filtered[:10]
+        return [item[1] for item in filtered]
+
+    def _press_sequence(self, arrows: List[str]) -> None:
+        for arrow_name in arrows:
+            key = ARROW_KEY_MAP.get(arrow_name)
+            if not key:
+                continue
+            pyautogui.press(key)
+            time.sleep(random.uniform(MIN_KEY_DELAY, MAX_KEY_DELAY))
+
+    def _is_perfect_moment(self, screen: mss.mss, zone: PerfectZone) -> bool:
+        monitor = {
+            "left": zone.left,
+            "top": zone.top,
+            "width": max(zone.width, 1),
+            "height": max(zone.height, 1),
+        }
+
+        shot = np.array(screen.grab(monitor))
+        rgb = cv2.cvtColor(shot, cv2.COLOR_BGRA2RGB)
+
+        # Проверяем средний цвет зоны, чтобы снизить шум.
+        mean_color = tuple(int(x) for x in np.mean(rgb.reshape(-1, 3), axis=0))
+
+        for target in self.perfect_colors:
+            if self._is_close_color(mean_color, target, self.color_tolerance):
+                return True
+        return False
+
+    @staticmethod
+    def _is_close_color(color: Tuple[int, int, int], target: Tuple[int, int, int], tolerance: int) -> bool:
+        return all(abs(c - t) <= tolerance for c, t in zip(color, target))
+
+
+class App(ctk.CTk):
+    """Минималистичный UI на customtkinter."""
+
+    def __init__(self) -> None:
+        super().__init__()
+
+        self.title("Auto-Dance Prototype")
+        self.geometry("540x430")
+        ctk.set_appearance_mode("dark")
+        ctk.set_default_color_theme("dark-blue")
+
+        self.bot = AutoDanceBot()
+
+        self._build_ui()
+
+    def _build_ui(self) -> None:
+        title = ctk.CTkLabel(self, text="Auto-Dance Control", font=("Arial", 20, "bold"))
+        title.pack(pady=12)
+
+        # Поля зоны поиска стрелок
+        self.search_left = self._entry_row("Search Left", "400")
+        self.search_top = self._entry_row("Search Top", "720")
+        self.search_width = self._entry_row("Search Width", "800")
+        self.search_height = self._entry_row("Search Height", "120")
+
+        separator = ctk.CTkLabel(self, text="Perfect Zone", font=("Arial", 16, "bold"))
+        separator.pack(pady=(10, 4))
+
+        self.perfect_left = self._entry_row("Perfect Left", "650")
+        self.perfect_top = self._entry_row("Perfect Top", "685")
+        self.perfect_width = self._entry_row("Perfect Width", "300")
+        self.perfect_height = self._entry_row("Perfect Height", "40")
+
+        btn_frame = ctk.CTkFrame(self)
+        btn_frame.pack(fill="x", padx=16, pady=14)
+
+        self.start_button = ctk.CTkButton(btn_frame, text="Старт", command=self.on_start)
+        self.start_button.pack(side="left", padx=8, pady=10, expand=True)
+
+        self.stop_button = ctk.CTkButton(btn_frame, text="Стоп", command=self.on_stop)
+        self.stop_button.pack(side="left", padx=8, pady=10, expand=True)
+
+        self.status_label = ctk.CTkLabel(self, text="Status: idle", text_color="#b0b0b0")
+        self.status_label.pack(pady=6)
+
+        note = (
+            "Templates: assets/left.png, assets/down.png, assets/up.png, assets/right.png\n"
+            "Bot reads up to 10 arrows, sorts left→right, then presses keys."
+        )
+        info = ctk.CTkLabel(self, text=note, justify="left")
+        info.pack(padx=12, pady=8)
+
+    def _entry_row(self, label_text: str, default: str) -> ctk.CTkEntry:
+        frame = ctk.CTkFrame(self)
+        frame.pack(fill="x", padx=16, pady=3)
+
+        label = ctk.CTkLabel(frame, text=label_text, width=130, anchor="w")
+        label.pack(side="left", padx=8, pady=8)
+
+        entry = ctk.CTkEntry(frame)
+        entry.insert(0, default)
+        entry.pack(side="left", fill="x", expand=True, padx=8, pady=8)
+        return entry
+
+    def on_start(self) -> None:
+        try:
+            search = SearchRegion(
+                left=int(self.search_left.get()),
+                top=int(self.search_top.get()),
+                width=int(self.search_width.get()),
+                height=int(self.search_height.get()),
+            )
+            perfect = PerfectZone(
+                left=int(self.perfect_left.get()),
+                top=int(self.perfect_top.get()),
+                width=int(self.perfect_width.get()),
+                height=int(self.perfect_height.get()),
+            )
+
+            if search.width <= 0 or search.height <= 0:
+                raise ValueError("Search region size must be > 0")
 
-from PySide6.QtWidgets import QApplication
+            self.bot.update_regions(search, perfect)
+            self.bot.start()
+            self.status_label.configure(text="Status: running", text_color="#72e06a")
+        except ValueError as exc:
+            self.status_label.configure(text=f"Input error: {exc}", text_color="#ff6b6b")
+        except Exception as exc:  # noqa: BLE001
+            self.status_label.configure(text=f"Start failed: {exc}", text_color="#ff6b6b")
 
-from ui.main_window import MainWindow
+    def on_stop(self) -> None:
+        try:
+            self.bot.stop()
+            self.status_label.configure(text="Status: stopped", text_color="#f5c542")
+        except Exception as exc:  # noqa: BLE001
+            self.status_label.configure(text=f"Stop failed: {exc}", text_color="#ff6b6b")
 
 
-def main() -> int:
-    app = QApplication(sys.argv)
-    window = MainWindow()
-    window.show()
-    return app.exec()
+def main() -> None:
+    pyautogui.FAILSAFE = False
+    app = App()
+    app.mainloop()
 
 
 if __name__ == "__main__":
-    raise SystemExit(main())
+    main()
