"""Shared data models for rhythm analysis."""

from __future__ import annotations

from dataclasses import dataclass, field
from collections import deque
from typing import Deque, List


@dataclass
class DetectionEvent:
    """Represents one vision-detected beat event."""

    timestamp: float
    strength: float


@dataclass
class HitResult:
    """Represents one keyboard hit evaluated against nearest beat."""

    timestamp: float
    offset_ms: float
    verdict: str


@dataclass
class RuntimeStats:
    """Simple rolling stats used by the UI."""

    hits: int = 0
    perfect: int = 0
    good: int = 0
    miss: int = 0
    offsets_ms: List[float] = field(default_factory=list)

    def add_hit(self, result: HitResult) -> None:
        self.hits += 1
        self.offsets_ms.append(result.offset_ms)
        if result.verdict == "Perfect":
            self.perfect += 1
        elif result.verdict == "Good":
            self.good += 1
        else:
            self.miss += 1

    @property
    def accuracy(self) -> float:
        if self.hits == 0:
            return 0.0
        return ((self.perfect + self.good) / self.hits) * 100.0

    @property
    def avg_offset(self) -> float:
        if not self.offsets_ms:
            return 0.0
        return sum(self.offsets_ms) / len(self.offsets_ms)


class BeatBuffer:
    """Thread-safe-ish bounded beat timestamp storage for nearest lookups."""

    def __init__(self, maxlen: int = 256) -> None:
        self._beats: Deque[float] = deque(maxlen=maxlen)

    def append(self, ts: float) -> None:
        self._beats.append(ts)

    def nearest(self, ts: float) -> float | None:
        if not self._beats:
            return None
        return min(self._beats, key=lambda b: abs(b - ts))

    def as_list(self) -> list[float]:
        return list(self._beats)
