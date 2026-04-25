import multiprocessing as mp
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import customtkinter as ctk
import cv2
import mss
import numpy as np
import pyautogui

STATE_SCANNING = 0
STATE_WAITING_PERFECT = 1


@dataclass(frozen=True)
class Zones:
    arrow_zone: dict
    perfect_zone: dict


class SmartDancerPro:
    def __init__(self):
        self.window = ctk.CTk()
        self.window.title("Smart Dancer Pro (1600x900)")
        self.window.geometry("620x620")
        self.window.attributes("-topmost", True)

        self.zones = Zones(
            arrow_zone={"top": 650, "left": 400, "width": 800, "height": 250},
            perfect_zone={"top": 505, "left": 775, "width": 50, "height": 50},
        )

        self.match_threshold = 0.85
        self.space_brightness_peak = 240.0
        self.dedupe_radius_px = 10

        self.auto_keys_var = ctk.BooleanVar(value=True)
        self.auto_space_var = ctk.BooleanVar(value=True)
        self.space_delay_var = ctk.IntVar(value=0)

        self.is_active = False
        self.worker_processes = []

        self.command_queue = mp.Queue()
        self.log_queue = mp.Queue()

        self.shared_auto_keys = mp.Value("b", True)
        self.shared_auto_space = mp.Value("b", True)
        self.shared_space_delay_ms = mp.Value("i", 0)
        self.shared_state = mp.Value("i", STATE_SCANNING)
        self.stop_event = mp.Event()

        self._build_ui()
        self.window.after(80, self._poll_logs)

    def _build_ui(self):
        self.start_button = ctk.CTkButton(self.window, text="СТАРТ", command=self.toggle)
        self.start_button.pack(pady=(14, 8))

        self.status_label = ctk.CTkLabel(self.window, text="Статус: Выключен")
        self.status_label.pack(pady=(0, 10))

        self.auto_keys_switch = ctk.CTkSwitch(
            self.window,
            text="Auto-Keys",
            variable=self.auto_keys_var,
            onvalue=True,
            offvalue=False,
            command=self._sync_runtime_settings,
        )
        self.auto_keys_switch.pack(pady=(0, 6))

        self.auto_space_switch = ctk.CTkSwitch(
            self.window,
            text="Auto-Space",
            variable=self.auto_space_var,
            onvalue=True,
            offvalue=False,
            command=self._sync_runtime_settings,
        )
        self.auto_space_switch.pack(pady=(0, 10))

        delay_frame = ctk.CTkFrame(self.window)
        delay_frame.pack(padx=14, pady=(0, 10), fill="x")

        ctk.CTkLabel(delay_frame, text="Space Delay (ms): -100 .. 100").pack(anchor="w", padx=10, pady=(8, 2))

        self.delay_slider = ctk.CTkSlider(
            delay_frame,
            from_=-100,
            to=100,
            number_of_steps=200,
            command=self._on_delay_slider,
        )
        self.delay_slider.pack(fill="x", padx=10, pady=(0, 8))
        self.delay_slider.set(0)

        self.delay_entry = ctk.CTkEntry(delay_frame)
        self.delay_entry.pack(fill="x", padx=10, pady=(0, 10))
        self.delay_entry.insert(0, "0")
        self.delay_entry.bind("<Return>", self._on_delay_entry)
        self.delay_entry.bind("<FocusOut>", self._on_delay_entry)

        zone_text = (
            f"arrow_zone: {self.zones.arrow_zone}\\n"
            f"perfect_zone (tiny focus): {self.zones.perfect_zone}"
        )
        self.zone_label = ctk.CTkLabel(self.window, text=zone_text)
        self.zone_label.pack(pady=(0, 10))

        self.log_box = ctk.CTkTextbox(self.window, width=580, height=310)
        self.log_box.pack(padx=14, pady=(0, 14), fill="both", expand=True)
        self.log_box.insert("end", "Smart Dancer Pro log\\n")
        self.log_box.configure(state="disabled")

    def _append_log(self, message):
        timestamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]
        line = f"[{timestamp}] {message}\\n"
        self.log_box.configure(state="normal")
        self.log_box.insert("end", line)
        self.log_box.see("end")
        self.log_box.configure(state="disabled")

    def _poll_logs(self):
        while not self.log_queue.empty():
            message = self.log_queue.get()
            self._append_log(message)
        self.window.after(80, self._poll_logs)

    def _sync_runtime_settings(self):
        self.shared_auto_keys.value = bool(self.auto_keys_var.get())
        self.shared_auto_space.value = bool(self.auto_space_var.get())
        self.shared_space_delay_ms.value = int(self.space_delay_var.get())

    def _on_delay_slider(self, value):
        ms = int(round(float(value)))
        self.space_delay_var.set(ms)
        self.delay_entry.delete(0, "end")
        self.delay_entry.insert(0, str(ms))
        self._sync_runtime_settings()

    def _on_delay_entry(self, _event=None):
        raw = self.delay_entry.get().strip() or "0"
        try:
            ms = int(raw)
        except ValueError:
            ms = self.space_delay_var.get()

        ms = max(-100, min(100, ms))
        self.space_delay_var.set(ms)
        self.delay_slider.set(ms)
        self.delay_entry.delete(0, "end")
        self.delay_entry.insert(0, str(ms))
        self._sync_runtime_settings()

    def toggle(self):
        if self.is_active:
            self._stop_workers()
            self._append_log("Бот остановлен")
            return

        self._start_workers()
        self._append_log("Бот запущен (2 процесса)")

    def _start_workers(self):
        self.is_active = True
        self.stop_event.clear()
        self.shared_state.value = STATE_SCANNING
        self._sync_runtime_settings()

        assets_dir = str(Path("assets").resolve())

        key_proc = mp.Process(
            target=key_worker,
            args=(
                self.zones.arrow_zone,
                assets_dir,
                self.match_threshold,
                self.dedupe_radius_px,
                self.shared_auto_keys,
                self.shared_auto_space,
                self.shared_state,
                self.command_queue,
                self.log_queue,
                self.stop_event,
            ),
            daemon=True,
        )

        space_proc = mp.Process(
            target=space_worker,
            args=(
                self.zones.perfect_zone,
                self.space_brightness_peak,
                self.shared_auto_space,
                self.shared_space_delay_ms,
                self.shared_state,
                self.command_queue,
                self.log_queue,
                self.stop_event,
            ),
            daemon=True,
        )

        key_proc.start()
        space_proc.start()
        self.worker_processes = [key_proc, space_proc]

        self.start_button.configure(text="СТОП", fg_color="red")
        self.status_label.configure(text="Статус: Работает")

    def _stop_workers(self):
        self.is_active = False
        self.stop_event.set()
        for proc in self.worker_processes:
            proc.join(timeout=1.5)
            if proc.is_alive():
                proc.terminate()
        self.worker_processes = []

        self.start_button.configure(text="СТАРТ", fg_color=["#3a7ebf", "#1f538d"])
        self.status_label.configure(text="Статус: Выключен")


def load_arrow_templates(assets_dir):
    files = {
        "up": "up.png",
        "down": "down.png",
        "left": "left.png",
        "right": "right.png",
    }
    templates = {}
    for direction, filename in files.items():
        template_path = Path(assets_dir) / filename
        template = cv2.imread(str(template_path), cv2.IMREAD_GRAYSCALE)
        if template is None:
            raise FileNotFoundError(f"Не найден шаблон стрелки: {template_path}")
        templates[direction] = template
    return templates


def dedupe_detections(detections, radius_px):
    if not detections:
        return []

    detections.sort(key=lambda item: item["score"], reverse=True)
    result = []
    for item in detections:
        duplicate = False
        for kept in result:
            if abs(item["center_x"] - kept["center_x"]) <= radius_px and abs(item["center_y"] - kept["center_y"]) <= radius_px:
                duplicate = True
                break
        if not duplicate:
            result.append(item)

    result.sort(key=lambda item: item["center_x"])
    return result


def detect_combo(gray_frame, templates, threshold, dedupe_radius_px):
    detections = []
    for direction, template in templates.items():
        result = cv2.matchTemplate(gray_frame, template, cv2.TM_CCOEFF_NORMED)
        ys, xs = np.where(result >= threshold)
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

    return dedupe_detections(detections, dedupe_radius_px)


def press_arrows(combo):
    for item in combo:
        pyautogui.press(item["direction"])
        time.sleep(0.025)


def maybe_save_debug_frame(combo, arrow_frame_bgra):
    directions = [item["direction"] for item in combo]
    if "up" not in directions and "down" not in directions:
        return

    debug_dir = Path("debug")
    debug_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    debug_path = debug_dir / f"up_down_debug_{stamp}.png"
    bgr = cv2.cvtColor(arrow_frame_bgra, cv2.COLOR_BGRA2BGR)
    cv2.imwrite(str(debug_path), bgr)


def key_worker(
    arrow_zone,
    assets_dir,
    threshold,
    dedupe_radius_px,
    auto_keys,
    auto_space,
    shared_state,
    command_queue,
    log_queue,
    stop_event,
):
    pyautogui.PAUSE = 0
    pyautogui.FAILSAFE = False
    templates = load_arrow_templates(assets_dir)
    last_signature = None

    with mss.mss() as sct:
        while not stop_event.is_set():
            if shared_state.value != STATE_SCANNING:
                time.sleep(0.002)
                continue

            arrow_frame = np.array(sct.grab(arrow_zone))
            gray = cv2.cvtColor(arrow_frame, cv2.COLOR_BGRA2GRAY)
            combo = detect_combo(gray, templates, threshold, dedupe_radius_px)

            if not combo:
                time.sleep(0.002)
                continue

            signature = tuple((item["direction"], item["center_x"]) for item in combo)
            if signature == last_signature:
                time.sleep(0.003)
                continue
            last_signature = signature

            maybe_save_debug_frame(combo, arrow_frame)

            combo_names = [item["direction"].upper() for item in combo]
            log_queue.put(f"Считано: {combo_names} -> Жду Perfect...")

            if auto_keys.value:
                press_arrows(combo)

            if auto_space.value:
                shared_state.value = STATE_WAITING_PERFECT
                command_queue.put({"cmd": "WAIT_PERFECT"})

            time.sleep(0.002)


def space_worker(
    perfect_zone,
    brightness_threshold,
    auto_space,
    space_delay_ms,
    shared_state,
    command_queue,
    log_queue,
    stop_event,
):
    pyautogui.PAUSE = 0
    pyautogui.FAILSAFE = False

    with mss.mss() as sct:
        while not stop_event.is_set():
            if not auto_space.value:
                shared_state.value = STATE_SCANNING
                time.sleep(0.01)
                continue

            if shared_state.value == STATE_SCANNING:
                time.sleep(0.002)
                continue

            while not command_queue.empty():
                _ = command_queue.get()

            frame = np.array(sct.grab(perfect_zone))
            gray = cv2.cvtColor(frame, cv2.COLOR_BGRA2GRAY)
            mean_brightness = float(np.mean(gray))

            if mean_brightness > brightness_threshold:
                delay = space_delay_ms.value / 1000.0
                if delay > 0:
                    time.sleep(delay)

                pyautogui.press("space")

                if delay < 0:
                    # Отрицательная задержка не может нажать раньше света, но логируем её для настройки.
                    log_queue.put(
                        f"Perfect пик={mean_brightness:.1f} (delay {space_delay_ms.value}ms, ранний сдвиг ограничен физически)"
                    )
                else:
                    log_queue.put(f"Perfect пик={mean_brightness:.1f} -> Space (delay {space_delay_ms.value}ms)")

                shared_state.value = STATE_SCANNING

            time.sleep(0.001)


if __name__ == "__main__":
    mp.freeze_support()
    app = SmartDancerPro()
    app.window.mainloop()
