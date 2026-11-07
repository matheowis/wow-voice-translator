"""Putting the translated text into the WoW chat input — never sending it.

Default method "slash":
  1. press the "/" key  -> WoW opens the chat box with "/" typed
  2. paste "p <text>"   -> the box contains "/p <text>"; WoW's ChatEdit ParseText switches
                           the box to the Party channel right away (verified in the 1.60.1 UI
                           source: OnTextChanged -> ParseText(0) -> HandleChatType)
  3. the player reads it and presses Enter (or Esc to discard).

If the chat box was already open, "/" is simply typed into it and nothing is sent.
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import logging
import time

from . import winapi

log = logging.getLogger(__name__)

CF_UNICODETEXT = 13
GMEM_MOVEABLE = 0x0002

if winapi.IS_WINDOWS:
    _u = winapi.user32
    _k = winapi.kernel32
    _u.OpenClipboard.argtypes = [wt.HWND]
    _u.GetClipboardData.argtypes = [wt.UINT]
    _u.GetClipboardData.restype = wt.HANDLE
    _u.SetClipboardData.argtypes = [wt.UINT, wt.HANDLE]
    _u.SetClipboardData.restype = wt.HANDLE
    _u.IsClipboardFormatAvailable.argtypes = [wt.UINT]
    _k.GlobalAlloc.argtypes = [wt.UINT, ctypes.c_size_t]
    _k.GlobalAlloc.restype = wt.HGLOBAL
    _k.GlobalLock.argtypes = [wt.HGLOBAL]
    _k.GlobalLock.restype = ctypes.c_void_p
    _k.GlobalUnlock.argtypes = [wt.HGLOBAL]
    _k.GlobalFree.argtypes = [wt.HGLOBAL]


def _open_clipboard(retries: int = 20) -> bool:
    for _ in range(retries):
        if winapi.user32.OpenClipboard(None):
            return True
        time.sleep(0.02)
    return False


def get_clipboard_text() -> str | None:
    if not winapi.IS_WINDOWS or not _open_clipboard():
        return None
    try:
        if not winapi.user32.IsClipboardFormatAvailable(CF_UNICODETEXT):
            return None
        h = winapi.user32.GetClipboardData(CF_UNICODETEXT)
        if not h:
            return None
        p = winapi.kernel32.GlobalLock(h)
        try:
            return ctypes.wstring_at(p) if p else None
        finally:
            winapi.kernel32.GlobalUnlock(h)
    finally:
        winapi.user32.CloseClipboard()


def set_clipboard_text(text: str) -> bool:
    if not winapi.IS_WINDOWS or not _open_clipboard():
        return False
    try:
        winapi.user32.EmptyClipboard()
        data = text.encode("utf-16-le") + b"\x00\x00"
        h = winapi.kernel32.GlobalAlloc(GMEM_MOVEABLE, len(data))
        if not h:
            return False
        p = winapi.kernel32.GlobalLock(h)
        ctypes.memmove(p, data, len(data))
        winapi.kernel32.GlobalUnlock(h)
        if not winapi.user32.SetClipboardData(CF_UNICODETEXT, h):
            winapi.kernel32.GlobalFree(h)
            return False
        return True
    finally:
        winapi.user32.CloseClipboard()


# --------------------------------------------------------------------------- SendInput

def _key_input(vk: int, up: bool) -> winapi.INPUT:
    scan = winapi.user32.MapVirtualKeyW(vk, winapi.MAPVK_VK_TO_VSC)
    flags = winapi.KEYEVENTF_KEYUP if up else 0
    inp = winapi.INPUT()
    inp.type = winapi.INPUT_KEYBOARD
    inp.ki = winapi.KEYBDINPUT(vk, scan, flags, 0, 0)
    return inp


def _send(inputs: list[winapi.INPUT]) -> int:
    arr = (winapi.INPUT * len(inputs))(*inputs)
    return winapi.user32.SendInput(len(inputs), arr, ctypes.sizeof(winapi.INPUT))


def tap_key(vk: int, shift: bool = False) -> None:
    seq = []
    if shift:
        seq.append(_key_input(winapi.VK_SHIFT, False))
    seq += [_key_input(vk, False), _key_input(vk, True)]
    if shift:
        seq.append(_key_input(winapi.VK_SHIFT, True))
    _send(seq)


def paste() -> None:
    _send([
        _key_input(winapi.VK_CONTROL, False),
        _key_input(winapi.VK_V, False),
        _key_input(winapi.VK_V, True),
        _key_input(winapi.VK_CONTROL, True),
    ])


MODIFIERS = (winapi.VK_SHIFT, winapi.VK_CONTROL, winapi.VK_MENU, winapi.VK_LWIN, winapi.VK_RWIN)


def wait_modifiers_released(timeout: float = 1.0) -> None:
    """Shift held while we press "/" would type "?" — wait, then force-release."""
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if not any(winapi.key_down(vk) for vk in MODIFIERS):
            return
        time.sleep(0.02)
    held = [vk for vk in MODIFIERS if winapi.key_down(vk)]
    if held:
        _send([_key_input(vk, True) for vk in held])


def slash_key() -> tuple[int, bool]:
    res = winapi.user32.VkKeyScanW("/")
    if res == -1:
        return 0xBF, False  # VK_OEM_2 on US / Polish programmer layout
    return res & 0xFF, bool((res >> 8) & 1)


# --------------------------------------------------------------------------- text rules

def sanitize_chat_text(text: str, max_bytes: int) -> str:
    """WoW chat: single line, no '|' escape sequences, at most ``max_bytes`` UTF-8 bytes."""
    t = " ".join(text.replace("|", "/").split())
    raw = t.encode("utf-8")
    if len(raw) <= max_bytes:
        return t
    cut = raw[:max_bytes]
    while cut and (cut[-1] & 0xC0) == 0x80:
        cut = cut[:-1]
    if cut and cut[-1] >= 0xC0:  # dangling lead byte
        cut = cut[:-1]
    return cut.decode("utf-8", errors="ignore").rstrip()


def build_chat_line(channel_cmd: str, text: str, max_bytes: int = 255) -> str:
    """'/p' + text -> '/p text' limited so that the whole input fits WoW's 255 byte box."""
    prefix = f"{channel_cmd} " if channel_cmd else ""
    body = sanitize_chat_text(text, max_bytes - len(prefix.encode("utf-8")))
    return prefix + body


class ChatInserter:
    def __init__(self, method: str = "slash", key_delay_ms: int = 120, restore_clipboard: bool = False):
        self.method = method
        self.key_delay = max(0.03, key_delay_ms / 1000.0)
        self.restore_clipboard = restore_clipboard

    def insert(self, chat_line: str, wow_is_foreground: bool) -> str:
        """Returns a short status string ("inserted" / "copied")."""
        previous = get_clipboard_text() if self.restore_clipboard else None
        if self.method == "clipboard" or not wow_is_foreground:
            set_clipboard_text(chat_line)
            return "copied"

        wait_modifiers_released()
        if self.method == "enter" or not chat_line.startswith("/"):
            set_clipboard_text(chat_line)
            tap_key(winapi.VK_RETURN)
        else:  # slash
            payload = chat_line[1:] if chat_line.startswith("/") else chat_line
            set_clipboard_text(payload)
            vk, shift = slash_key()
            tap_key(vk, shift)
        time.sleep(self.key_delay)
        paste()
        if self.restore_clipboard and previous is not None:
            time.sleep(0.4)
            set_clipboard_text(previous)
        elif self.method == "slash":
            # leave the full line (with "/") in the clipboard for a manual re-paste
            time.sleep(0.25)
            set_clipboard_text(chat_line)
        return "inserted"
