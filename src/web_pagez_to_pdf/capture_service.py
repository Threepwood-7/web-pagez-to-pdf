"""Windows capture primitives for window selection and screenshot automation."""

from __future__ import annotations

import ctypes
import time
from ctypes import wintypes
from dataclasses import dataclass

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
KEYEVENTF_KEYUP = 0x0002
WM_MOUSEWHEEL = 0x020A
WHEEL_DELTA = 120
ULONG_PTR = ctypes.c_ulonglong if ctypes.sizeof(ctypes.c_void_p) == 8 else ctypes.c_ulong
LRESULT = ctypes.c_ssize_t
PW_RENDERFULLCONTENT = 0x00000002
CAPTURE_BACKENDS = ("screen_region_gdi", "qt_grab_window", "print_window")
DEFAULT_CAPTURE_BACKEND = "screen_region_gdi"

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
USER32.SetForegroundWindow.argtypes = [wintypes.HWND]
USER32.SetForegroundWindow.restype = wintypes.BOOL
USER32.SetFocus.argtypes = [wintypes.HWND]
USER32.SetFocus.restype = wintypes.HWND
USER32.PrintWindow.argtypes = [wintypes.HWND, wintypes.HDC, wintypes.UINT]
USER32.PrintWindow.restype = wintypes.BOOL
USER32.SendMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
USER32.SendMessageW.restype = LRESULT
USER32.keybd_event.argtypes = [
    wintypes.BYTE,
    wintypes.BYTE,
    wintypes.DWORD,
    ULONG_PTR,
]

KERNEL32 = ctypes.windll.kernel32
KERNEL32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
KERNEL32.OpenProcess.restype = wintypes.HANDLE
KERNEL32.CloseHandle.argtypes = [wintypes.HANDLE]
KERNEL32.CloseHandle.restype = wintypes.BOOL

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


@dataclass(slots=True)
class WindowInfo:
    """Metadata for a visible top-level window."""

    hwnd: int
    title: str
    process_name: str
    class_name: str

    @property
    def label(self) -> str:
        if self.process_name:
            return f"{self.title} ({self.process_name})"
        return self.title


class WindowCaptureService:
    """Track window focus and provide capture automation helpers."""

    def __init__(self) -> None:
        self._foreground_history: list[int] = []

    def record_foreground_window(self) -> int:
        """Append current foreground window handle into history."""

        hwnd = int(USER32.GetForegroundWindow())
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

    def list_top_windows(self, own_hwnd: int) -> list[WindowInfo]:
        """Enumerate currently visible top-level windows."""

        windows: list[WindowInfo] = []

        @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        def _enum_proc(hwnd: int, _lparam: int) -> bool:
            if hwnd == own_hwnd:
                return True
            if not self._is_capture_candidate(hwnd):
                return True
            title = self.window_title(hwnd).strip()
            if not title:
                return True
            windows.append(
                WindowInfo(
                    hwnd=int(hwnd),
                    title=title,
                    process_name=self.window_process_name(hwnd),
                    class_name=self.window_class_name(hwnd),
                )
            )
            return True

        USER32.EnumWindows(_enum_proc, 0)
        windows.sort(key=lambda item: item.label.lower())
        return windows

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
        """Attempt foreground/focus without changing target window state."""

        if hwnd <= 0:
            return (False, "Target window handle is invalid.")
        if not bool(USER32.IsWindowVisible(hwnd)):
            return (False, "Target window is not visible. Bring it on-screen and retry.")
        if bool(USER32.IsIconic(hwnd)):
            return (False, "Target window is minimized. Restore it manually, then retry.")

        foreground_ok = bool(USER32.SetForegroundWindow(hwnd))
        USER32.SetFocus(hwnd)
        if not foreground_ok:
            return (False, "Could not focus target window. Click it once, then retry.")
        return (True, "")

    @staticmethod
    def is_foreground_window(hwnd: int) -> bool:
        """Return whether the given hwnd currently owns foreground focus."""

        return hwnd > 0 and int(USER32.GetForegroundWindow()) == int(hwnd)

    def ensure_window_foreground(self, hwnd: int) -> tuple[bool, str]:
        """Ensure target is in foreground without changing window state."""

        if self.is_foreground_window(hwnd):
            return (True, "")
        return self.activate_window(hwnd)

    @staticmethod
    def send_page_down() -> None:
        """Emit a PageDown keyboard event to the current foreground window."""

        USER32.keybd_event(VK_NEXT, 0, 0, 0)
        USER32.keybd_event(VK_NEXT, 0, KEYEVENTF_KEYUP, 0)

    def scroll_target_window(self, hwnd: int, strategy: str = "hybrid_wheel_pagedown") -> str:
        """Inject scroll input toward target window using requested strategy."""

        normalized = str(strategy or "").strip().lower()
        if normalized in {"pagedown_only", "pagedown", "page_down"}:
            self.send_page_down()
            return "pagedown"
        if normalized in {"wheel_only", "wheel"}:
            if self._scroll_with_wheel(hwnd):
                return "wheel"
            return ""
        if self._scroll_with_wheel(hwnd):
            return "wheel"
        self.send_page_down()
        return "pagedown"

    def capture_window(
        self,
        hwnd: int,
        *,
        primary_backend: str = DEFAULT_CAPTURE_BACKEND,
    ) -> tuple[QPixmap | None, str]:
        """Capture one window using selected backend and deterministic fallbacks."""

        if hwnd <= 0:
            return (None, "")

        ordered_backends = self._ordered_backends(primary_backend)
        for backend in ordered_backends:
            for attempt in range(2):
                pixmap = self._capture_with_backend(hwnd, backend)
                if pixmap is not None and not self._is_blank_like(pixmap):
                    return (pixmap, backend)
                if attempt == 0:
                    time.sleep(0.12)
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

        if hwnd <= 0:
            return ""
        pid = wintypes.DWORD(0)
        USER32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value <= 0:
            return ""
        process_handle = KERNEL32.OpenProcess(
            PROCESS_QUERY_LIMITED_INFORMATION | PROCESS_VM_READ,
            False,
            pid.value,
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

    def _ordered_backends(self, primary_backend: str) -> list[str]:
        normalized = str(primary_backend or "").strip().lower()
        if normalized not in CAPTURE_BACKENDS:
            normalized = DEFAULT_CAPTURE_BACKEND
        return [normalized, *[name for name in CAPTURE_BACKENDS if name != normalized]]

    def _capture_with_backend(self, hwnd: int, backend: str) -> QPixmap | None:
        if backend == "screen_region_gdi":
            return self._capture_screen_region_gdi(hwnd)
        if backend == "print_window":
            return self._capture_print_window(hwnd)
        if backend == "qt_grab_window":
            return self._capture_qt_window(hwnd)
        return None

    def _capture_qt_window(self, hwnd: int) -> QPixmap | None:
        screen = self._screen_for_window(hwnd)
        if screen is None:
            return None
        pixmap = screen.grabWindow(hwnd)
        if pixmap.isNull():
            return None
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

    def _capture_screen_region_gdi(self, hwnd: int) -> QPixmap | None:
        rect = self._window_rect(hwnd)
        if rect is None:
            return None
        left, top, width, height = rect
        desktop_hwnd = win32gui.GetDesktopWindow()
        desktop_dc = win32gui.GetWindowDC(desktop_hwnd)
        if desktop_dc == 0:
            return None
        src_dc = win32ui.CreateDCFromHandle(desktop_dc)
        mem_dc = src_dc.CreateCompatibleDC()
        bitmap = win32ui.CreateBitmap()
        bitmap.CreateCompatibleBitmap(src_dc, width, height)
        old_obj = mem_dc.SelectObject(bitmap)
        try:
            mem_dc.BitBlt((0, 0), (width, height), src_dc, (left, top), win32con.SRCCOPY)
            return self._bitmap_to_pixmap(bitmap)
        except Exception:
            return None
        finally:
            mem_dc.SelectObject(old_obj)
            win32gui.DeleteObject(bitmap.GetHandle())
            mem_dc.DeleteDC()
            src_dc.DeleteDC()
            win32gui.ReleaseDC(desktop_hwnd, desktop_dc)

    def _capture_print_window(self, hwnd: int) -> QPixmap | None:
        rect = self._window_rect(hwnd)
        if rect is None:
            return None
        _left, _top, width, height = rect
        hwnd_dc = win32gui.GetWindowDC(hwnd)
        if hwnd_dc == 0:
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
                    return None
            return self._bitmap_to_pixmap(bitmap)
        except Exception:
            return None
        finally:
            mem_dc.SelectObject(old_obj)
            win32gui.DeleteObject(bitmap.GetHandle())
            mem_dc.DeleteDC()
            src_dc.DeleteDC()
            win32gui.ReleaseDC(hwnd, hwnd_dc)

    @staticmethod
    def _bitmap_to_pixmap(bitmap) -> QPixmap | None:
        info = bitmap.GetInfo()
        width = int(info.get("bmWidth", 0))
        height = int(info.get("bmHeight", 0))
        if width <= 0 or height <= 0:
            return None
        bits = bitmap.GetBitmapBits(True)
        if not bits:
            return None
        image = Image.frombuffer("RGB", (width, height), bits, "raw", "BGRX", 0, 1)
        pixmap = ImageQt.toqpixmap(image)
        if pixmap.isNull():
            return None
        return pixmap

    def _scroll_with_wheel(self, hwnd: int) -> bool:
        rect = self._window_rect(hwnd)
        if rect is None:
            return False
        left, top, width, height = rect
        x_pos = left + max(10, width // 2)
        y_pos = top + max(10, min(height - 10, height // 3))
        wheel_target = self.window_from_point(x_pos, y_pos) or hwnd
        if wheel_target <= 0:
            wheel_target = hwnd
        wheel_delta = (-WHEEL_DELTA) & 0xFFFF
        wparam = (wheel_delta << 16) | 0
        lparam = ((y_pos & 0xFFFF) << 16) | (x_pos & 0xFFFF)
        try:
            USER32.SendMessageW(wheel_target, WM_MOUSEWHEEL, wparam, lparam)
            if wheel_target != hwnd:
                USER32.SendMessageW(hwnd, WM_MOUSEWHEEL, wparam, lparam)
        except Exception:
            return False
        return True

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
