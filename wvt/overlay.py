"""Click-through, non-activating overlay windows drawn on top of WoW.

* HoverOverlay – the Polish translation painted exactly over the hovered chat line.
* HudOverlay   – small status pill (recording / processing / result / error).

Works with WoW in windowed or "windowed (fullscreen)" mode; exclusive fullscreen
would hide any overlay.
"""

from __future__ import annotations

import ctypes
import html
import time
from typing import Optional

from PySide6.QtCore import QPoint, QRect, QRectF, Qt, QTimer
from PySide6.QtGui import (QColor, QFont, QFontMetrics, QGuiApplication, QPainter, QPainterPath, QPen,
                           QTextDocument)
from PySide6.QtWidgets import QWidget

from . import winapi

GWL_EXSTYLE = -20
WS_EX_NOACTIVATE = 0x08000000
WS_EX_TRANSPARENT = 0x00000020
WS_EX_LAYERED = 0x00080000
WS_EX_TOOLWINDOW = 0x00000080


def _make_click_through(widget: QWidget) -> None:
    if not winapi.IS_WINDOWS:
        return
    hwnd = int(widget.winId())
    get = winapi.user32.GetWindowLongPtrW if hasattr(winapi.user32, "GetWindowLongPtrW") else winapi.user32.GetWindowLongW
    set_ = winapi.user32.SetWindowLongPtrW if hasattr(winapi.user32, "SetWindowLongPtrW") else winapi.user32.SetWindowLongW
    get.restype = ctypes.c_ssize_t
    get.argtypes = [ctypes.c_void_p, ctypes.c_int]
    set_.restype = ctypes.c_ssize_t
    set_.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_ssize_t]
    style = get(hwnd, GWL_EXSTYLE)
    set_(hwnd, GWL_EXSTYLE, style | WS_EX_NOACTIVATE | WS_EX_TRANSPARENT | WS_EX_LAYERED | WS_EX_TOOLWINDOW)


def physical_to_logical(x: int, y: int) -> tuple[float, float, float]:
    """Physical screen pixels -> Qt logical coordinates (+ device pixel ratio)."""
    for screen in QGuiApplication.screens():
        dpr = screen.devicePixelRatio()
        g = screen.geometry()
        nx, ny = g.x() * dpr, g.y() * dpr
        nw, nh = g.width() * dpr, g.height() * dpr
        if nx <= x < nx + nw and ny <= y < ny + nh:
            return g.x() + (x - nx) / dpr, g.y() + (y - ny) / dpr, dpr
    screen = QGuiApplication.primaryScreen()
    dpr = screen.devicePixelRatio() if screen else 1.0
    return x / dpr, y / dpr, dpr


class _OverlayBase(QWidget):
    def __init__(self) -> None:
        super().__init__(None)
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowTransparentForInput
            | Qt.WindowType.WindowDoesNotAcceptFocus
            | Qt.WindowType.NoDropShadowWindowHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self._styled = False

    def showEvent(self, event):  # noqa: N802 (Qt naming)
        super().showEvent(event)
        if not self._styled:
            _make_click_through(self)
            self._styled = True


class HoverOverlay(_OverlayBase):
    PAD = 4

    def __init__(self, font_family: str = "Arial Narrow", font_scale: float = 1.0, opacity: float = 0.9):
        super().__init__()
        self.font_family = font_family
        self.font_scale = font_scale
        self.opacity = opacity
        self.prefix = ""
        self.text = ""
        self.note = ""
        self.color = QColor(255, 255, 255)
        self._font = QFont(font_family)
        self._anchor_bottom = 0.0

    def show_text(self, rect_px: tuple[int, int, int, int], font_px: float, color: tuple[int, int, int],
                  prefix: str, text: str, note: str = "") -> None:
        """rect_px: (x, y, w, h) physical screen pixels of the chat line."""
        x, y, w, h = rect_px
        lx, ly, dpr = physical_to_logical(x, y)
        lw, lh = w / dpr, h / dpr
        size = max(9.0, font_px * self.font_scale / dpr)
        self._font = QFont(self.font_family)
        self._font.setPixelSize(int(round(size)))
        self.prefix, self.text, self.note = prefix, text, note
        self.color = QColor(*color)
        inner_w = max(40, int(lw))
        self._doc = self._build_doc(inner_w)
        doc_h = int(self._doc.size().height())
        box_h = max(int(lh), doc_h) + 2 * self.PAD
        box_w = inner_w + 2 * self.PAD
        bottom = ly + lh + self.PAD
        self.setGeometry(int(lx - self.PAD), int(bottom - box_h), box_w, box_h)
        self.update()
        if not self.isVisible():
            self.show()
        self.raise_()

    def _build_doc(self, width: int) -> QTextDocument:
        doc = QTextDocument()
        doc.setDefaultFont(self._font)
        doc.setDocumentMargin(0)
        c = self.color
        body_col = f"rgb({c.red()},{c.green()},{c.blue()})"
        parts = []
        if self.prefix:
            parts.append(f'<span style="color:{body_col}; opacity:0.8">{html.escape(self.prefix)}</span>')
        parts.append(f'<span style="color:{body_col}">{html.escape(self.text or "")}</span>')
        if self.note:
            parts.append(f'&nbsp;&nbsp;<span style="color:#9aa0a6">{html.escape(self.note)}</span>')
        doc.setHtml("".join(parts))
        doc.setTextWidth(width)
        return doc

    def paintEvent(self, event):  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        path = QPainterPath()
        path.addRoundedRect(r, 4, 4)
        p.fillPath(path, QColor(12, 12, 16, int(255 * self.opacity)))
        p.setPen(QPen(QColor(255, 209, 0, 140), 1))
        p.drawPath(path)
        doc = getattr(self, "_doc", None)
        if doc is not None:
            doc_h = doc.size().height()
            top = self.height() - self.PAD - doc_h  # text sits on the line's baseline like chat
            p.translate(self.PAD, max(self.PAD, top))
            doc.drawContents(p)
        p.end()


class HudOverlay(_OverlayBase):
    """Status pill shown near the top of the WoW window."""

    COLORS = {
        "rec": QColor(220, 40, 40),
        "busy": QColor(255, 170, 0),
        "ok": QColor(60, 200, 90),
        "info": QColor(90, 160, 255),
        "error": QColor(255, 70, 70),
    }

    def __init__(self, position: str = "top_center"):
        super().__init__()
        self.position = position
        self.kind = "info"
        self.title = ""
        self.lines: list[str] = []
        self.level = 0.0
        self.started = 0.0
        self._anchor: Optional[tuple[int, int, int, int]] = None
        self._hide_timer = QTimer(self)
        self._hide_timer.setSingleShot(True)
        self._hide_timer.timeout.connect(self.hide)
        self._tick = QTimer(self)
        self._tick.setInterval(100)
        self._tick.timeout.connect(self.update)
        self._title_font = QFont("Segoe UI", 11, QFont.Weight.DemiBold)
        self._line_font = QFont("Segoe UI", 11)

    def set_anchor(self, client_rect: Optional[tuple[int, int, int, int]]) -> None:
        self._anchor = client_rect

    def show_status(self, kind: str, title: str, lines: Optional[list[str]] = None, timeout: float = 0.0) -> None:
        self.kind, self.title, self.lines = kind, title, list(lines or [])
        if kind == "rec":
            self.started = time.monotonic()
            self._tick.start()
        else:
            self._tick.stop()
        self._relayout()
        self.update()
        if not self.isVisible():
            self.show()
        self.raise_()
        if timeout > 0:
            self._hide_timer.start(int(timeout * 1000))
        else:
            self._hide_timer.stop()

    def _relayout(self) -> None:
        fm_t = QFontMetrics(self._title_font)
        fm_l = QFontMetrics(self._line_font)
        max_w = 900
        width = fm_t.horizontalAdvance(self.title) + 60
        height = fm_t.height() + 16
        for ln in self.lines:
            br = fm_l.boundingRect(QRect(0, 0, max_w, 1000), int(Qt.TextFlag.TextWordWrap), ln)
            width = max(width, br.width() + 32)
            height += br.height() + 4
        width = min(max_w + 32, max(260, width))
        if self._anchor:
            ax, ay, aw, ah = self._anchor
            lx, ly, dpr = physical_to_logical(ax, ay)
            law, lah = aw / dpr, ah / dpr
        else:
            screen = QGuiApplication.primaryScreen().geometry()
            lx, ly, law, lah = screen.x(), screen.y(), screen.width(), screen.height()
        x = lx + (law - width) / 2
        y = ly + (lah * 0.08 if self.position == "top_center" else lah * 0.72)
        self.setGeometry(int(x), int(y), int(width), int(height))

    def paintEvent(self, event):  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        path = QPainterPath()
        path.addRoundedRect(r, 10, 10)
        p.fillPath(path, QColor(16, 16, 22, 225))
        accent = self.COLORS.get(self.kind, self.COLORS["info"])
        p.setPen(QPen(accent, 2))
        p.drawPath(path)
        # status dot
        dot = QColor(accent)
        if self.kind == "rec" and int(time.monotonic() * 2) % 2:
            dot.setAlpha(120)
        p.setBrush(dot)
        p.setPen(Qt.PenStyle.NoPen)
        p.drawEllipse(QPoint(18, 18), 6, 6)
        p.setFont(self._title_font)
        p.setPen(QColor(240, 240, 240))
        title = self.title
        if self.kind == "rec":
            title = f"{self.title}  {time.monotonic() - self.started:4.1f}s"
        p.drawText(QRect(32, 6, self.width() - 40, 24), int(Qt.AlignmentFlag.AlignVCenter), title)
        if self.kind == "rec":
            # microphone level bar
            w = self.width() - 64
            lvl = min(1.0, self.level * 12)
            p.setBrush(QColor(60, 60, 70))
            p.drawRoundedRect(QRectF(32, 30, w, 5), 2, 2)
            p.setBrush(QColor(80, 220, 120))
            p.drawRoundedRect(QRectF(32, 30, w * lvl, 5), 2, 2)
        p.setFont(self._line_font)
        y = 32 if self.kind != "rec" else 40
        fm = QFontMetrics(self._line_font)
        for ln in self.lines:
            br = fm.boundingRect(QRect(0, 0, self.width() - 32, 1000), int(Qt.TextFlag.TextWordWrap), ln)
            p.setPen(QColor(210, 210, 215))
            p.drawText(QRect(16, y, self.width() - 32, br.height()), int(Qt.TextFlag.TextWordWrap), ln)
            y += br.height() + 4
        p.end()
