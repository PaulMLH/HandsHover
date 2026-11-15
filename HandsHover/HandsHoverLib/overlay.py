"""Displays in the Slicer window: mode banner, frame around the controlled view,
and hand skeleton drawn on the camera preview."""

import qt
import slicer

from .gestures import ACTION_LABELS, HOLD_LABELS
from .hands import HAND_CONNECTIONS, POSE_LABELS

MODE_COLORS = {
    "3D": "#5b7083",
    "Red": "#c0392b",
    "Green": "#27ae60",
    "Yellow": "#c9a400",
    "Pause": "#7f8c8d",
    "Off": "#95a5a6",
}

MODE_TEXT = {
    "3D": "3D",
    "Red": "RED",
    "Green": "GREEN",
    "Yellow": "YELLOW",
}

ROLE_COLORS = {"C": "#f39c12", "S": "#3498db"}   # orange = controlling hand, blue = other hand


class ModeBanner:
    """Toolbar at the top of the window, on its own row, always visible."""

    def __init__(self):
        mw = slicer.util.mainWindow()
        self.toolbar = qt.QToolBar("HandsHover")
        self.toolbar.setObjectName("HandsHoverToolBar")
        self.toolbar.setMovable(False)
        self.toolbar.setFloatable(False)

        self.label = qt.QLabel()
        self.label.setSizePolicy(qt.QSizePolicy.Expanding, qt.QSizePolicy.Preferred)
        self.label.setAlignment(qt.Qt.AlignCenter)
        self.label.setMinimumHeight(34)

        self.gauge = qt.QProgressBar()
        self.gauge.setRange(0, 100)
        self.gauge.setFixedWidth(220)
        self.gauge.setTextVisible(True)
        self.gauge.setVisible(False)

        self.handsLabel = qt.QLabel()
        self.handsLabel.setMinimumWidth(260)
        self.handsLabel.setStyleSheet("padding-left: 8px; padding-right: 8px;")

        self.toolbar.addWidget(self.label)
        self.toolbar.addWidget(self.gauge)
        self.toolbar.addWidget(self.handsLabel)
        mw.addToolBarBreak(qt.Qt.TopToolBarArea)
        mw.addToolBar(qt.Qt.TopToolBarArea, self.toolbar)
        self.setOff()

    def _style(self, color):
        self.label.setStyleSheet(
            f"background-color: {color}; color: white; font-size: 18px; font-weight: bold;"
            " border-radius: 4px; padding: 4px 16px;")

    def setOff(self, text="HANDSHOVER OFF"):
        self._style(MODE_COLORS["Off"])
        self.label.setText(text)
        self.gauge.setVisible(False)
        self.handsLabel.setText("")

    def setMode(self, mode, paused, action=None):
        text = MODE_TEXT.get(mode, mode)
        if paused:
            self._style(MODE_COLORS["Pause"])
            self.label.setText(f"PAUSED  ({text})  –  two fists for 1 s to resume")
            return
        self._style(MODE_COLORS.get(mode, MODE_COLORS["Off"]))
        if action:
            text += f"   ·   {ACTION_LABELS.get(action, action)}"
        self.label.setText(text)

    def setProgress(self, key, fraction):
        if not key or fraction <= 0.0:
            self.gauge.setVisible(False)
            return
        self.gauge.setVisible(True)
        self.gauge.setValue(int(round(min(fraction, 1.0) * 100)))
        self.gauge.setFormat(HOLD_LABELS.get(key, key))

    def setHands(self, info, fps=None):
        parts = []
        for side, name in (("C", "Hand"), ("S", "Other hand")):
            hand = info.get(side)
            if hand is None or hand.get("pose") is None:
                parts.append(f"{name}: –")
            else:
                parts.append(f"{name}: {POSE_LABELS.get(hand['pose'], hand['pose'])}")
        if fps:
            parts.append(f"{fps:.0f} fps")
        self.handsLabel.setText("   ".join(parts))

    def remove(self):
        mw = slicer.util.mainWindow()
        if mw and self.toolbar:
            mw.removeToolBar(self.toolbar)
            self.toolbar.deleteLater()
        self.toolbar = None


class ViewHighlighter:
    """Coloured frame, transparent to clicks, laid over the controlled view."""

    def __init__(self):
        self.frame = None
        self.target = None
        self.view = None
        self.color = None

    @staticmethod
    def _widgetFor(view):
        lm = slicer.app.layoutManager()
        if lm is None:
            return None
        if view == "3D":
            return lm.threeDWidget(0) if lm.threeDViewCount > 0 else None
        return lm.sliceWidget(view)

    def show(self, view, color):
        try:
            widget = self._widgetFor(view)
            if widget is None or not widget.isVisible():
                self.hide()
                return
            if self.frame is None or self.view != view:
                self._destroy()
                self.frame = qt.QFrame(widget)
                self.frame.setObjectName("HandsHoverHighlight")
                self.frame.setAttribute(qt.Qt.WA_TransparentForMouseEvents)
                self.target = widget
                self.view = view
                self.color = None
            if color != self.color:
                self.frame.setStyleSheet(
                    f"QFrame#HandsHoverHighlight {{ border: 5px solid {color}; background: transparent; }}")
                self.color = color
            self.refresh()
            self.frame.show()
            self.frame.raise_()
        except Exception:
            # View deleted by a layout change: recreated on the next call
            self.frame = None
            self.target = None
            self.view = None

    def refresh(self):
        if self.frame is None or self.target is None:
            return
        try:
            w, h = self.target.width, self.target.height
            if self.frame.width != w or self.frame.height != h:
                self.frame.setGeometry(0, 0, w, h)
                self.frame.raise_()
        except Exception:
            self.frame = None
            self.target = None
            self.view = None

    def hide(self):
        if self.frame is not None:
            try:
                self.frame.hide()
            except Exception:
                self.frame = None
                self.target = None
                self.view = None

    def _destroy(self):
        if self.frame is not None:
            try:
                self.frame.hide()
                self.frame.setParent(None)
                self.frame.deleteLater()
            except Exception:
                pass
        self.frame = None
        self.target = None
        self.view = None

    def remove(self):
        self._destroy()


def drawHandsOnPixmap(pixmap, assigned, info):
    """Draws the hand skeletons (orange = controlling hand, blue = other hand)."""
    w, h = pixmap.width(), pixmap.height()
    painter = qt.QPainter(pixmap)
    try:
        painter.setRenderHint(qt.QPainter.Antialiasing)
        for side, hand in assigned.items():
            color = qt.QColor(ROLE_COLORS.get(side, "#ffffff"))
            pen = qt.QPen(color)
            pen.setWidth(2)
            painter.setPen(pen)
            pts = [(int(p[0] * w), int(p[1] * h)) for p in hand["lm"]]
            for a, b in HAND_CONNECTIONS:
                painter.drawLine(pts[a][0], pts[a][1], pts[b][0], pts[b][1])
            for x, y in pts:
                painter.drawEllipse(qt.QPoint(x, y), 2, 2)
            pose = (info.get(side) or {}).get("pose")
            label = ("" if side == "C" else "other: ") + POSE_LABELS.get(pose, "…")
            painter.drawText(pts[0][0] + 6, pts[0][1] + 14, label)
    finally:
        painter.end()
    return pixmap
