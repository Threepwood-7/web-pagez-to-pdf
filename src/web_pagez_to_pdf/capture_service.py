"""Windows window-activation tracking and screenshot capture helpers."""

from __future__ import annotations

import ctypes
from ctypes import wintypes

from PySide6.QtCore import QPoint
from PySide6.QtGui import QGuiApplication, QPixmap, QScreen

GW_HWNDPREV = 3
HISTORY_LIMIT = 40
MAX_Z_ORDER_HOPS = 64

USER32 = ctypes.windll.user32
USER32.GetForegroundWindow.restype = wintypes.HWND
USER32.GetWindow.argtypes = [wintypes.HWND, wintypes.UINT]
USER32.GetWindow.restype = wintypes.HWND
USER32.IsWindowVisible.argtypes = [wintypes.HWND]
USER32.IsWindowVisible.restype = wintypes.BOOL
USER32.IsIconic.argtypes = [wintypes.HWND]
USER32.IsIconic.restype = wintypes.BOOL
USER32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
USER32.GetWindowRect.restype = wintypes.BOOL
USER32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
USER32.GetWindowTextLengthW.restype = ctypes.c_int
USER32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
USER32.GetWindowTextW.restype = ctypes.c_int


class WindowCaptureService:
    """Track foreground windows and capture the previous active one."""

    def __init__(self) -> None:
        self._foreground_history: list[int] = []

    def record_foreground_window(self) -> int:
        """Append the current foreground handle to activation history."""

        hwnd = int(USER32.GetForegroundWindow())
        if hwnd and (not self._foreground_history or self._foreground_history[-1] != hwnd):
            self._foreground_history.append(hwnd)
            if len(self._foreground_history) > HISTORY_LIMIT:
                self._foreground_history = self._foreground_history[-HISTORY_LIMIT:]
        return hwnd

    def resolve_second_last_window(self, own_hwnd: int) -> int | None:
        """Return the window handle that was active before the current window."""

        current_hwnd = self.record_foreground_window()
        candidate = self._history_candidate(current_hwnd=current_hwnd, own_hwnd=own_hwnd)
        if candidate is not None:
            return candidate

        start_hwnd = current_hwnd or own_hwnd
        return self._previous_visible_window(start_hwnd=start_hwnd, own_hwnd=own_hwnd)

    def capture_window(self, hwnd: int) -> QPixmap | None:
        """Capture a screenshot of a specific native window handle."""

        if hwnd <= 0:
            return None

        screen = self._screen_for_window(hwnd)
        if screen is None:
            return None

        pixmap = screen.grabWindow(hwnd)
        if pixmap.isNull():
            return None
        return pixmap

    @staticmethod
    def window_title(hwnd: int) -> str:
        """Return a best-effort title for a native window handle."""

        if hwnd <= 0:
            return ""

        text_length = int(USER32.GetWindowTextLengthW(hwnd))
        if text_length <= 0:
            return f"hwnd:{hwnd}"

        buffer = ctypes.create_unicode_buffer(text_length + 1)
        USER32.GetWindowTextW(hwnd, buffer, len(buffer))
        title = buffer.value.strip()
        if title:
            return title
        return f"hwnd:{hwnd}"

    def _history_candidate(self, current_hwnd: int, own_hwnd: int) -> int | None:
        condensed: list[int] = []
        for hwnd in reversed(self._foreground_history):
            if not condensed or condensed[-1] != hwnd:
                condensed.append(hwnd)

        skipped_current = False
        for hwnd in condensed:
            if hwnd == own_hwnd:
                continue
            if current_hwnd and not skipped_current and hwnd == current_hwnd:
                skipped_current = True
                continue
            if self._is_capture_candidate(hwnd):
                return hwnd
        return None

    @staticmethod
    def _is_capture_candidate(hwnd: int) -> bool:
        if hwnd <= 0:
            return False
        return bool(USER32.IsWindowVisible(hwnd)) and not bool(USER32.IsIconic(hwnd))

    @staticmethod
    def _previous_visible_window(start_hwnd: int, own_hwnd: int) -> int | None:
        hwnd = int(USER32.GetWindow(start_hwnd, GW_HWNDPREV))
        hop_count = 0
        while hwnd and hop_count < MAX_Z_ORDER_HOPS:
            if hwnd != own_hwnd and bool(USER32.IsWindowVisible(hwnd)) and not bool(USER32.IsIconic(hwnd)):
                return hwnd
            hwnd = int(USER32.GetWindow(hwnd, GW_HWNDPREV))
            hop_count += 1
        return None

    @staticmethod
    def _screen_for_window(hwnd: int) -> QScreen | None:
        rect = wintypes.RECT()
        screen = None
        if USER32.GetWindowRect(hwnd, ctypes.byref(rect)):
            center_point = QPoint((rect.left + rect.right) // 2, (rect.top + rect.bottom) // 2)
            screen = QGuiApplication.screenAt(center_point)
        if screen is None:
            screen = QGuiApplication.primaryScreen()
        return screen
