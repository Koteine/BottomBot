"""Drag-to-select screen region widget."""

from __future__ import annotations

from PySide6.QtCore import QRect, Qt, Signal
from PySide6.QtGui import QColor, QMouseEvent, QPainter, QPen
from PySide6.QtWidgets import QApplication, QWidget, QRubberBand


class RegionSelector(QWidget):
    region_selected = Signal(dict)

    def __init__(self) -> None:
        super().__init__()
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setCursor(Qt.CrossCursor)
        self.setWindowState(Qt.WindowFullScreen)

        self.origin = None
        self.rubber_band = QRubberBand(QRubberBand.Rectangle, self)

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        self.origin = event.position().toPoint()
        self.rubber_band.setGeometry(QRect(self.origin, self.origin))
        self.rubber_band.show()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if self.origin:
            rect = QRect(self.origin, event.position().toPoint()).normalized()
            self.rubber_band.setGeometry(rect)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if not self.origin:
            return
        rect = QRect(self.origin, event.position().toPoint()).normalized()
        self.rubber_band.hide()
        self.hide()
        self.region_selected.emit(
            {
                "left": rect.left(),
                "top": rect.top(),
                "width": max(1, rect.width()),
                "height": max(1, rect.height()),
            }
        )
        self.deleteLater()

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(0, 0, 0, 80))
        painter.setPen(QPen(Qt.red, 2))
        painter.drawText(20, 40, "Drag to select capture region")


def start_region_selection(on_selected) -> None:
    selector = RegionSelector()
    selector.region_selected.connect(on_selected)
    selector.show()
    QApplication.processEvents()
