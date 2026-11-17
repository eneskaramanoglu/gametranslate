"""Win32 yardımcıları (ctypes): pencere listesi, pencere sınırları, capture'dan hariç tutma."""

from __future__ import annotations

import ctypes
import sys
from ctypes import wintypes
from dataclasses import dataclass

IS_WINDOWS = sys.platform == "win32"

WDA_NONE = 0x0
WDA_EXCLUDEFROMCAPTURE = 0x11
DWMWA_EXTENDED_FRAME_BOUNDS = 9
DWMWA_CLOAKED = 14
GWL_EXSTYLE = -20
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_LAYERED = 0x00080000
WS_EX_TRANSPARENT = 0x00000020
WS_EX_NOACTIVATE = 0x08000000
HWND_TOPMOST = -1
SWP_NOACTIVATE = 0x0010
SWP_NOMOVE = 0x0002
SWP_NOSIZE = 0x0001
SWP_SHOWWINDOW = 0x0040

if IS_WINDOWS:
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    dwmapi = ctypes.WinDLL("dwmapi", use_last_error=True)

    _EnumWindowsProc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    user32.EnumWindows.argtypes = [_EnumWindowsProc, wintypes.LPARAM]
    user32.EnumWindows.restype = wintypes.BOOL
    user32.IsWindowVisible.argtypes = [wintypes.HWND]
    user32.IsWindow.argtypes = [wintypes.HWND]
    user32.IsIconic.argtypes = [wintypes.HWND]
    user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
    user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    user32.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
    user32.GetWindowLongW.restype = ctypes.c_long
    user32.SetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_long]
    user32.SetWindowDisplayAffinity.argtypes = [wintypes.HWND, wintypes.DWORD]
    user32.SetWindowDisplayAffinity.restype = wintypes.BOOL
    user32.SetWindowPos.argtypes = [
        wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.UINT,
    ]
    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user32.GetWindowThreadProcessId.restype = wintypes.DWORD
    dwmapi.DwmGetWindowAttribute.argtypes = [wintypes.HWND, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD]
    dwmapi.DwmGetWindowAttribute.restype = ctypes.c_long


@dataclass(frozen=True)
class WindowInfo:
    hwnd: int
    title: str
    pid: int


def _title(hwnd: int) -> str:
    length = user32.GetWindowTextLengthW(hwnd)
    if length <= 0:
        return ""
    buf = ctypes.create_unicode_buffer(length + 1)
    user32.GetWindowTextW(hwnd, buf, length + 1)
    return buf.value


def window_title(hwnd: int) -> str:
    return _title(hwnd) if IS_WINDOWS else ""


def list_windows() -> list[WindowInfo]:
    """Görünür, başlıklı, gizlenmemiş (cloaked olmayan) üst düzey pencereler."""
    if not IS_WINDOWS:
        return []
    result: list[WindowInfo] = []
    own_pid = ctypes.windll.kernel32.GetCurrentProcessId()

    def callback(hwnd: int, _lparam: int) -> bool:
        if not user32.IsWindowVisible(hwnd):
            return True
        title = _title(hwnd)
        if not title:
            return True
        cloaked = wintypes.DWORD(0)
        dwmapi.DwmGetWindowAttribute(hwnd, DWMWA_CLOAKED, ctypes.byref(cloaked), ctypes.sizeof(cloaked))
        if cloaked.value:
            return True
        if user32.GetWindowLongW(hwnd, GWL_EXSTYLE) & WS_EX_TOOLWINDOW:
            return True
        pid = wintypes.DWORD(0)
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value == own_pid:
            return True
        result.append(WindowInfo(int(hwnd), title, int(pid.value)))
        return True

    user32.EnumWindows(_EnumWindowsProc(callback), 0)
    return result


def find_window(title_substring: str) -> WindowInfo | None:
    """Başlığı tam eşleşen pencereyi, yoksa alt dize içeren ilk pencereyi döndürür."""
    if not title_substring:
        return None
    needle = title_substring.casefold()
    windows = list_windows()
    for win in windows:
        if win.title.casefold() == needle:
            return win
    for win in windows:
        if needle in win.title.casefold():
            return win
    return None


def suggestion_rank(title: str, keywords: list[str]) -> int:
    """Küçük = daha iyi öneri; eşleşme yoksa len(keywords)."""
    folded = title.casefold()
    for i, keyword in enumerate(keywords):
        if keyword.casefold() in folded:
            return i
    return len(keywords)


def is_window(hwnd: int) -> bool:
    return bool(IS_WINDOWS and user32.IsWindow(hwnd))


def is_minimized(hwnd: int) -> bool:
    return bool(IS_WINDOWS and user32.IsIconic(hwnd))


def frame_bounds(hwnd: int) -> tuple[int, int, int, int] | None:
    """Pencerenin görünen sınırları (x, y, w, h), fiziksel piksel.

    WGC'nin yakaladığı kare bu sınırlarla (gölge hariç) örtüşür.
    """
    if not IS_WINDOWS or not user32.IsWindow(hwnd):
        return None
    rect = wintypes.RECT()
    hr = dwmapi.DwmGetWindowAttribute(hwnd, DWMWA_EXTENDED_FRAME_BOUNDS, ctypes.byref(rect), ctypes.sizeof(rect))
    if hr != 0 and not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
        return None
    width, height = rect.right - rect.left, rect.bottom - rect.top
    if width <= 0 or height <= 0:
        return None
    return rect.left, rect.top, width, height


def exclude_from_capture(hwnd: int, exclude: bool = True) -> bool:
    """Pencereyi tüm ekran yakalamalarından gizler (Windows 10 2004+)."""
    if not IS_WINDOWS:
        return False
    return bool(user32.SetWindowDisplayAffinity(hwnd, WDA_EXCLUDEFROMCAPTURE if exclude else WDA_NONE))


def make_click_through(hwnd: int) -> None:
    if not IS_WINDOWS:
        return
    style = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
    user32.SetWindowLongW(hwnd, GWL_EXSTYLE, style | WS_EX_LAYERED | WS_EX_TRANSPARENT | WS_EX_NOACTIVATE)


def place_topmost(hwnd: int, x: int, y: int, w: int, h: int) -> None:
    """Pencereyi fiziksel piksel koordinatlarıyla, odak almadan en üste yerleştirir."""
    if IS_WINDOWS:
        user32.SetWindowPos(hwnd, HWND_TOPMOST, x, y, w, h, SWP_NOACTIVATE)


def raise_topmost(hwnd: int) -> None:
    if IS_WINDOWS:
        user32.SetWindowPos(hwnd, HWND_TOPMOST, 0, 0, 0, 0, SWP_NOACTIVATE | SWP_NOMOVE | SWP_NOSIZE)
