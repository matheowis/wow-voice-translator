"""Global push-to-talk keys via low-level keyboard/mouse hooks.

* Works for keyboard keys and mouse buttons (mouse3/4/5).
* While WoW is the foreground window (configurable) the PTT key is *swallowed*, so
  Caps Lock does not toggle and ` is not typed into the game.
* Injected events (our own SendInput) are ignored.
* The hook callback only records the event and hands it to ``on_event``; the heavy
  work happens elsewhere, so system-wide input latency is not affected.
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import logging
import threading
from dataclasses import dataclass
from typing import Callable, Optional

from . import winapi

log = logging.getLogger(__name__)

MOUSE_KEYS = {"mouse3": "middle", "middle": "middle", "mouse4": "x1", "xbutton1": "x1", "mouse5": "x2", "xbutton2": "x2"}

NAMED_VK = {
    "caps_lock": 0x14, "capslock": 0x14, "caps": 0x14,
    "tab": 0x09, "space": 0x20, "esc": 0x1B, "escape": 0x1B, "enter": 0x0D, "return": 0x0D,
    "backspace": 0x08, "insert": 0x2D, "delete": 0x2E, "del": 0x2E, "home": 0x24, "end": 0x23,
    "pageup": 0x21, "page_up": 0x21, "pagedown": 0x22, "page_down": 0x22,
    "pause": 0x13, "scroll_lock": 0x91, "scrolllock": 0x91, "print_screen": 0x2C,
    "left": 0x25, "up": 0x26, "right": 0x27, "down": 0x28,
    "lshift": 0xA0, "rshift": 0xA1, "lctrl": 0xA2, "rctrl": 0xA3, "lalt": 0xA4, "ralt": 0xA5,
    "apps": 0x5D, "menu": 0x5D,
    "numpad_multiply": 0x6A, "numpad_plus": 0x6B, "numpad_minus": 0x6D, "numpad_decimal": 0x6E,
    "numpad_divide": 0x6F, "num_lock": 0x90,
    # layout independent names for OEM keys (US/Polish-programmer positions)
    "backquote": 0xC0, "grave": 0xC0, "tilde": 0xC0,
}

VK_TO_NAME = {
    0x14: "Caps Lock", 0xC0: "` (tylda)", 0x09: "Tab", 0x20: "Spacja", 0x1B: "Esc",
    0xA0: "Lewy Shift", 0xA1: "Prawy Shift", 0xA2: "Lewy Ctrl", 0xA3: "Prawy Ctrl",
    0xA4: "Lewy Alt", 0xA5: "Prawy Alt", 0x2D: "Insert", 0x2E: "Delete", 0x24: "Home", 0x23: "End",
    0x21: "Page Up", 0x22: "Page Down", 0x13: "Pause", 0x91: "Scroll Lock",
}


@dataclass(frozen=True)
class KeySpec:
    kind: str  # "key" | "mouse"
    code: int | str  # VK code or mouse button id ("middle", "x1", "x2")
    name: str

    def describe(self) -> str:
        if self.kind == "mouse":
            return {"middle": "Środkowy przycisk myszy", "x1": "Mysz 4 (boczny tył)", "x2": "Mysz 5 (boczny przód)"}[self.code]  # type: ignore[index]
        vk = int(self.code)
        if vk in VK_TO_NAME:
            return VK_TO_NAME[vk]
        if 0x70 <= vk <= 0x87:
            return f"F{vk - 0x6F}"
        if 0x30 <= vk <= 0x39 or 0x41 <= vk <= 0x5A:
            return chr(vk)
        if 0x60 <= vk <= 0x69:
            return f"Numpad {vk - 0x60}"
        return self.name


def parse_key(name: str) -> KeySpec:
    """Parse a key name from the config ("caps_lock", "`", "f9", "mouse5", "x" ...)."""
    raw = str(name).strip()
    low = raw.lower()
    if low in MOUSE_KEYS:
        return KeySpec("mouse", MOUSE_KEYS[low], low)
    if low in NAMED_VK:
        return KeySpec("key", NAMED_VK[low], low)
    if low.startswith("f") and low[1:].isdigit() and 1 <= int(low[1:]) <= 24:
        return KeySpec("key", 0x6F + int(low[1:]), low)
    if low.startswith("numpad") and low[6:].isdigit() and len(low) == 7:
        return KeySpec("key", 0x60 + int(low[6:]), low)
    if low.startswith("vk:"):
        return KeySpec("key", int(low[3:], 0), low)
    if len(raw) == 1:
        ch = raw
        if ch.isalnum() and ch.isascii():
            return KeySpec("key", ord(ch.upper()), low)
        if winapi.IS_WINDOWS:
            res = winapi.user32.VkKeyScanW(ch)
            if res != -1:
                return KeySpec("key", res & 0xFF, low)
        if ch == "`":
            return KeySpec("key", 0xC0, low)
    raise ValueError(f"Nieznany klawisz: {name!r}")


@dataclass
class Binding:
    key: KeySpec
    channel: str


class HotkeyManager:
    """Runs the low-level hooks on a private thread with its own message loop."""

    def __init__(
        self,
        bindings: list[Binding],
        on_event: Callable[[str, Optional[Binding]], None],
        is_active: Callable[[], bool],
    ):
        self.bindings = bindings
        self.on_event = on_event
        self.is_active = is_active
        self._by_vk = {int(b.key.code): b for b in bindings if b.key.kind == "key"}
        self._by_mouse = {str(b.key.code): b for b in bindings if b.key.kind == "mouse"}
        self._down: dict[str, Binding] = {}  # key name -> binding currently held
        self.recording = False  # set by the app; enables Esc-to-cancel
        self._thread: threading.Thread | None = None
        self._thread_id = 0
        self._hooks: list[int] = []
        self._procs: list = []  # keep ctypes callbacks alive
        self._ready = threading.Event()
        self.capture_next: Optional[Callable[[KeySpec], None]] = None  # "press a key" UI

    # ------------------------------------------------------------------ lifecycle
    def start(self) -> None:
        if not winapi.IS_WINDOWS:
            log.warning("Hotkeys are only supported on Windows")
            return
        self._thread = threading.Thread(target=self._run, name="wvt-hotkeys", daemon=True)
        self._thread.start()
        self._ready.wait(3)

    def stop(self) -> None:
        if self._thread_id:
            winapi.user32.PostThreadMessageW(self._thread_id, winapi.WM_QUIT, 0, 0)
        if self._thread:
            self._thread.join(2)

    def _run(self) -> None:
        self._thread_id = winapi.kernel32.GetCurrentThreadId()
        hmod = winapi.kernel32.GetModuleHandleW(None)
        kb_proc = winapi.HOOKPROC(self._keyboard_proc)
        self._procs.append(kb_proc)
        h = winapi.user32.SetWindowsHookExW(winapi.WH_KEYBOARD_LL, kb_proc, hmod, 0)
        if h:
            self._hooks.append(h)
        else:
            log.error("SetWindowsHookEx(WH_KEYBOARD_LL) failed: %s", ctypes.get_last_error())
        if self._by_mouse:
            ms_proc = winapi.HOOKPROC(self._mouse_proc)
            self._procs.append(ms_proc)
            hm = winapi.user32.SetWindowsHookExW(winapi.WH_MOUSE_LL, ms_proc, hmod, 0)
            if hm:
                self._hooks.append(hm)
            else:
                log.error("SetWindowsHookEx(WH_MOUSE_LL) failed: %s", ctypes.get_last_error())
        self._ready.set()
        msg = wt.MSG()
        while winapi.user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            pass
        for hk in self._hooks:
            winapi.user32.UnhookWindowsHookEx(hk)
        self._hooks.clear()

    # ------------------------------------------------------------------ hook procs
    def _emit(self, kind: str, binding: Optional[Binding]) -> None:
        try:
            self.on_event(kind, binding)
        except Exception:  # never let an exception escape into the hook chain
            log.exception("hotkey handler failed")

    def _handle(self, name: str, binding: Optional[Binding], is_down: bool) -> bool:
        """Return True to swallow the event."""
        if is_down:
            if name in self._down:
                return True  # auto-repeat of a key we own
            if binding is None or not self.is_active():
                return False
            self._down[name] = binding
            self._emit("press", binding)
            return True
        held = self._down.pop(name, None)
        if held is not None:
            self._emit("release", held)
            return True
        return False

    def _keyboard_proc(self, n_code: int, w_param: int, l_param: int) -> int:
        try:
            if n_code == 0:
                kb = ctypes.cast(l_param, ctypes.POINTER(winapi.KBDLLHOOKSTRUCT)).contents
                if not (kb.flags & winapi.LLKHF_INJECTED):
                    vk = kb.vkCode
                    is_down = w_param in (winapi.WM_KEYDOWN, winapi.WM_SYSKEYDOWN)
                    if self.capture_next is not None and is_down:
                        cb, self.capture_next = self.capture_next, None
                        cb(KeySpec("key", vk, f"vk:{vk:#x}"))
                        return 1
                    if vk == winapi.VK_ESCAPE and self.recording and self._down:
                        if is_down:
                            self._emit("cancel", None)
                        return 1
                    binding = self._by_vk.get(vk)
                    if binding is not None or f"k{vk}" in self._down:
                        if self._handle(f"k{vk}", binding, is_down):
                            return 1
        except Exception:
            log.exception("keyboard hook error")
        return winapi.user32.CallNextHookEx(None, n_code, w_param, l_param)

    def _mouse_proc(self, n_code: int, w_param: int, l_param: int) -> int:
        try:
            if n_code == 0 and w_param in (winapi.WM_XBUTTONDOWN, winapi.WM_XBUTTONUP, winapi.WM_MBUTTONDOWN, winapi.WM_MBUTTONUP):
                ms = ctypes.cast(l_param, ctypes.POINTER(winapi.MSLLHOOKSTRUCT)).contents
                if not (ms.flags & winapi.LLMHF_INJECTED):
                    if w_param in (winapi.WM_MBUTTONDOWN, winapi.WM_MBUTTONUP):
                        button = "middle"
                    else:
                        button = "x1" if (ms.mouseData >> 16) == winapi.XBUTTON1 else "x2"
                    is_down = w_param in (winapi.WM_XBUTTONDOWN, winapi.WM_MBUTTONDOWN)
                    if self.capture_next is not None and is_down:
                        cb, self.capture_next = self.capture_next, None
                        cb(KeySpec("mouse", button, {"middle": "mouse3", "x1": "mouse4", "x2": "mouse5"}[button]))
                        return 1
                    binding = self._by_mouse.get(button)
                    if binding is not None or f"m{button}" in self._down:
                        if self._handle(f"m{button}", binding, is_down):
                            return 1
        except Exception:
            log.exception("mouse hook error")
        return winapi.user32.CallNextHookEx(None, n_code, w_param, l_param)


def keyspec_to_config(spec: KeySpec) -> str:
    """Inverse of parse_key for keys captured in the settings dialog."""
    if spec.kind == "mouse":
        return {"middle": "mouse3", "x1": "mouse4", "x2": "mouse5"}[str(spec.code)]
    vk = int(spec.code)
    for name, code in NAMED_VK.items():
        if code == vk and name in ("caps_lock", "tab", "space", "backquote", "insert", "delete", "home", "end",
                                   "pageup", "pagedown", "pause", "scroll_lock", "lshift", "rshift", "lctrl",
                                   "rctrl", "lalt", "ralt", "apps", "num_lock"):
            return "`" if name == "backquote" else name
    if 0x70 <= vk <= 0x87:
        return f"f{vk - 0x6F}"
    if 0x30 <= vk <= 0x39 or 0x41 <= vk <= 0x5A:
        return chr(vk).lower()
    if 0x60 <= vk <= 0x69:
        return f"numpad{vk - 0x60}"
    return f"vk:{vk:#x}"
