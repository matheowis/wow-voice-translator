"""Developer tool: a fake "WoW" window that behaves like the addon.

    python -m wvt.devsim            # then start the app with:  python -m wvt --sim

It draws a chat box with English lines; when the mouse rests on a line it paints the
same pixel signal as the real addon (top-left of the client area). Lets you test the
capture -> decode -> translate -> overlay path without the game.
"""

from __future__ import annotations

import sys
import time

import numpy as np
from PySide6.QtCore import QRect, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QFontMetrics, QImage, QPainter
from PySide6.QtWidgets import QApplication, QWidget

from . import protocol as P

SIM_TITLE = "WVT symulator WoW"

LINES = [
    ("[Party] [Anna]: ", "LFM Deadmines need tank and heals", (170, 170, 255), 3),
    ("[Party] [Bob]: ", "oom, drinking, don't pull yet", (170, 170, 255), 3),
    ("[Trade] [Kai]: ", "WTS [Linen Cloth] x20 50s, pst", (255, 192, 192), 14),
    ("[Guild] [Mira]: ", "gtg, gn all", (64, 255, 64), 8),
    ("Zoe whispers: ", "can u summon pls? ty", (255, 128, 255), 10),
    ("[Party] [Ty]: ", "Siema, dzięki za grupę!", (170, 170, 255), 3),
]


class SimWindow(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle(SIM_TITLE)
        self.resize(1280, 720)
        self.setMouseTracking(True)
        self.font_px = 18
        self.hover_idx: int | None = None
        self.hover_since = 0.0
        self.sent_at = 0.0
        self.seq = 0
        self.cells: list[int] | None = None
        self.keep = False
        t = QTimer(self)
        t.timeout.connect(self.tick)
        t.start(50)

    def line_rects(self) -> list[QRect]:
        fm = QFontMetrics(self._font())
        h = fm.height() + 2
        x, w = 30, 520
        bottom = self.height() - 120
        rects = []
        for i in range(len(LINES)):
            y = bottom - (len(LINES) - i) * h
            rects.append(QRect(x, y, w, h))
        return rects

    def _font(self) -> QFont:
        f = QFont("Arial Narrow")
        f.setPixelSize(self.font_px)
        return f

    def mouseMoveEvent(self, e):  # noqa: N802
        pos = e.position().toPoint()
        idx = next((i for i, r in enumerate(self.line_rects()) if r.contains(pos)), None)
        if idx != self.hover_idx:
            self.hover_idx = idx
            self.hover_since = time.monotonic()
            self.cells = None
            self.keep = False
            self.update()

    def leaveEvent(self, e):  # noqa: N802
        self.hover_idx = None
        self.cells = None
        self.update()

    def tick(self) -> None:
        now = time.monotonic()
        if self.hover_idx is None:
            return
        if self.cells is None and now - self.hover_since >= 0.12:
            self.seq = (self.seq + 1) % 65536
            r = self.line_rects()[self.hover_idx]
            dpr = self.devicePixelRatioF()
            ch = self.height() * dpr
            k = 768.0 / ch  # physical px -> base units
            prefix, text, color, kind = LINES[self.hover_idx]
            pkt = P.Packet(type=P.TYPE_HOVER, seq=self.seq, left=r.x() * dpr * k,
                           bottom=(self.height() - r.bottom() - 1) * dpr * k, width=r.width() * dpr * k,
                           height=r.height() * dpr * k, font_height=self.font_px * dpr * k, color=color,
                           phys_w=int(self.width() * dpr), phys_h=int(ch), chat_kind=kind, prefix=prefix, text=text)
            self.cells = P.packet_cells(pkt)
            self.sent_at = now
            self.update()
        elif self.cells is not None and not self.keep and now - self.sent_at > 0.6:
            self.cells = P.packet_cells(P.Packet(type=P.TYPE_KEEP, seq=self.seq))
            self.keep = True
            self.update()

    def paintEvent(self, e):  # noqa: N802
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(40, 52, 40))
        p.setPen(QColor(200, 200, 200))
        p.setFont(QFont("Segoe UI", 12))
        p.drawText(QRect(0, 20, self.width(), 30), Qt.AlignmentFlag.AlignHCenter,
                   "Symulator WoW – najedź na linię czatu (aplikacja: python -m wvt --sim)")
        chat = self.line_rects()
        p.fillRect(chat[0].united(chat[-1]).adjusted(-6, -6, 6, 6), QColor(0, 0, 0, 110))
        p.setFont(self._font())
        for r, (prefix, text, color, _k) in zip(chat, LINES):
            p.setPen(QColor(*color))
            p.drawText(r, int(Qt.AlignmentFlag.AlignVCenter), prefix + text)
        if self.cells:
            img = P.render_cells(self.cells[: P.MAX_CELLS])
            rows = (len(self.cells) + P.COLS - 1) // P.COLS
            img = img[: rows * P.CELL_PX]
            h, w, _ = img.shape
            rgba = np.dstack([img, np.full((h, w), 255, np.uint8)]).copy()
            qimg = QImage(rgba.data, w, h, w * 4, QImage.Format.Format_RGBA8888).copy()
            dpr = self.devicePixelRatioF()
            qimg.setDevicePixelRatio(dpr)  # draw 1:1 in physical pixels
            # only cells that are part of the packet are drawn (like the addon)
            n = len(self.cells)
            full_rows, rest = divmod(n, P.COLS)
            p.drawImage(0, 0, qimg, 0, 0, w, full_rows * P.CELL_PX)
            if rest:
                p.drawImage(0, full_rows * P.CELL_PX / dpr, qimg, 0, full_rows * P.CELL_PX, rest * P.CELL_PX, P.CELL_PX)
        p.end()


def main() -> int:
    import argparse
    from pathlib import Path

    ap = argparse.ArgumentParser()
    ap.add_argument("--auto", action="store_true", help="hover the lines one by one without the mouse")
    ap.add_argument("--shots", help="directory for screenshots taken while auto-hovering")
    ap.add_argument("--dwell", type=float, default=3.0)
    args = ap.parse_args()
    app = QApplication(sys.argv)
    w = SimWindow()
    w.show()
    if args.auto:
        state = {"i": -1}
        shots = Path(args.shots) if args.shots else None
        if shots:
            shots.mkdir(parents=True, exist_ok=True)

        def shot():
            if not shots:
                return
            from . import winapi
            from .signal_reader import ScreenCapture

            x, y, cw, ch = winapi.client_rect_on_screen(int(w.winId()))
            cap = ScreenCapture()
            img = cap.grab(x, y, cw, ch)
            cap.close()
            h, wd, _ = img.shape
            q = QImage(np.ascontiguousarray(img).data, wd, h, wd * 3, QImage.Format.Format_RGB888).copy()
            q.save(str(shots / f"hover_{state['i']}.png"))

        def step():
            state["i"] += 1
            if state["i"] >= len(LINES):
                app.quit()
                return
            w.hover_idx = state["i"]
            w.hover_since = time.monotonic()
            w.cells = None
            w.keep = False
            w.update()
            QTimer.singleShot(int(args.dwell * 1000) - 300, shot)

        w.setMouseTracking(False)
        w.mouseMoveEvent = lambda e: None  # ignore the real mouse
        w.leaveEvent = lambda e: None
        QTimer.singleShot(1500, step)
        t = QTimer(w)
        t.timeout.connect(step)
        QTimer.singleShot(1500, lambda: t.start(int(args.dwell * 1000)))
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
