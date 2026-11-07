"""Real screen round-trip: a window paints the addon's cell grid, GDI capture reads it back.

Simulates what happens with WoW (minus WoW): proves the capture path, physical-pixel
geometry and decoder work on this machine's desktop/DWM setup.
"""

import sys
import time

import numpy as np
import pytest

from wvt import protocol as P

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows only")


def test_screen_capture_roundtrip():
    QtWidgets = pytest.importorskip("PySide6.QtWidgets")
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QImage, QPainter

    from wvt import winapi
    from wvt.signal_reader import ScreenCapture

    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    pkt = P.Packet(type=P.TYPE_HOVER, seq=42, left=30, bottom=150, width=420, height=15, font_height=13,
                   color=(255, 128, 64), phys_w=2560, phys_h=1440, chat_kind=10, prefix="[Bob] whispers: ",
                   text="can u summon pls? ty <3")
    img = P.render_cells(P.packet_cells(pkt))
    h, w, _ = img.shape
    rgba = np.dstack([img, np.full((h, w), 255, np.uint8)]).copy()
    qimg = QImage(rgba.data, w, h, w * 4, QImage.Format.Format_RGBA8888).copy()

    class Win(QtWidgets.QWidget):
        def paintEvent(self, e):  # noqa: N802
            p = QPainter(self)
            p.drawImage(0, 0, qimg)
            p.end()

    win = Win()
    win.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.Tool)
    dpr = app.primaryScreen().devicePixelRatio()
    win.setFixedSize(int(np.ceil(w / dpr)) + 2, int(np.ceil(h / dpr)) + 2)
    win.move(200, 200)
    win.show()
    deadline = time.time() + 1.0
    while time.time() < deadline:
        app.processEvents()
        time.sleep(0.02)
    try:
        x, y, cw, ch = winapi.client_rect_on_screen(int(win.winId()))
        if dpr != 1.0:
            pytest.skip(f"display scaling {dpr} – painting is scaled, covered by unit tests")
        cap = ScreenCapture()
        try:
            shot = cap.grab(x, y, P.REGION_W + 4, P.REGION_H + 4)
        finally:
            cap.close()
        out = P.try_decode(shot)
        assert out is not None, "captured image did not decode"
        assert out.text == pkt.text and out.prefix == pkt.prefix and out.seq == 42
    finally:
        win.close()
        app.processEvents()
