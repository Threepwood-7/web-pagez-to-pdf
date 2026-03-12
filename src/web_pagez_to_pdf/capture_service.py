"""Windows capture primitives for window selection and screenshot automation."""

from __future__ import annotations

import ctypes
import logging
import time
from ctypes import wintypes
from dataclasses import dataclass
from typing import ClassVar

import win32con
import win32gui
import win32ui
from PIL import Image, ImageQt
from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QGuiApplication, QImage, QPixmap, QScreen

GW_HWNDPREV = 3
HISTORY_LIMIT = 80
MAX_Z_ORDER_HOPS = 96
VK_NEXT = 0x22
VK_HOME = 0x24
VK_TAB = 0x09
VK_MENU = 0x12
KEYEVENTF_KEYUP = 0x0002
WM_MOUSEWHEEL = 0x020A
WHEEL_DELTA = 120
INPUT_MOUSE = 0
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
MOUSEEVENTF_WHEEL = 0x0800
SWP_NOMOVE = 0x0002
SWP_NOSIZE = 0x0001
SWP_NOACTIVATE = 0x0010
HWND_TOP = 0
SW_RESTORE = 9
ULONG_PTR = ctypes.c_ulonglong if ctypes.sizeof(ctypes.c_void_p) == 8 else ctypes.c_ulong
LRESULT = ctypes.c_ssize_t
PW_RENDERFULLCONTENT = 0x00000002
CAPTURE_BACKENDS = ("screen_region_gdi", "qt_grab_window", "print_window")
DEFAULT_CAPTURE_BACKEND = "screen_region_gdi"
CAPTURE_FRAME_REGIONS = ("client_area", "full_window")
DEFAULT_CAPTURE_FRAME_REGION = "client_area"
CAPTURE_LOGGER_NAME = "web_pagez_to_pdf.capture"
CURSOR_VERIFY_TOLERANCE_PX = 2
CURSOR_SHOWING = 0x00000001

USER32 = ctypes.WinDLL("user32", use_last_error=True)
USER32.GetForegroundWindow.restype = wintypes.HWND
USER32.GetWindow.argtypes = [wintypes.HWND, wintypes.UINT]
USER32.GetWindow.restype = wintypes.HWND
USER32.IsWindowVisible.argtypes = [wintypes.HWND]
USER32.IsWindowVisible.restype = wintypes.BOOL
USER32.IsIconic.argtypes = [wintypes.HWND]
USER32.IsIconic.restype = wintypes.BOOL
USER32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
USER32.GetWindowRect.restype = wintypes.BOOL
USER32.GetClientRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
USER32.GetClientRect.restype = wintypes.BOOL
USER32.ClientToScreen.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.POINT)]
USER32.ClientToScreen.restype = wintypes.BOOL
USER32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
USER32.GetWindowTextLengthW.restype = ctypes.c_int
USER32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
USER32.GetWindowTextW.restype = ctypes.c_int
USER32.EnumWindows.argtypes = [
    ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM),
    wintypes.LPARAM,
]
USER32.EnumWindows.restype = wintypes.BOOL
USER32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
USER32.GetWindowThreadProcessId.restype = wintypes.DWORD
USER32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
USER32.GetClassNameW.restype = ctypes.c_int
USER32.WindowFromPoint.argtypes = [wintypes.POINT]
USER32.WindowFromPoint.restype = wintypes.HWND
USER32.GetCursorPos.argtypes = [ctypes.POINTER(wintypes.POINT)]
USER32.GetCursorPos.restype = wintypes.BOOL
USER32.SetCursorPos.argtypes = [ctypes.c_int, ctypes.c_int]
USER32.SetCursorPos.restype = wintypes.BOOL
USER32.SetForegroundWindow.argtypes = [wintypes.HWND]
USER32.SetForegroundWindow.restype = wintypes.BOOL
USER32.SetFocus.argtypes = [wintypes.HWND]
USER32.SetFocus.restype = wintypes.HWND
USER32.SetActiveWindow.argtypes = [wintypes.HWND]
USER32.SetActiveWindow.restype = wintypes.HWND
USER32.BringWindowToTop.argtypes = [wintypes.HWND]
USER32.BringWindowToTop.restype = wintypes.BOOL
USER32.SetWindowPos.argtypes = [
    wintypes.HWND,
    wintypes.HWND,
    ctypes.c_int,
    ctypes.c_int,
    ctypes.c_int,
    ctypes.c_int,
    wintypes.UINT,
]
USER32.SetWindowPos.restype = wintypes.BOOL
USER32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
USER32.ShowWindow.restype = wintypes.BOOL
USER32.AttachThreadInput.argtypes = [wintypes.DWORD, wintypes.DWORD, wintypes.BOOL]
USER32.AttachThreadInput.restype = wintypes.BOOL
USER32.PrintWindow.argtypes = [wintypes.HWND, wintypes.HDC, wintypes.UINT]
USER32.PrintWindow.restype = wintypes.BOOL
USER32.SendInput.argtypes = [wintypes.UINT, ctypes.c_void_p, ctypes.c_int]
USER32.SendInput.restype = wintypes.UINT
USER32.SendMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
USER32.SendMessageW.restype = LRESULT
USER32.keybd_event.argtypes = [
    wintypes.BYTE,
    wintypes.BYTE,
    wintypes.DWORD,
    ULONG_PTR,
]

KERNEL32 = ctypes.WinDLL("kernel32", use_last_error=True)
KERNEL32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
KERNEL32.OpenProcess.restype = wintypes.HANDLE
KERNEL32.CloseHandle.argtypes = [wintypes.HANDLE]
KERNEL32.CloseHandle.restype = wintypes.BOOL
KERNEL32.GetCurrentThreadId.argtypes = []
KERNEL32.GetCurrentThreadId.restype = wintypes.DWORD

PSAPI = ctypes.windll.psapi
PSAPI.GetModuleBaseNameW.argtypes = [
    wintypes.HANDLE,
    wintypes.HMODULE,
    wintypes.LPWSTR,
    wintypes.DWORD,
]
PSAPI.GetModuleBaseNameW.restype = wintypes.DWORD

PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
PROCESS_VM_READ = 0x0010
ALT_TAB_EXCLUDED_CLASSES = {
    "Shell_TrayWnd",
    "Shell_SecondaryTrayWnd",
    "Progman",
    "WorkerW",
    "MultitaskingViewFrame",
    "TaskSwitcherWnd",
}
LOGGER = logging.getLogger(CAPTURE_LOGGER_NAME)


def _hwnd_to_int(value: object) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


class MOUSEINPUT(ctypes.Structure):
    """ctypes mapping for Win32 MOUSEINPUT."""

    _fields_: ClassVar[list[tuple[str, object]]] = [
        ("dx", wintypes.LONG),
        ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class _INPUTUNION(ctypes.Union):
    _fields_: ClassVar[list[tuple[str, object]]] = [("mi", MOUSEINPUT)]


class INPUT(ctypes.Structure):
    """ctypes mapping for Win32 INPUT."""

    _fields_: ClassVar[list[tuple[str, object]]] = [
        ("type", wintypes.DWORD),
        ("union", _INPUTUNION),
    ]


USER32.SendInput.argtypes = [wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int]


@dataclass(slots=True)
class WindowInfo:
    """Metadata for a visible top-level window."""

    hwnd: int
    title: str
    process_name: str
    class_name: str
    process_id: int = 0
    is_minimized: bool = False

    @property
    def process_short_name(self) -> str:
        normalized = str(self.process_name or "").strip().lower()
        if normalized.endswith(".exe"):
            normalized = normalized[:-4]
        return normalized or "unknown"

    @property
    def sort_key(self) -> tuple[str, str]:
        title_text = str(self.title or "").strip().lower()
        return (self.process_short_name, title_text)

    @property
    def label(self) -> str:
        title_text = str(self.title or "").strip() or f"hwnd:{self.hwnd}"
        return f"{self.process_short_name} - {title_text} [{self.process_id}, {self.hwnd}]"


class WindowCaptureService:
    """Track window focus and provide capture automation helpers."""

    def __init__(self) -> None:
        self._foreground_history: list[int] = []
        self._scroll_cursor_origin: tuple[int, int] | None = None
        self._scroll_cursor_hold_mode = "keep_at_center"
        self._scroll_session_active = False
        self._capture_session_id = ""
        self._capture_session_target = ""

    def _session_prefix(self) -> str:
        if not self._capture_session_id:
            return "[capture-session:none]"
        return f"[capture-session:{self._capture_session_id}]"

    def record_foreground_window(self) -> int:
        """Append current foreground window handle into history."""

        hwnd = _hwnd_to_int(USER32.GetForegroundWindow())
        if hwnd and (not self._foreground_history or self._foreground_history[-1] != hwnd):
            self._foreground_history.append(hwnd)
            if len(self._foreground_history) > HISTORY_LIMIT:
                self._foreground_history = self._foreground_history[-HISTORY_LIMIT:]
        return hwnd

    def resolve_second_last_window(self, own_hwnd: int) -> int | None:
        """Return the most recent valid handle that is not this app."""

        current_hwnd = self.record_foreground_window()
        candidate = self._history_candidate(current_hwnd=current_hwnd, own_hwnd=own_hwnd)
        if candidate is not None:
            return candidate

        start_hwnd = current_hwnd or own_hwnd
        return self._previous_visible_window(start_hwnd=start_hwnd, own_hwnd=own_hwnd)

    def resolve_alt_tab_target(
        self,
        own_hwnd: int,
        *,
        retries: int = 1,
        settle_ms: int = 180,
    ) -> int | None:
        """Switch to previous app via Alt+Tab and return a capture-safe foreground hwnd."""

        attempt_count = max(1, int(retries) + 1)
        settle_s = max(0.05, float(settle_ms) / 1000.0)
        for attempt in range(attempt_count):
            self._emit_alt_tab_shortcut()
            time.sleep(settle_s)
            candidate = _hwnd_to_int(USER32.GetForegroundWindow())
            if self._is_alt_tab_target_candidate(candidate, own_hwnd):
                LOGGER.info(
                    "alt-tab target resolved hwnd=%s attempt=%s/%s",
                    candidate,
                    attempt + 1,
                    attempt_count,
                )
                self.record_foreground_window()
                return candidate
            LOGGER.warning(
                "alt-tab target rejected hwnd=%s class=%r attempt=%s/%s",
                candidate,
                self.window_class_name(candidate),
                attempt + 1,
                attempt_count,
            )
        return None

    def list_top_windows(self, own_hwnd: int, *, include_minimized: bool = False) -> list[WindowInfo]:
        """Enumerate currently visible top-level windows."""

        windows: list[WindowInfo] = []

        @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        def _enum_proc(hwnd: int, _lparam: int) -> bool:
            if hwnd == own_hwnd:
                return True
            if not self._is_capture_candidate(hwnd, include_minimized=include_minimized):
                return True
            info = self.window_info(int(hwnd))
            if info is None:
                return True
            if not info.title.strip():
                return True
            windows.append(info)
            return True

        USER32.EnumWindows(_enum_proc, 0)
        windows.sort(key=lambda item: item.sort_key)
        return windows

    def window_info(self, hwnd: int) -> WindowInfo | None:
        """Build best-effort metadata snapshot for one window handle."""

        if hwnd <= 0:
            return None
        title = self.window_title(hwnd).strip()
        if not title:
            title = f"hwnd:{hwnd}"
        process_id = self.window_process_id(hwnd)
        return WindowInfo(
            hwnd=int(hwnd),
            title=title,
            process_name=self.window_process_name(hwnd),
            process_id=process_id,
            class_name=self.window_class_name(hwnd),
            is_minimized=bool(USER32.IsIconic(hwnd)),
        )

    @staticmethod
    def window_from_point(global_x: int, global_y: int) -> int | None:
        """Resolve native window handle at a global screen point."""

        point = wintypes.POINT(global_x, global_y)
        hwnd = int(USER32.WindowFromPoint(point))
        if hwnd <= 0:
            return None
        return hwnd

    @staticmethod
    def activate_window(hwnd: int) -> tuple[bool, str]:
        """Attempt foreground/focus, restoring minimized targets when needed."""

        if hwnd <= 0:
            LOGGER.error("activate_window invalid hwnd=%s", hwnd)
            return (False, "Target window handle is invalid.")
        if not bool(USER32.IsWindowVisible(hwnd)):
            LOGGER.error("activate_window target not visible hwnd=%s", hwnd)
            return (False, "Target window is not visible. Bring it on-screen and retry.")
        if bool(USER32.IsIconic(hwnd)):
            LOGGER.info("activate_window target minimized, attempting restore hwnd=%s", hwnd)
            USER32.ShowWindow(hwnd, SW_RESTORE)
            time.sleep(0.08)
            if bool(USER32.IsIconic(hwnd)):
                LOGGER.error("activate_window restore failed hwnd=%s", hwnd)
                return (
                    False,
                    "Target window is minimized and could not be restored. Restore it and retry.",
                )
        if WindowCaptureService.is_foreground_window(hwnd):
            LOGGER.debug("activate_window already foreground hwnd=%s", hwnd)
            return (True, "")

        LOGGER.debug("activate_window focus attempt hwnd=%s", hwnd)
        WindowCaptureService._focus_window(hwnd)
        if WindowCaptureService.is_foreground_window(hwnd):
            LOGGER.debug("activate_window focus succeeded direct hwnd=%s", hwnd)
            return (True, "")

        foreground_hwnd = _hwnd_to_int(USER32.GetForegroundWindow())
        foreground_thread = WindowCaptureService._window_thread_id(foreground_hwnd)
        target_thread = WindowCaptureService._window_thread_id(hwnd)
        current_thread = int(KERNEL32.GetCurrentThreadId())
        attached_foreground = False
        attached_target = False
        try:
            if foreground_thread and foreground_thread != current_thread:
                attached_foreground = bool(
                    USER32.AttachThreadInput(current_thread, foreground_thread, True)
                )
            if target_thread and target_thread != current_thread:
                attached_target = bool(
                    USER32.AttachThreadInput(current_thread, target_thread, True)
                )
            WindowCaptureService._focus_window(hwnd)
        finally:
            if attached_target and target_thread:
                USER32.AttachThreadInput(current_thread, target_thread, False)
            if attached_foreground and foreground_thread:
                USER32.AttachThreadInput(current_thread, foreground_thread, False)

        if not WindowCaptureService.is_foreground_window(hwnd):
            LOGGER.error("activate_window failed hwnd=%s after thread attach focus path", hwnd)
            return (
                False,
                "Could not focus target window. Click it once, keep it visible, then retry.",
            )
        LOGGER.debug("activate_window focus succeeded via thread attach hwnd=%s", hwnd)
        return (True, "")

    @staticmethod
    def is_foreground_window(hwnd: int) -> bool:
        """Return whether the given hwnd currently owns foreground focus."""

        return hwnd > 0 and _hwnd_to_int(USER32.GetForegroundWindow()) == int(hwnd)

    def ensure_window_foreground(self, hwnd: int) -> tuple[bool, str]:
        """Ensure target is in foreground without changing window state."""

        if self.is_foreground_window(hwnd):
            LOGGER.debug("%s ensure_foreground already active hwnd=%s", self._session_prefix(), hwnd)
            return (True, "")
        focused, reason = self.activate_window(hwnd)
        LOGGER.debug(
            "%s ensure_foreground activation_result hwnd=%s focused=%s reason=%s",
            self._session_prefix(),
            hwnd,
            focused,
            reason or "",
        )
        return (focused, reason)

    @staticmethod
    def send_page_down() -> None:
        """Emit a PageDown keyboard event to the current foreground window."""

        USER32.keybd_event(VK_NEXT, 0, 0, 0)
        USER32.keybd_event(VK_NEXT, 0, KEYEVENTF_KEYUP, 0)
        LOGGER.debug("page_down injected via keybd_event")

    @staticmethod
    def _emit_alt_tab_shortcut() -> None:
        """Inject Alt+Tab to switch to the previous task."""

        USER32.keybd_event(VK_MENU, 0, 0, 0)
        USER32.keybd_event(VK_TAB, 0, 0, 0)
        USER32.keybd_event(VK_TAB, 0, KEYEVENTF_KEYUP, 0)
        USER32.keybd_event(VK_MENU, 0, KEYEVENTF_KEYUP, 0)

    @staticmethod
    def send_home() -> bool:
        """Emit a Home keyboard event to the current foreground window."""

        try:
            USER32.keybd_event(VK_HOME, 0, 0, 0)
            USER32.keybd_event(VK_HOME, 0, KEYEVENTF_KEYUP, 0)
            LOGGER.debug("home injected via keybd_event")
            return True
        except Exception:
            LOGGER.exception("home injection failed")
            return False

    def start_full_capture_input_session(
        self,
        target_hwnd: int,
        *,
        cursor_hold_mode: str = "keep_at_center",
        session_id: str = "",
        target_label: str = "",
        target_process: str = "",
        scroll_strategy: str = "",
        capture_backend: str = "",
        wheel_injection_mode: str = "",
        center_click_assist: str = "",
    ) -> None:
        """Initialize cursor handling for the full-capture loop."""

        self._capture_session_id = str(session_id or "").strip()
        self._capture_session_target = (
            f"hwnd={target_hwnd} label={target_label!r} process={target_process!r}"
        )
        self._scroll_session_active = True
        self._scroll_cursor_hold_mode = self._normalize_cursor_hold_mode(cursor_hold_mode)
        self._scroll_cursor_origin = self._current_cursor_pos()
        LOGGER.info(
            "%s input-session start %s strategy=%s backend=%s wheel=%s click_assist=%s cursor_hold=%s",
            self._session_prefix(),
            self._capture_session_target,
            scroll_strategy or "n/a",
            capture_backend or "n/a",
            wheel_injection_mode or "n/a",
            center_click_assist or "n/a",
            self._scroll_cursor_hold_mode,
        )
        LOGGER.debug(
            "%s cursor-origin=%s",
            self._session_prefix(),
            self._scroll_cursor_origin,
        )
        if self._scroll_cursor_hold_mode == "keep_at_center":
            self._move_cursor_to_window_center(target_hwnd)

    def end_full_capture_input_session(self) -> None:
        """Restore cursor state after full-capture loop ends."""

        try:
            if (
                self._scroll_session_active
                and self._scroll_cursor_hold_mode == "keep_at_center"
                and self._scroll_cursor_origin is not None
            ):
                USER32.SetCursorPos(self._scroll_cursor_origin[0], self._scroll_cursor_origin[1])
                LOGGER.debug(
                    "%s cursor-restored to %s",
                    self._session_prefix(),
                    self._scroll_cursor_origin,
                )
        finally:
            LOGGER.info(
                "%s input-session end %s",
                self._session_prefix(),
                self._capture_session_target or "target=n/a",
            )
            self._scroll_cursor_origin = None
            self._scroll_cursor_hold_mode = "keep_at_center"
            self._scroll_session_active = False
            self._capture_session_id = ""
            self._capture_session_target = ""

    def wheel_down_at_window_center(
        self,
        hwnd: int,
        *,
        wheel_injection_mode: str = "physical_center_sendinput",
        cursor_hold_mode: str = "keep_at_center",
    ) -> bool:
        """Move cursor to target center and emit WheelDown via selected backend."""

        return self._wheel_at_window_center(
            hwnd,
            direction="down",
            wheel_injection_mode=wheel_injection_mode,
            cursor_hold_mode=cursor_hold_mode,
        )

    def wheel_up_at_window_center(
        self,
        hwnd: int,
        *,
        wheel_injection_mode: str = "physical_center_sendinput",
        cursor_hold_mode: str = "keep_at_center",
    ) -> bool:
        """Move cursor to target center and emit WheelUp via selected backend."""

        return self._wheel_at_window_center(
            hwnd,
            direction="up",
            wheel_injection_mode=wheel_injection_mode,
            cursor_hold_mode=cursor_hold_mode,
        )

    def _wheel_at_window_center(
        self,
        hwnd: int,
        *,
        direction: str,
        wheel_injection_mode: str,
        cursor_hold_mode: str,
    ) -> bool:
        """Move cursor to target center and emit wheel input in chosen direction."""

        normalized_mode = self._normalize_wheel_injection_mode(wheel_injection_mode)
        hold_mode = self._normalize_cursor_hold_mode(cursor_hold_mode)
        original_pos = self._current_cursor_pos() if hold_mode == "restore_each_step" else None
        normalized_direction = "up" if str(direction).strip().lower() == "up" else "down"

        if normalized_mode == "legacy_message_wheel":
            ok = self._scroll_with_wheel_message(hwnd, direction=normalized_direction)
            if not ok:
                LOGGER.error(
                    "%s wheel-message failed direction=%s %s",
                    self._session_prefix(),
                    normalized_direction,
                    self._capture_session_target or f"hwnd={hwnd}",
                )
        else:
            moved = self._move_cursor_to_window_center(hwnd)
            if not moved:
                LOGGER.error(
                    "%s wheel-center cursor move failed %s",
                    self._session_prefix(),
                    self._capture_session_target or f"hwnd={hwnd}",
                )
                return False
            if normalized_direction == "up":
                ok, error_code = self._send_mouse_wheel_up()
            else:
                ok, error_code = self._send_mouse_wheel_down()
            if not ok:
                LOGGER.error(
                    "%s SendInput wheel failed direction=%s error_code=%s",
                    self._session_prefix(),
                    normalized_direction,
                    error_code,
                )
            else:
                LOGGER.debug(
                    "%s SendInput wheel success direction=%s cursor=%s",
                    self._session_prefix(),
                    normalized_direction,
                    self._current_cursor_pos(),
                )

        if hold_mode == "restore_each_step" and original_pos is not None:
            USER32.SetCursorPos(original_pos[0], original_pos[1])
            LOGGER.debug(
                "%s cursor restored after wheel direction=%s step=%s",
                self._session_prefix(),
                normalized_direction,
                original_pos,
            )
        return ok

    def click_window_center(
        self,
        hwnd: int,
        *,
        cursor_hold_mode: str = "keep_at_center",
    ) -> bool:
        """Move cursor to target center and perform a left click."""

        hold_mode = self._normalize_cursor_hold_mode(cursor_hold_mode)
        original_pos = self._current_cursor_pos() if hold_mode == "restore_each_step" else None
        if not self._move_cursor_to_window_center(hwnd):
            LOGGER.error(
                "%s click-center cursor move failed %s",
                self._session_prefix(),
                self._capture_session_target or f"hwnd={hwnd}",
            )
            return False
        clicked, error_code = self._send_mouse_left_click()
        if not clicked:
            LOGGER.error(
                "%s SendInput click failed error_code=%s",
                self._session_prefix(),
                error_code,
            )
        else:
            LOGGER.debug(
                "%s SendInput click success cursor=%s",
                self._session_prefix(),
                self._current_cursor_pos(),
            )
        if hold_mode == "restore_each_step" and original_pos is not None:
            USER32.SetCursorPos(original_pos[0], original_pos[1])
            LOGGER.debug(
                "%s cursor restored after click step=%s",
                self._session_prefix(),
                original_pos,
            )
        return clicked

    def capture_window(
        self,
        hwnd: int,
        *,
        primary_backend: str = DEFAULT_CAPTURE_BACKEND,
        frame_region: str = DEFAULT_CAPTURE_FRAME_REGION,
        include_mouse_cursor: bool = False,
    ) -> tuple[QPixmap | None, str]:
        """Capture one window using selected backend and deterministic fallbacks."""

        if hwnd <= 0:
            LOGGER.error("%s capture_window invalid hwnd=%s", self._session_prefix(), hwnd)
            return (None, "")

        ordered_backends = self._ordered_backends(primary_backend)
        normalized_region = self._normalize_frame_region(frame_region)
        include_cursor = bool(include_mouse_cursor)
        LOGGER.debug(
            "%s capture_window start hwnd=%s primary=%s frame_region=%s include_cursor=%s fallback_chain=%s",
            self._session_prefix(),
            hwnd,
            primary_backend,
            normalized_region,
            include_cursor,
            ordered_backends,
        )
        for backend in ordered_backends:
            for attempt in range(2):
                pixmap = self._capture_with_backend(
                    hwnd,
                    backend,
                    frame_region=normalized_region,
                    include_mouse_cursor=include_cursor,
                )
                is_blank_or_null = pixmap is None
                if pixmap is not None:
                    is_blank_or_null = self._is_blank_like(pixmap)
                if pixmap is not None and not is_blank_or_null:
                    LOGGER.debug(
                        "%s capture_window success backend=%s attempt=%s",
                        self._session_prefix(),
                        backend,
                        attempt + 1,
                    )
                    return (pixmap, backend)
                LOGGER.debug(
                    "%s capture_window rejected backend=%s attempt=%s blank_or_null=%s",
                    self._session_prefix(),
                    backend,
                    attempt + 1,
                    is_blank_or_null,
                )
                if attempt == 0:
                    time.sleep(0.12)
        LOGGER.error(
            "%s capture_window failed hwnd=%s primary=%s",
            self._session_prefix(),
            hwnd,
            primary_backend,
        )
        return (None, "")

    @staticmethod
    def window_title(hwnd: int) -> str:
        """Best-effort window title for a native handle."""

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

    @staticmethod
    def window_class_name(hwnd: int) -> str:
        """Class name for a native window."""

        if hwnd <= 0:
            return ""
        buffer = ctypes.create_unicode_buffer(256)
        USER32.GetClassNameW(hwnd, buffer, len(buffer))
        return buffer.value.strip()

    @staticmethod
    def window_process_name(hwnd: int) -> str:
        """Resolve executable name for the owning window process."""

        process_id = WindowCaptureService.window_process_id(hwnd)
        if process_id <= 0:
            return ""
        process_handle = KERNEL32.OpenProcess(
            PROCESS_QUERY_LIMITED_INFORMATION | PROCESS_VM_READ,
            False,
            process_id,
        )
        if not process_handle:
            return ""
        try:
            buffer = ctypes.create_unicode_buffer(260)
            copied = PSAPI.GetModuleBaseNameW(
                process_handle,
                None,
                buffer,
                len(buffer),
            )
            if copied <= 0:
                return ""
            return buffer.value.strip()
        finally:
            KERNEL32.CloseHandle(process_handle)

    @staticmethod
    def window_process_id(hwnd: int) -> int:
        """Resolve process id for one native window handle."""

        if hwnd <= 0:
            return 0
        pid = wintypes.DWORD(0)
        USER32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        return int(pid.value)

    def wait_after_scroll(self, delay_ms: int) -> None:
        """Sleep helper for scroll-and-capture loops."""

        delay_s = max(0.0, float(delay_ms) / 1000.0)
        time.sleep(delay_s)

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
    def _is_capture_candidate(hwnd: int, *, include_minimized: bool = False) -> bool:
        if hwnd <= 0:
            return False
        if not bool(USER32.IsWindowVisible(hwnd)):
            return False
        if bool(USER32.IsIconic(hwnd)):
            return bool(include_minimized)
        return True

    @classmethod
    def _is_alt_tab_target_candidate(cls, hwnd: int, own_hwnd: int) -> bool:
        if hwnd <= 0 or int(hwnd) == int(own_hwnd):
            return False
        if not cls._is_capture_candidate(int(hwnd)):
            return False
        class_name = cls.window_class_name(int(hwnd))
        if class_name in ALT_TAB_EXCLUDED_CLASSES:
            return False
        return True

    @staticmethod
    def _focus_window(hwnd: int) -> None:
        USER32.BringWindowToTop(hwnd)
        USER32.SetWindowPos(
            hwnd,
            HWND_TOP,
            0,
            0,
            0,
            0,
            SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE,
        )
        USER32.SetForegroundWindow(hwnd)
        USER32.SetActiveWindow(hwnd)
        USER32.SetFocus(hwnd)

    @staticmethod
    def _window_thread_id(hwnd: int) -> int:
        if hwnd <= 0:
            return 0
        pid = wintypes.DWORD(0)
        return int(USER32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid)))

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

    def _ordered_backends(self, primary_backend: str) -> list[str]:
        normalized = str(primary_backend or "").strip().lower()
        if normalized not in CAPTURE_BACKENDS:
            normalized = DEFAULT_CAPTURE_BACKEND
        return [normalized, *[name for name in CAPTURE_BACKENDS if name != normalized]]

    def _capture_with_backend(
        self,
        hwnd: int,
        backend: str,
        *,
        frame_region: str,
        include_mouse_cursor: bool,
    ) -> QPixmap | None:
        if backend == "screen_region_gdi":
            return self._capture_screen_region_gdi(
                hwnd,
                frame_region=frame_region,
                include_mouse_cursor=include_mouse_cursor,
            )
        if backend == "print_window":
            return self._capture_print_window(
                hwnd,
                frame_region=frame_region,
                include_mouse_cursor=include_mouse_cursor,
            )
        if backend == "qt_grab_window":
            return self._capture_qt_window(
                hwnd,
                frame_region=frame_region,
                include_mouse_cursor=include_mouse_cursor,
            )
        LOGGER.error("%s unknown capture backend=%s", self._session_prefix(), backend)
        return None

    def _capture_qt_window(
        self,
        hwnd: int,
        *,
        frame_region: str,
        include_mouse_cursor: bool,
    ) -> QPixmap | None:
        screen = self._screen_for_window(hwnd)
        if screen is None:
            LOGGER.debug("%s qt_grab_window no screen hwnd=%s", self._session_prefix(), hwnd)
            return None
        normalized_region = self._normalize_frame_region(frame_region)
        pixmap: QPixmap
        if normalized_region == "client_area":
            window_rect = self._window_rect(hwnd)
            client_rect = self._client_rect(hwnd)
            if window_rect is None or client_rect is None:
                LOGGER.debug(
                    "%s qt_grab_window client-area fallback to full window hwnd=%s",
                    self._session_prefix(),
                    hwnd,
                )
                pixmap = screen.grabWindow(hwnd)
            else:
                window_left, window_top, _window_width, _window_height = window_rect
                client_left, client_top, client_width, client_height = client_rect
                offset_x = max(0, client_left - window_left)
                offset_y = max(0, client_top - window_top)
                pixmap = screen.grabWindow(
                    hwnd,
                    offset_x,
                    offset_y,
                    client_width,
                    client_height,
                )
        else:
            pixmap = screen.grabWindow(hwnd)
        if pixmap.isNull():
            LOGGER.debug("%s qt_grab_window null pixmap hwnd=%s", self._session_prefix(), hwnd)
            return None
        if include_mouse_cursor:
            LOGGER.debug(
                "%s qt_grab_window cursor inclusion not supported; returning frame without cursor hwnd=%s",
                self._session_prefix(),
                hwnd,
            )
        return pixmap

    @staticmethod
    def _window_rect(hwnd: int) -> tuple[int, int, int, int] | None:
        rect = wintypes.RECT()
        if not USER32.GetWindowRect(hwnd, ctypes.byref(rect)):
            return None
        width = max(0, int(rect.right - rect.left))
        height = max(0, int(rect.bottom - rect.top))
        if width <= 0 or height <= 0:
            return None
        return (int(rect.left), int(rect.top), width, height)

    @staticmethod
    def _client_rect(hwnd: int) -> tuple[int, int, int, int] | None:
        client = wintypes.RECT()
        if not USER32.GetClientRect(hwnd, ctypes.byref(client)):
            return None
        top_left = wintypes.POINT(int(client.left), int(client.top))
        bottom_right = wintypes.POINT(int(client.right), int(client.bottom))
        if not USER32.ClientToScreen(hwnd, ctypes.byref(top_left)):
            return None
        if not USER32.ClientToScreen(hwnd, ctypes.byref(bottom_right)):
            return None
        width = max(0, int(bottom_right.x - top_left.x))
        height = max(0, int(bottom_right.y - top_left.y))
        if width <= 0 or height <= 0:
            return None
        return (int(top_left.x), int(top_left.y), width, height)

    def _capture_rect(self, hwnd: int, frame_region: str) -> tuple[int, int, int, int] | None:
        normalized = self._normalize_frame_region(frame_region)
        if normalized == "client_area":
            # Prefer client-area bounds so browser chrome/toolbars/status bars are excluded by default.
            client_rect = self._client_rect(hwnd)
            if client_rect is not None:
                return client_rect
            LOGGER.debug(
                "%s client-area rect unavailable; falling back to full window hwnd=%s",
                self._session_prefix(),
                hwnd,
            )
        return self._window_rect(hwnd)

    def _capture_screen_region_gdi(
        self,
        hwnd: int,
        *,
        frame_region: str,
        include_mouse_cursor: bool,
    ) -> QPixmap | None:
        rect = self._capture_rect(hwnd, frame_region)
        if rect is None:
            LOGGER.debug("%s gdi capture no rect hwnd=%s", self._session_prefix(), hwnd)
            return None
        left, top, width, height = rect
        desktop_hwnd = win32gui.GetDesktopWindow()
        desktop_dc = win32gui.GetWindowDC(desktop_hwnd)
        if desktop_dc == 0:
            LOGGER.debug("%s gdi capture no desktop dc hwnd=%s", self._session_prefix(), hwnd)
            return None
        src_dc = win32ui.CreateDCFromHandle(desktop_dc)
        mem_dc = src_dc.CreateCompatibleDC()
        bitmap = win32ui.CreateBitmap()
        bitmap.CreateCompatibleBitmap(src_dc, width, height)
        old_obj = mem_dc.SelectObject(bitmap)
        try:
            mem_dc.BitBlt((0, 0), (width, height), src_dc, (left, top), win32con.SRCCOPY)
            if include_mouse_cursor:
                self._draw_cursor_on_dc(
                    mem_dc,
                    capture_left=left,
                    capture_top=top,
                    capture_width=width,
                    capture_height=height,
                )
            return self._bitmap_to_pixmap(bitmap)
        except Exception:
            LOGGER.exception("%s gdi capture raised exception hwnd=%s", self._session_prefix(), hwnd)
            return None
        finally:
            mem_dc.SelectObject(old_obj)
            win32gui.DeleteObject(bitmap.GetHandle())
            mem_dc.DeleteDC()
            src_dc.DeleteDC()
            win32gui.ReleaseDC(desktop_hwnd, desktop_dc)

    def _capture_print_window(
        self,
        hwnd: int,
        *,
        frame_region: str,
        include_mouse_cursor: bool,
    ) -> QPixmap | None:
        window_rect = self._window_rect(hwnd)
        if window_rect is None:
            LOGGER.debug("%s print_window capture no rect hwnd=%s", self._session_prefix(), hwnd)
            return None
        window_left, window_top, width, height = window_rect
        normalized_region = self._normalize_frame_region(frame_region)
        hwnd_dc = win32gui.GetWindowDC(hwnd)
        if hwnd_dc == 0:
            LOGGER.debug(
                "%s print_window capture no window dc hwnd=%s",
                self._session_prefix(),
                hwnd,
            )
            return None
        src_dc = win32ui.CreateDCFromHandle(hwnd_dc)
        mem_dc = src_dc.CreateCompatibleDC()
        bitmap = win32ui.CreateBitmap()
        bitmap.CreateCompatibleBitmap(src_dc, width, height)
        old_obj = mem_dc.SelectObject(bitmap)
        try:
            result = USER32.PrintWindow(hwnd, mem_dc.GetSafeHdc(), PW_RENDERFULLCONTENT)
            if not bool(result):
                result = USER32.PrintWindow(hwnd, mem_dc.GetSafeHdc(), 0)
                if not bool(result):
                    LOGGER.debug(
                        "%s print_window capture returned false hwnd=%s",
                        self._session_prefix(),
                        hwnd,
                    )
                    return None
            if include_mouse_cursor:
                self._draw_cursor_on_dc(
                    mem_dc,
                    capture_left=window_left,
                    capture_top=window_top,
                    capture_width=width,
                    capture_height=height,
                )
            image = self._bitmap_to_image(bitmap)
            if image is None:
                return None
            if normalized_region == "client_area":
                client_rect = self._client_rect(hwnd)
                if client_rect is None:
                    LOGGER.debug(
                        "%s print_window client-area rect unavailable; using full frame hwnd=%s",
                        self._session_prefix(),
                        hwnd,
                    )
                else:
                    client_left, client_top, client_width, client_height = client_rect
                    offset_x = max(0, client_left - window_left)
                    offset_y = max(0, client_top - window_top)
                    max_x = min(width, offset_x + client_width)
                    max_y = min(height, offset_y + client_height)
                    if max_x > offset_x and max_y > offset_y:
                        image = image.crop((offset_x, offset_y, max_x, max_y))
            return self._image_to_pixmap(image)
        except Exception:
            LOGGER.exception(
                "%s print_window capture raised exception hwnd=%s",
                self._session_prefix(),
                hwnd,
            )
            return None
        finally:
            mem_dc.SelectObject(old_obj)
            win32gui.DeleteObject(bitmap.GetHandle())
            mem_dc.DeleteDC()
            src_dc.DeleteDC()
            win32gui.ReleaseDC(hwnd, hwnd_dc)

    @staticmethod
    def _bitmap_to_image(bitmap) -> Image.Image | None:
        info = bitmap.GetInfo()
        width = int(info.get("bmWidth", 0))
        height = int(info.get("bmHeight", 0))
        if width <= 0 or height <= 0:
            return None
        bits = bitmap.GetBitmapBits(True)
        if not bits:
            return None
        return Image.frombuffer("RGB", (width, height), bits, "raw", "BGRX", 0, 1)

    @staticmethod
    def _image_to_pixmap(image: Image.Image) -> QPixmap | None:
        pixmap = ImageQt.toqpixmap(image)
        if pixmap.isNull():
            return None
        return pixmap

    @classmethod
    def _bitmap_to_pixmap(cls, bitmap) -> QPixmap | None:
        image = cls._bitmap_to_image(bitmap)
        if image is None:
            return None
        return cls._image_to_pixmap(image)

    def _draw_cursor_on_dc(
        self,
        mem_dc,
        *,
        capture_left: int,
        capture_top: int,
        capture_width: int,
        capture_height: int,
    ) -> bool:
        try:
            flags, cursor_handle, cursor_pos = win32gui.GetCursorInfo()
        except Exception:
            LOGGER.exception("%s cursor info query failed", self._session_prefix())
            return False
        if int(flags) & CURSOR_SHOWING == 0:
            return False
        if not cursor_handle:
            return False
        cursor_x, cursor_y = int(cursor_pos[0]), int(cursor_pos[1])
        if not (
            capture_left <= cursor_x < capture_left + capture_width
            and capture_top <= cursor_y < capture_top + capture_height
        ):
            return False
        icon_info: tuple[int, int, int, int, int] | None = None
        try:
            icon_info = win32gui.GetIconInfo(cursor_handle)
            hotspot_x = int(icon_info[1])
            hotspot_y = int(icon_info[2])
            draw_x = cursor_x - capture_left - hotspot_x
            draw_y = cursor_y - capture_top - hotspot_y
            win32gui.DrawIconEx(
                mem_dc.GetSafeHdc(),
                draw_x,
                draw_y,
                cursor_handle,
                0,
                0,
                0,
                0,
                win32con.DI_NORMAL,
            )
            LOGGER.debug(
                "%s cursor composited into frame at (%s,%s)",
                self._session_prefix(),
                draw_x,
                draw_y,
            )
            return True
        except Exception:
            LOGGER.exception("%s cursor draw failed", self._session_prefix())
            return False
        finally:
            if icon_info is not None:
                mask_bmp = int(icon_info[3] or 0)
                color_bmp = int(icon_info[4] or 0)
                if mask_bmp:
                    win32gui.DeleteObject(mask_bmp)
                if color_bmp:
                    win32gui.DeleteObject(color_bmp)

    def _scroll_with_wheel_message(self, hwnd: int, *, direction: str = "down") -> bool:
        rect = self._window_rect(hwnd)
        if rect is None:
            LOGGER.error(
                "%s wheel-message failed: no window rect hwnd=%s",
                self._session_prefix(),
                hwnd,
            )
            return False
        left, top, width, height = rect
        x_pos = left + max(10, width // 2)
        y_pos = top + max(10, min(height - 10, height // 3))
        wheel_target = self.window_from_point(x_pos, y_pos) or hwnd
        if wheel_target <= 0:
            wheel_target = hwnd
        normalized_direction = "up" if str(direction).strip().lower() == "up" else "down"
        signed_delta = WHEEL_DELTA if normalized_direction == "up" else -WHEEL_DELTA
        wheel_delta = signed_delta & 0xFFFF
        wparam = (wheel_delta << 16) | 0
        lparam = ((y_pos & 0xFFFF) << 16) | (x_pos & 0xFFFF)
        try:
            USER32.SendMessageW(wheel_target, WM_MOUSEWHEEL, wparam, lparam)
            if wheel_target != hwnd:
                USER32.SendMessageW(hwnd, WM_MOUSEWHEEL, wparam, lparam)
            LOGGER.debug(
                "%s wheel-message sent direction=%s target=%s cursor_point=(%s,%s)",
                self._session_prefix(),
                normalized_direction,
                wheel_target,
                x_pos,
                y_pos,
            )
        except Exception:
            LOGGER.exception(
                "%s wheel-message raised exception hwnd=%s",
                self._session_prefix(),
                hwnd,
            )
            return False
        return True

    @staticmethod
    def _normalize_wheel_injection_mode(mode: str) -> str:
        normalized = str(mode or "").strip().lower()
        if normalized == "legacy_message_wheel":
            return "legacy_message_wheel"
        return "physical_center_sendinput"

    @staticmethod
    def _normalize_cursor_hold_mode(mode: str) -> str:
        normalized = str(mode or "").strip().lower()
        if normalized == "restore_each_step":
            return "restore_each_step"
        return "keep_at_center"

    @staticmethod
    def _normalize_frame_region(frame_region: str) -> str:
        normalized = str(frame_region or "").strip().lower()
        if normalized in CAPTURE_FRAME_REGIONS:
            return normalized
        return DEFAULT_CAPTURE_FRAME_REGION

    @staticmethod
    def _send_mouse_input(flags: int, mouse_data: int = 0) -> tuple[bool, int]:
        ctypes.set_last_error(0)
        input_event = INPUT()
        input_event.type = INPUT_MOUSE
        input_event.union.mi = MOUSEINPUT(
            dx=0,
            dy=0,
            mouseData=wintypes.DWORD(mouse_data),
            dwFlags=flags,
            time=0,
            dwExtraInfo=ULONG_PTR(0),
        )
        sent = int(USER32.SendInput(1, ctypes.byref(input_event), ctypes.sizeof(INPUT)))
        error_code = int(ctypes.get_last_error())
        return (sent == 1, error_code)

    @staticmethod
    def _send_mouse_wheel_down() -> tuple[bool, int]:
        wheel_delta = ctypes.c_uint32((-WHEEL_DELTA) & 0xFFFFFFFF).value
        return WindowCaptureService._send_mouse_input(MOUSEEVENTF_WHEEL, wheel_delta)

    @staticmethod
    def _send_mouse_wheel_up() -> tuple[bool, int]:
        wheel_delta = ctypes.c_uint32(WHEEL_DELTA & 0xFFFFFFFF).value
        return WindowCaptureService._send_mouse_input(MOUSEEVENTF_WHEEL, wheel_delta)

    @staticmethod
    def _send_mouse_left_click() -> tuple[bool, int]:
        down_ok, down_error = WindowCaptureService._send_mouse_input(MOUSEEVENTF_LEFTDOWN)
        up_ok, up_error = WindowCaptureService._send_mouse_input(MOUSEEVENTF_LEFTUP)
        if down_ok and up_ok:
            return (True, 0)
        if not down_ok:
            return (False, down_error)
        return (False, up_error)

    @staticmethod
    def _current_cursor_pos() -> tuple[int, int] | None:
        point = wintypes.POINT()
        if not bool(USER32.GetCursorPos(ctypes.byref(point))):
            return None
        return (int(point.x), int(point.y))

    def _window_center_point(self, hwnd: int) -> tuple[int, int] | None:
        rect = self._window_rect(hwnd)
        if rect is None:
            return None
        left, top, width, height = rect
        x_pos = int(left + max(2, width // 2))
        y_pos = int(top + max(2, height // 2))
        return (x_pos, y_pos)

    def _move_cursor_to_window_center(self, hwnd: int) -> bool:
        center = self._window_center_point(hwnd)
        if center is None:
            LOGGER.error(
                "%s could not resolve window center for hwnd=%s",
                self._session_prefix(),
                hwnd,
            )
            return False
        moved = bool(USER32.SetCursorPos(center[0], center[1]))
        actual = self._current_cursor_pos()
        if not moved:
            LOGGER.error(
                "%s SetCursorPos failed target_center=%s error_code=%s",
                self._session_prefix(),
                center,
                int(ctypes.get_last_error()),
            )
            return False
        if actual is None:
            LOGGER.error(
                "%s GetCursorPos failed after SetCursorPos target_center=%s",
                self._session_prefix(),
                center,
            )
            return False
        x_delta = abs(actual[0] - center[0])
        y_delta = abs(actual[1] - center[1])
        ok = x_delta <= CURSOR_VERIFY_TOLERANCE_PX and y_delta <= CURSOR_VERIFY_TOLERANCE_PX
        LOGGER.debug(
            "%s cursor-move target=%s actual=%s delta=(%s,%s) ok=%s",
            self._session_prefix(),
            center,
            actual,
            x_delta,
            y_delta,
            ok,
        )
        return ok

    @staticmethod
    def _is_blank_like(pixmap: QPixmap) -> bool:
        if pixmap.isNull():
            return True
        image = pixmap.toImage()
        if image.isNull():
            return True
        sample = image.scaled(
            64,
            64,
            Qt.AspectRatioMode.IgnoreAspectRatio,
            Qt.TransformationMode.FastTransformation,
        ).convertToFormat(QImage.Format.Format_RGB32)
        colors: set[tuple[int, int, int]] = set()
        luminance_min = 255
        luminance_max = 0
        for y_pos in range(0, sample.height(), 4):
            for x_pos in range(0, sample.width(), 4):
                color = sample.pixelColor(x_pos, y_pos)
                rgb = (color.red(), color.green(), color.blue())
                colors.add(rgb)
                lum = int((rgb[0] * 299 + rgb[1] * 587 + rgb[2] * 114) / 1000)
                luminance_min = min(luminance_min, lum)
                luminance_max = max(luminance_max, lum)
        if len(colors) <= 1:
            return True
        return (luminance_max - luminance_min) <= 3

    @staticmethod
    def _screen_for_window(hwnd: int) -> QScreen | None:
        rect = wintypes.RECT()
        screen: QScreen | None = None
        if USER32.GetWindowRect(hwnd, ctypes.byref(rect)):
            center_point = QPoint((rect.left + rect.right) // 2, (rect.top + rect.bottom) // 2)
            screen = QGuiApplication.screenAt(center_point)
        if screen is None:
            screen = QGuiApplication.primaryScreen()
        return screen
