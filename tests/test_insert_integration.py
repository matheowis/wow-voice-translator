"""Real keystroke test of ChatInserter against a WoW-like chat box.

Injects "/" and Ctrl+V, so it only runs when WVT_INTERACTIVE_TESTS=1 and only types
when its own window is in the foreground. The clipboard is restored afterwards.
"""

import os
import sys
import threading
import time

import pytest

pytestmark = pytest.mark.skipif(
    sys.platform != "win32" or os.environ.get("WVT_INTERACTIVE_TESTS") != "1",
    reason="interactive: set WVT_INTERACTIVE_TESTS=1",
)


def test_slash_paste_into_chat_box():
    QtWidgets = pytest.importorskip("PySide6.QtWidgets")
    from PySide6.QtCore import Qt

    from wvt import winapi
    from wvt.chat_insert import ChatInserter, build_chat_line, get_clipboard_text, set_clipboard_text

    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    class FakeWow(QtWidgets.QWidget):
        """Mimics WoW: '/' opens the chat input with '/' typed; Ctrl+V pastes."""

        def __init__(self):
            super().__init__()
            self.setWindowTitle("WVT insert test")
            self.resize(500, 120)
            self.edit = QtWidgets.QLineEdit(self)
            self.edit.setGeometry(10, 60, 480, 30)
            self.edit.hide()
            self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

        def keyPressEvent(self, e):  # noqa: N802
            if e.text() == "/" and not self.edit.isVisible():
                self.edit.show()
                self.edit.setText("/")
                self.edit.setFocus()
                return
            super().keyPressEvent(e)

    saved = get_clipboard_text()
    win = FakeWow()
    win.show()
    win.raise_()
    win.activateWindow()
    hwnd = int(win.winId())
    winapi.user32.SetForegroundWindow(hwnd)
    t_end = time.time() + 1.5
    while time.time() < t_end:
        app.processEvents()
        time.sleep(0.01)
    try:
        if winapi.foreground_window() != hwnd:
            pytest.skip("test window could not take the foreground")
        line = build_chat_line("/p", "Hello from the voice translator test")
        result = {}
        th = threading.Thread(target=lambda: result.update(status=ChatInserter("slash", 120).insert(line, True)))
        th.start()
        t_end = time.time() + 2.5
        while time.time() < t_end:
            app.processEvents()
            time.sleep(0.01)
        th.join(1)
        assert result.get("status") == "inserted"
        assert win.edit.text() == line
    finally:
        win.close()
        app.processEvents()
        if saved is not None:
            set_clipboard_text(saved)
