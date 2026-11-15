"""Pointeur et boutons de souris pour le mode souris.

- Déplacement : QCursor.setPos (Qt gère la mise à l'échelle des écrans haute densité).
- Clics et molette :
    * Windows : événements système (user32.mouse_event), fiables partout dans Slicer,
      y compris menus et boîtes de dialogue ;
    * autres systèmes : événements Qt envoyés au widget sous le pointeur
      (expérimental, molette non prise en charge).
Le pointeur est toujours limité à la fenêtre principale de Slicer.
"""

import logging
import sys

import qt
import slicer

_IS_WINDOWS = sys.platform.startswith("win")

_WIN_FLAGS = {
    ("left", "down"): 0x0002,
    ("left", "up"): 0x0004,
    ("right", "down"): 0x0008,
    ("right", "up"): 0x0010,
}
_WIN_WHEEL = 0x0800
_WHEEL_DELTA = 120


class MouseController:
    def __init__(self):
        self._pressed = set()
        self._qtTarget = None
        self._user32 = None
        if _IS_WINDOWS:
            try:
                import ctypes
                self._user32 = ctypes.windll.user32
            except Exception:
                logging.warning("HandsHover : accès souris Windows indisponible, repli sur Qt.")
                self._user32 = None
        self._warnedWheel = False

    # ----------------------------------------------------------- outils
    @staticmethod
    def windowRect():
        mw = slicer.util.mainWindow()
        topLeft = mw.mapToGlobal(qt.QPoint(0, 0))
        return topLeft.x(), topLeft.y(), mw.width, mw.height

    # ------------------------------------------------------------ actions
    def execute(self, event):
        kind = event[0]
        try:
            if kind == "pointer":
                self.moveTo(event[1], event[2])
            elif kind == "mouse_button":
                self.button(event[1], event[2] == "down")
            elif kind == "mouse_click":
                self.button(event[1], True)
                self.button(event[1], False)
            elif kind == "wheel":
                self.wheel(event[1])
        except Exception:
            logging.exception("HandsHover : échec de l'action souris %s", kind)

    def moveTo(self, u, v):
        x0, y0, w, h = self.windowRect()
        x = int(round(x0 + u * max(w - 1, 1)))
        y = int(round(y0 + v * max(h - 1, 1)))
        qt.QCursor.setPos(x, y)
        if self._user32 is None and self._qtTarget is not None:
            self._sendQtMouse(qt.QEvent.MouseMove, self._qtTarget, None, pressed=True)

    def button(self, which, down):
        key = which
        if down:
            self._pressed.add(key)
        else:
            self._pressed.discard(key)
        if self._user32 is not None:
            self._user32.mouse_event(_WIN_FLAGS[(which, "down" if down else "up")], 0, 0, 0, 0)
            return
        pos = qt.QCursor.pos()
        widget = qt.QApplication.widgetAt(pos) if down else (self._qtTarget or qt.QApplication.widgetAt(pos))
        if widget is None:
            return
        etype = qt.QEvent.MouseButtonPress if down else qt.QEvent.MouseButtonRelease
        self._sendQtMouse(etype, widget, which, pressed=down)
        self._qtTarget = widget if down else None

    def wheel(self, notches):
        if not notches:
            return
        if self._user32 is not None:
            self._user32.mouse_event(_WIN_WHEEL, 0, 0, int(notches) * _WHEEL_DELTA, 0)
        elif not self._warnedWheel:
            self._warnedWheel = True
            logging.warning("HandsHover : la molette n'est prise en charge que sous Windows pour l'instant.")

    def releaseAll(self):
        for which in list(self._pressed):
            self.button(which, False)
        self._pressed.clear()
        self._qtTarget = None

    # ---------------------------------------------------------- repli Qt
    def _sendQtMouse(self, etype, widget, which, pressed):
        pos = qt.QCursor.pos()
        local = widget.mapFromGlobal(pos)
        qbutton = qt.Qt.NoButton
        if which == "left":
            qbutton = qt.Qt.LeftButton
        elif which == "right":
            qbutton = qt.Qt.RightButton
        held = qt.Qt.NoButton
        if pressed:
            held = qt.Qt.LeftButton if "left" in self._pressed else qt.Qt.RightButton
        event = qt.QMouseEvent(etype, qt.QPointF(local), qt.QPointF(pos), qbutton, held, qt.Qt.NoModifier)
        qt.QApplication.sendEvent(widget, event)
