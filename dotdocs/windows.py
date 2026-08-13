from __future__ import annotations

import ctypes
from ctypes import wintypes


SW_RESTORE = 9


def focus_existing_window(title: str, *, user32=None) -> bool:
    """Restore and foreground the first visible top-level window with an exact title."""
    user32 = user32 or ctypes.windll.user32
    matching = []
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    @callback_type
    def collect(hwnd, _lparam):
        if not user32.IsWindowVisible(hwnd):
            return True
        length = user32.GetWindowTextLengthW(hwnd)
        if not length:
            return True
        buffer = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buffer, length + 1)
        if buffer.value == title:
            matching.append(hwnd)
            return False
        return True

    user32.EnumWindows(collect, 0)
    if not matching:
        return False
    hwnd = matching[0]
    if user32.IsIconic(hwnd):
        user32.ShowWindow(hwnd, SW_RESTORE)
    user32.BringWindowToTop(hwnd)
    user32.SetForegroundWindow(hwnd)
    return True
