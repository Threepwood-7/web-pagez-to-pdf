"""Global shortcut polling for capture commands."""

from __future__ import annotations

import ctypes
from dataclasses import dataclass

from PySide6.QtCore import QObject, QTimer, Signal

USER32 = ctypes.windll.user32
USER32.GetAsyncKeyState.argtypes = [ctypes.c_int]
USER32.GetAsyncKeyState.restype = ctypes.c_short

VK_CONTROL = 0x11
VK_SHIFT = 0x10
VK_C = 0x43
VK_S = 0x53
VK_X = 0x58


@dataclass(frozen=True, slots=True)
class Hotkey:
    """Virtual-key set for one global shortcut."""

    primary_vk: int
    ctrl: bool = True
    shift: bool = True


class GlobalHotkeyPoller(QObject):
    """Poll keyboard state and emit capture hotkey signals system-wide."""

    capture_selected_requested = Signal()
    capture_full_requested = Signal()
    stop_capture_requested = Signal()

    def __init__(self) -> None:
        super().__init__()
        self._timer = QTimer(self)
        self._timer.setInterval(90)
        self._timer.timeout.connect(self._poll)
        self._capture_hotkey = Hotkey(primary_vk=VK_C)
        self._full_hotkey = Hotkey(primary_vk=VK_S)
        self._stop_hotkey = Hotkey(primary_vk=VK_X)
        self._previous_state: dict[Hotkey, bool] = {
            self._capture_hotkey: False,
            self._full_hotkey: False,
            self._stop_hotkey: False,
        }

    def start(self) -> None:
        """Begin polling for hotkey transitions."""

        self._timer.start()

    def stop(self) -> None:
        """Stop polling."""

        self._timer.stop()

    def _poll(self) -> None:
        mapping = (
            (self._capture_hotkey, self.capture_selected_requested.emit),
            (self._full_hotkey, self.capture_full_requested.emit),
            (self._stop_hotkey, self.stop_capture_requested.emit),
        )
        for hotkey, callback in mapping:
            pressed = self._is_hotkey_pressed(hotkey)
            previously_pressed = self._previous_state[hotkey]
            if pressed and not previously_pressed:
                callback()
            self._previous_state[hotkey] = pressed

    @staticmethod
    def _is_hotkey_pressed(hotkey: Hotkey) -> bool:
        ctrl_ok = not hotkey.ctrl or _is_vk_pressed(VK_CONTROL)
        shift_ok = not hotkey.shift or _is_vk_pressed(VK_SHIFT)
        primary_ok = _is_vk_pressed(hotkey.primary_vk)
        return ctrl_ok and shift_ok and primary_ok


def _is_vk_pressed(vk: int) -> bool:
    state = USER32.GetAsyncKeyState(vk)
    return bool(state & 0x8000)
