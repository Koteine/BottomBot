"""Screen capture and vision-based beat detection thread."""

from __future__ import annotations

import time
from dataclasses import dataclass

import cv2
import mss
import numpy as np
from PySide6.QtCore import QThread, Signal


@dataclass
class DetectorConfig:
    threshold: int = 28
    min_motion_area: int = 600
    fps: int = 45
    debug: bool = True


class BeatDetectorThread(QThread):
    beat_detected = Signal(float, float)  # timestamp, strength
    frame_ready = Signal(object)  # debug preview frame (numpy array)

    def __init__(self, region: dict[str, int], config: DetectorConfig) -> None:
        super().__init__()
        self.region = region
        self.config = config
        self._running = True

    def stop(self) -> None:
        self._running = False
        self.wait(1000)

    def update_threshold(self, value: int) -> None:
        self.config.threshold = value

    def set_debug(self, enabled: bool) -> None:
        self.config.debug = enabled

    def run(self) -> None:
        with mss.mss() as sct:
            prev_gray = None
            last_emit = 0.0
            frame_interval = 1.0 / max(1, self.config.fps)

            while self._running:
                start = time.perf_counter()
                img = np.array(sct.grab(self.region))
                frame = cv2.cvtColor(img, cv2.COLOR_BGRA2BGR)
                gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)

                strength = 0.0
                motion_mask = np.zeros_like(gray)

                if prev_gray is not None:
                    diff = cv2.absdiff(gray, prev_gray)
                    _, motion_mask = cv2.threshold(diff, self.config.threshold, 255, cv2.THRESH_BINARY)
                    motion_mask = cv2.GaussianBlur(motion_mask, (5, 5), 0)
                    contours, _ = cv2.findContours(motion_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
                    area = sum(cv2.contourArea(c) for c in contours)
                    strength = float(area)

                    now = time.perf_counter()
                    if area > self.config.min_motion_area and (now - last_emit) > 0.1:
                        last_emit = now
                        self.beat_detected.emit(now, strength)

                prev_gray = gray

                if self.config.debug:
                    debug_frame = frame.copy()
                    if prev_gray is not None:
                        heat = cv2.applyColorMap(motion_mask.astype(np.uint8), cv2.COLORMAP_JET)
                        debug_frame = cv2.addWeighted(debug_frame, 0.7, heat, 0.3, 0)
                    cv2.putText(
                        debug_frame,
                        f"Motion: {int(strength)}",
                        (10, 25),
                        cv2.FONT_HERSHEY_SIMPLEX,
                        0.7,
                        (0, 255, 0),
                        2,
                    )
                    self.frame_ready.emit(debug_frame)

                elapsed = time.perf_counter() - start
                sleep_for = frame_interval - elapsed
                if sleep_for > 0:
                    time.sleep(sleep_for)
