"""Windows screen capture via ctypes: find the Zoom window, grab it or a whole monitor.

The Zoom window is captured with ``PrintWindow(PW_RENDERFULLCONTENT)``, which works even when
other windows cover it. If that returns a blank image (some GPU-rendered windows do), the
window's screen area is grabbed instead. All calls run per-monitor DPI aware so coordinates are
physical pixels on every monitor.
"""

from __future__ import annotations

import ctypes
import logging
import sys
from ctypes import wintypes
from dataclasses import dataclass

import numpy as np
from PIL import Image, ImageGrab

from backend.capture.slides import Grab
from backend.core.config import SlidesConfig

log = logging.getLogger(__name__)

PW_RENDERFULLCONTENT = 2
DWMWA_EXTENDED_FRAME_BOUNDS = 9
DWMWA_CLOAKED = 14
DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2 = ctypes.c_void_p(-4)
MIN_W, MIN_H = 320, 200

if sys.platform == "win32":
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
    dwmapi = ctypes.WinDLL("dwmapi")

    WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    MONITORENUMPROC = ctypes.WINFUNCTYPE(
        wintypes.BOOL,
        wintypes.HMONITOR,
        wintypes.HDC,
        ctypes.POINTER(wintypes.RECT),
        wintypes.LPARAM,
    )

    user32.SetThreadDpiAwarenessContext.restype = ctypes.c_void_p
    user32.SetThreadDpiAwarenessContext.argtypes = [ctypes.c_void_p]
    user32.EnumWindows.argtypes = [WNDENUMPROC, wintypes.LPARAM]
    user32.IsWindowVisible.argtypes = [wintypes.HWND]
    user32.IsIconic.argtypes = [wintypes.HWND]
    user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    user32.GetWindowDC.argtypes = [wintypes.HWND]
    user32.GetWindowDC.restype = wintypes.HDC
    user32.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
    user32.PrintWindow.argtypes = [wintypes.HWND, wintypes.HDC, wintypes.UINT]
    user32.EnumDisplayMonitors.argtypes = [
        wintypes.HDC,
        ctypes.POINTER(wintypes.RECT),
        MONITORENUMPROC,
        wintypes.LPARAM,
    ]
    gdi32.CreateCompatibleDC.argtypes = [wintypes.HDC]
    gdi32.CreateCompatibleDC.restype = wintypes.HDC
    gdi32.CreateCompatibleBitmap.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int]
    gdi32.CreateCompatibleBitmap.restype = wintypes.HBITMAP
    gdi32.SelectObject.argtypes = [wintypes.HDC, wintypes.HGDIOBJ]
    gdi32.SelectObject.restype = wintypes.HGDIOBJ
    gdi32.DeleteObject.argtypes = [wintypes.HGDIOBJ]
    gdi32.DeleteDC.argtypes = [wintypes.HDC]
    gdi32.GetDIBits.argtypes = [
        wintypes.HDC, wintypes.HBITMAP, wintypes.UINT, wintypes.UINT,
        ctypes.c_void_p, ctypes.c_void_p, wintypes.UINT,
    ]  # fmt: skip
    dwmapi.DwmGetWindowAttribute.argtypes = [
        wintypes.HWND,
        wintypes.DWORD,
        ctypes.c_void_p,
        wintypes.DWORD,
    ]


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ("biSize", wintypes.DWORD),
        ("biWidth", wintypes.LONG),
        ("biHeight", wintypes.LONG),
        ("biPlanes", wintypes.WORD),
        ("biBitCount", wintypes.WORD),
        ("biCompression", wintypes.DWORD),
        ("biSizeImage", wintypes.DWORD),
        ("biXPelsPerMeter", wintypes.LONG),
        ("biYPelsPerMeter", wintypes.LONG),
        ("biClrUsed", wintypes.DWORD),
        ("biClrImportant", wintypes.DWORD),
    ]


@dataclass
class Window:
    hwnd: int
    title: str
    cls: str
    rect: tuple[int, int, int, int]  # visible bounds, physical pixels

    @property
    def area(self) -> int:
        return (self.rect[2] - self.rect[0]) * (self.rect[3] - self.rect[1])


@dataclass
class Monitor:
    index: int  # 1-based
    rect: tuple[int, int, int, int]

    @property
    def label(self) -> str:
        w, h = self.rect[2] - self.rect[0], self.rect[3] - self.rect[1]
        return f"Monitor {self.index} ({w}×{h})"


def thread_dpi_aware() -> None:
    if sys.platform == "win32":
        user32.SetThreadDpiAwarenessContext(DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2)


def _bounds(hwnd: int) -> tuple[int, int, int, int]:
    r = wintypes.RECT()
    ok = dwmapi.DwmGetWindowAttribute(
        hwnd, DWMWA_EXTENDED_FRAME_BOUNDS, ctypes.byref(r), ctypes.sizeof(r)
    )
    if ok != 0:  # S_OK == 0
        user32.GetWindowRect(hwnd, ctypes.byref(r))
    return r.left, r.top, r.right, r.bottom


def _cloaked(hwnd: int) -> bool:
    v = wintypes.DWORD()
    dwmapi.DwmGetWindowAttribute(hwnd, DWMWA_CLOAKED, ctypes.byref(v), ctypes.sizeof(v))
    return bool(v.value)


def windows() -> list[Window]:
    """Visible, non-minimized top-level windows."""
    out: list[Window] = []
    buf = ctypes.create_unicode_buffer(512)

    def cb(hwnd: int, _: int) -> bool:
        if not user32.IsWindowVisible(hwnd) or user32.IsIconic(hwnd) or _cloaked(hwnd):
            return True
        user32.GetClassNameW(hwnd, buf, 512)
        cls = buf.value
        user32.GetWindowTextW(hwnd, buf, 512)
        rect = _bounds(hwnd)
        win = Window(hwnd, buf.value, cls, rect)
        if rect[2] - rect[0] >= MIN_W and rect[3] - rect[1] >= MIN_H:
            out.append(win)
        return True

    user32.EnumWindows(WNDENUMPROC(cb), 0)
    return out


def find_window(cfg: SlidesConfig) -> Window | None:
    classes = {c.lower() for c in cfg.window_classes}
    titles = [t.lower() for t in cfg.window_titles]
    matches = [
        w
        for w in windows()
        if w.cls.lower() in classes or any(t in w.title.lower() for t in titles)
    ]
    return max(matches, key=lambda w: w.area, default=None)


def monitors() -> list[Monitor]:
    out: list[Monitor] = []

    def cb(_h: int, _dc: int, rect: ctypes._Pointer, _l: int) -> bool:  # type: ignore[type-arg]
        r = rect.contents
        out.append(Monitor(len(out) + 1, (r.left, r.top, r.right, r.bottom)))
        return True

    user32.EnumDisplayMonitors(None, None, MONITORENUMPROC(cb), 0)
    return out


def _blank(img: Image.Image) -> bool:
    a = np.asarray(img.convert("L").resize((32, 18)), dtype=np.float32)
    return float(a.std()) < 1.0


def print_window(win: Window) -> Image.Image | None:
    wr = wintypes.RECT()
    user32.GetWindowRect(win.hwnd, ctypes.byref(wr))
    w, h = wr.right - wr.left, wr.bottom - wr.top
    if w <= 0 or h <= 0:
        return None
    hdc = user32.GetWindowDC(win.hwnd)
    mem = gdi32.CreateCompatibleDC(hdc)
    bmp = gdi32.CreateCompatibleBitmap(hdc, w, h)
    old = gdi32.SelectObject(mem, bmp)
    try:
        if not user32.PrintWindow(win.hwnd, mem, PW_RENDERFULLCONTENT):
            return None
        bih = BITMAPINFOHEADER()
        bih.biSize = ctypes.sizeof(bih)
        bih.biWidth, bih.biHeight = w, -h  # negative: top-down rows
        bih.biPlanes, bih.biBitCount = 1, 32
        buf = ctypes.create_string_buffer(w * h * 4)
        if not gdi32.GetDIBits(mem, bmp, 0, h, buf, ctypes.byref(bih), 0):
            return None
        img = Image.frombuffer("RGB", (w, h), buf, "raw", "BGRX", 0, 1)
    finally:
        gdi32.SelectObject(mem, old)
        gdi32.DeleteObject(bmp)
        gdi32.DeleteDC(mem)
        user32.ReleaseDC(win.hwnd, hdc)
    # Crop the invisible resize borders: keep only the visible frame bounds.
    left, top, right, bottom = win.rect
    box = (left - wr.left, top - wr.top, right - wr.left, bottom - wr.top)
    if box[0] >= 0 and box[1] >= 0 and box[2] <= w and box[3] <= h:
        img = img.crop(box)
    return img


def grab_rect(rect: tuple[int, int, int, int]) -> Image.Image:
    return ImageGrab.grab(bbox=rect, all_screens=True)


def grab_window(win: Window) -> Image.Image:
    img = print_window(win)
    if img is None or _blank(img):
        img = grab_rect(win.rect)  # only correct while the window is not covered
    return img


class WindowsScreen:
    """The recorder's screen source: the Zoom window, else the configured monitor."""

    def __init__(self, cfg: SlidesConfig) -> None:
        self.cfg = cfg

    def grab(self) -> Grab | None:
        win = find_window(self.cfg)
        if win is not None:
            return Grab(grab_window(win), "Zoom window", win.title)
        if self.cfg.fallback_monitor:
            mons = monitors()
            if 1 <= self.cfg.fallback_monitor <= len(mons):
                mon = mons[self.cfg.fallback_monitor - 1]
                return Grab(grab_rect(mon.rect), mon.label)
        return None
