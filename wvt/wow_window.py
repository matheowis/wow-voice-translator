"""Finding the WoW window and checking whether it is in the foreground."""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import time
from dataclasses import dataclass

from . import winapi

WOW_WINDOW_CLASSES = ("GxWindowClass", "GxWindowClassD3d")


@dataclass
class WowWindow:
    hwnd: int
    process: str
    client: tuple[int, int, int, int]  # x, y, w, h in physical screen pixels


class WowWindowTracker:
    def __init__(self, process_names: list[str], extra_titles: list[str] | None = None):
        self.process_names = {p.lower() for p in process_names}
        self.extra_titles = list(extra_titles or [])  # dev simulator (python -m wvt --sim)
        self._cached: WowWindow | None = None
        self._cached_at = 0.0
        self._verdicts: dict[int, bool] = {}

    def is_wow(self, hwnd: int) -> bool:
        if not hwnd:
            return False
        cached = self._verdicts.get(hwnd)
        if cached is not None:
            return cached
        verdict = (
            winapi.window_class(hwnd) in WOW_WINDOW_CLASSES
            or winapi.process_name_of_window(hwnd).lower() in self.process_names
            or any(winapi.window_title(hwnd).startswith(t) for t in self.extra_titles)
        )
        if len(self._verdicts) > 256:
            self._verdicts.clear()
        self._verdicts[hwnd] = verdict
        return verdict

    def foreground_is_wow(self) -> bool:
        return self.is_wow(winapi.foreground_window())

    def find(self, max_age: float = 1.0) -> WowWindow | None:
        """Return the WoW window (foreground preferred), cached for ``max_age`` seconds."""
        now = time.monotonic()
        if self._cached and now - self._cached_at < max_age:
            client = winapi.client_rect_on_screen(self._cached.hwnd)
            if client:
                self._cached.client = client
                return self._cached
        self._cached = None
        hwnd = winapi.foreground_window()
        if not self.is_wow(hwnd):
            hwnd = self._enum_find()
        if hwnd:
            client = winapi.client_rect_on_screen(hwnd)
            if client and client[2] > 0 and client[3] > 0:
                self._cached = WowWindow(hwnd, winapi.process_name_of_window(hwnd), client)
                self._cached_at = now
        return self._cached

    def _enum_find(self) -> int:
        if not winapi.IS_WINDOWS:
            return 0
        found: list[int] = []

        @ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
        def cb(hwnd, _lparam):
            if winapi.user32.IsWindowVisible(hwnd) and (
                winapi.window_class(hwnd) in WOW_WINDOW_CLASSES
                or any(winapi.window_title(hwnd).startswith(t) for t in self.extra_titles)
            ):
                found.append(hwnd)
                return False
            return True

        winapi.user32.EnumWindows(cb, 0)
        return found[0] if found else 0
