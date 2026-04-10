"""Beat timestamp and BPM estimation utilities."""

from __future__ import annotations

from collections import deque
from typing import Deque


class BeatEstimator:
    """Maintains beat timestamps and a smoothed BPM estimate."""

    def __init__(self, window_size: int = 12) -> None:
        self.window_size = window_size
        self.timestamps: Deque[float] = deque(maxlen=256)
        self.intervals: Deque[float] = deque(maxlen=window_size)
        self.calibration_offset = 0.0

    def add_event(self, timestamp: float) -> None:
        timestamp += self.calibration_offset
        if self.timestamps:
            interval = timestamp - self.timestamps[-1]
            if 0.15 <= interval <= 2.0:
                self.intervals.append(interval)
        self.timestamps.append(timestamp)

    def get_bpm(self) -> float:
        if not self.intervals:
            return 0.0
        avg_interval = sum(self.intervals) / len(self.intervals)
        return 60.0 / avg_interval if avg_interval > 0 else 0.0

    def calibrate_with_taps(self, visual_ts: float, tap_ts: float) -> None:
        """Shift visual timings toward user tap timing."""
        self.calibration_offset += (tap_ts - visual_ts) * 0.5

    def recent_beats(self) -> list[float]:
        return list(self.timestamps)
