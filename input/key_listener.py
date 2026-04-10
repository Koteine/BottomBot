"""Global keyboard listener for rhythm hit analysis."""

from __future__ import annotations

import time
from typing import Callable

from pynput import keyboard


class KeyListener:
    def __init__(self, on_hit: Callable[[float, str], None]) -> None:
        self.on_hit = on_hit
        self.listener: keyboard.Listener | None = None

    def _on_press(self, key: keyboard.KeyCode | keyboard.Key) -> None:
        try:
            key_name = key.char if hasattr(key, "char") and key.char else str(key)
        except Exception:
            key_name = str(key)
        self.on_hit(time.perf_counter(), key_name)

    def start(self) -> None:
        if self.listener:
            return
        self.listener = keyboard.Listener(on_press=self._on_press)
        self.listener.daemon = True
        self.listener.start()

    def stop(self) -> None:
        if self.listener:
            self.listener.stop()
            self.listener = None
