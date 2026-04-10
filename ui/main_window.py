"""Main UI window and transparent overlay visualizer."""

from __future__ import annotations

import time
from collections import deque

import cv2
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QImage, QPainter, QPen, QColor, QPixmap
from PySide6.QtWidgets import (
    QCheckBox,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QPushButton,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from input.key_listener import KeyListener
from timing.beat_estimator import BeatEstimator
from utils.models import BeatBuffer, HitResult, RuntimeStats
from vision.beat_detector import BeatDetectorThread, DetectorConfig
from ui.region_selector import start_region_selection


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("BottomBot Rhythm Assistant")
        self.resize(1000, 700)

        self.detector: BeatDetectorThread | None = None
        self.key_listener = KeyListener(self.on_key_hit)
        self.estimator = BeatEstimator()
        self.beat_buffer = BeatBuffer()
        self.stats = RuntimeStats()
        self.current_feedback = "Waiting"
        self.pulses = deque(maxlen=24)

        self._build_ui()
        self.key_listener.start()

        self.ui_timer = QTimer(self)
        self.ui_timer.timeout.connect(self._tick_ui)
        self.ui_timer.start(33)

    def _build_ui(self) -> None:
        root = QWidget()
        self.setCentralWidget(root)
        layout = QVBoxLayout(root)

        controls = QHBoxLayout()
        self.select_region_btn = QPushButton("Select Region")
        self.select_region_btn.clicked.connect(self.select_region)

        self.calibrate_btn = QPushButton("Calibrate Tap")
        self.calibrate_btn.clicked.connect(self.calibrate_from_last_beat)

        self.threshold_slider = QSlider(Qt.Horizontal)
        self.threshold_slider.setRange(5, 100)
        self.threshold_slider.setValue(28)
        self.threshold_slider.valueChanged.connect(self.update_threshold)

        self.debug_check = QCheckBox("Debug Mode")
        self.debug_check.setChecked(True)
        self.debug_check.stateChanged.connect(self.toggle_debug)

        controls.addWidget(self.select_region_btn)
        controls.addWidget(self.calibrate_btn)
        controls.addWidget(QLabel("Sensitivity"))
        controls.addWidget(self.threshold_slider)
        controls.addWidget(self.debug_check)
        layout.addLayout(controls)

        self.overlay = OverlayCanvas()
        layout.addWidget(self.overlay, stretch=3)

        self.preview = QLabel("No preview")
        self.preview.setMinimumHeight(220)
        self.preview.setAlignment(Qt.AlignCenter)
        self.preview.setStyleSheet("background:#222;color:#ddd;")
        layout.addWidget(self.preview)

        self.stats_label = QLabel("BPM: 0 | Accuracy: 0% | Avg Offset: 0ms")
        layout.addWidget(self.stats_label)

    def select_region(self) -> None:
        start_region_selection(self.start_detector)

    def start_detector(self, region: dict) -> None:
        if self.detector:
            self.detector.stop()
        config = DetectorConfig(threshold=self.threshold_slider.value(), debug=self.debug_check.isChecked())
        self.detector = BeatDetectorThread(region, config)
        self.detector.beat_detected.connect(self.on_beat_detected)
        self.detector.frame_ready.connect(self.on_frame_ready)
        self.detector.start()

    def update_threshold(self, value: int) -> None:
        if self.detector:
            self.detector.update_threshold(value)

    def toggle_debug(self) -> None:
        if self.detector:
            self.detector.set_debug(self.debug_check.isChecked())

    def on_beat_detected(self, ts: float, strength: float) -> None:
        self.estimator.add_event(ts)
        self.beat_buffer.append(ts)
        self.pulses.append((ts, strength))
        self.overlay.push_beat(ts)

    def on_key_hit(self, ts: float, key_name: str) -> None:
        nearest = self.beat_buffer.nearest(ts)
        if nearest is None:
            return
        offset_ms = (ts - nearest) * 1000
        abs_offset = abs(offset_ms)
        if abs_offset <= 30:
            verdict = "Perfect"
        elif abs_offset <= 80:
            verdict = "Good"
        else:
            verdict = "Miss"

        result = HitResult(timestamp=ts, offset_ms=offset_ms, verdict=verdict)
        self.stats.add_hit(result)
        self.current_feedback = f"{verdict} ({offset_ms:+.1f}ms) [{key_name}]"
        self.overlay.set_feedback(verdict)

    def calibrate_from_last_beat(self) -> None:
        beats = self.estimator.recent_beats()
        if not beats:
            return
        self.estimator.calibrate_with_taps(beats[-1], time.perf_counter())

    def on_frame_ready(self, frame) -> None:
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        h, w, ch = rgb.shape
        image = QImage(rgb.data, w, h, ch * w, QImage.Format_RGB888)
        pixmap = QPixmap.fromImage(image).scaled(
            self.preview.width(), self.preview.height(), Qt.KeepAspectRatio, Qt.SmoothTransformation
        )
        self.preview.setPixmap(pixmap)

    def _tick_ui(self) -> None:
        bpm = self.estimator.get_bpm()
        self.stats_label.setText(
            f"BPM: {bpm:.1f} | Accuracy: {self.stats.accuracy:.1f}% | "
            f"Avg Offset: {self.stats.avg_offset:+.1f}ms | Feedback: {self.current_feedback}"
        )
        self.overlay.set_pulses(self.pulses)
        self.overlay.update()

    def closeEvent(self, event) -> None:  # noqa: N802
        if self.detector:
            self.detector.stop()
        self.key_listener.stop()
        super().closeEvent(event)


class OverlayCanvas(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.feedback = "Waiting"
        self.last_beats = deque(maxlen=20)

    def push_beat(self, ts: float) -> None:
        self.last_beats.append(ts)

    def set_feedback(self, verdict: str) -> None:
        self.feedback = verdict

    def set_pulses(self, pulses) -> None:
        self.last_beats = deque((p[0] for p in pulses), maxlen=20)

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(18, 18, 18))

        w, h = self.width(), self.height()
        center_y = h // 2

        painter.setPen(QPen(QColor(100, 100, 255), 3))
        painter.drawLine(40, center_y, w - 40, center_y)  # timing bar

        perfect_rect_x = w // 2 - 30
        painter.fillRect(perfect_rect_x, center_y - 15, 60, 30, QColor(0, 255, 120, 120))

        now = time.perf_counter()
        painter.setPen(QPen(QColor(255, 220, 0), 2))
        for beat_ts in self.last_beats:
            age = now - beat_ts
            x = int((w // 2) - age * 320)
            if 40 <= x <= w - 40:
                painter.drawEllipse(x - 8, center_y - 8, 16, 16)

        color = {"Perfect": QColor(0, 255, 120), "Good": QColor(255, 220, 0), "Miss": QColor(255, 80, 80)}.get(
            self.feedback, QColor(220, 220, 220)
        )
        painter.setPen(QPen(color, 2))
        painter.drawText(40, 40, f"Feedback: {self.feedback}")
