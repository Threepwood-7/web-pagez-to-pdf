from __future__ import annotations

import logging
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QSettings, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from threep_commons.logging import setup_logging_from_identity
from threep_commons.paths import configure_qsettings, resolve_app_data_dir

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_DIR = REPO_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

TARGET_PROCESS = "chrome.exe"
TARGET_TITLE_NEEDLE = "pressione e volume"
MAX_ATTEMPTS = 7
ATTEMPT_TIMEOUT_SECONDS = 120.0
STOP_REASON_PATTERN = re.compile(
    r"full capture done frame_count=(?P<frames>\d+) stop_reason=(?P<reason>[a-z_]+)"
)
TRIM_PATTERN = re.compile(
    r"full-capture complete stop_reason=[a-z_]+ frames=\d+ "
    r"trim_top_px=(?P<top>\d+) trim_bottom_px=(?P<bottom>\d+)"
)


@dataclass(frozen=True)
class AttemptProfile:
    backend: str
    scroll_mode: str
    wheel_mode: str


PROFILES: tuple[AttemptProfile, ...] = (
    AttemptProfile(
        "screen_region_gdi", "wheel_then_pagedown", "physical_center_sendinput"
    ),
    AttemptProfile("print_window", "wheel_then_pagedown", "physical_center_sendinput"),
    AttemptProfile(
        "qt_grab_window", "wheel_then_pagedown", "physical_center_sendinput"
    ),
    AttemptProfile("screen_region_gdi", "wheel_pagedown", "physical_center_sendinput"),
    AttemptProfile("print_window", "wheel_pagedown", "physical_center_sendinput"),
    AttemptProfile("qt_grab_window", "wheel_pagedown", "physical_center_sendinput"),
    AttemptProfile(
        "screen_region_gdi", "wheel_click_pagedown", "physical_center_sendinput"
    ),
)


def _log_path() -> Path:
    app_identity, default_log_filename, _main_window = _load_app_types()
    return resolve_app_data_dir(app_identity) / "logs" / default_log_filename


def _load_app_types():
    from web_pagez_to_pdf.constants import APP_IDENTITY, DEFAULT_LOG_FILENAME
    from web_pagez_to_pdf.main_window import MainWindow

    return APP_IDENTITY, DEFAULT_LOG_FILENAME, MainWindow


def _read_log_delta(log_file: Path, offset: int) -> str:
    if not log_file.exists():
        return ""
    with log_file.open("r", encoding="utf-8", errors="replace") as handle:
        handle.seek(max(0, offset))
        return handle.read()


def _latest_stop_reason(chunk: str) -> tuple[int, str]:
    last_frames = 0
    last_reason = ""
    for match in STOP_REASON_PATTERN.finditer(chunk):
        last_frames = int(match.group("frames"))
        last_reason = match.group("reason")
    return (last_frames, last_reason)


def _latest_trim_values(chunk: str) -> tuple[int, int]:
    top_trim = 0
    bottom_trim = 0
    for match in TRIM_PATTERN.finditer(chunk):
        top_trim = int(match.group("top"))
        bottom_trim = int(match.group("bottom"))
    return (top_trim, bottom_trim)


def _process_events(app: QApplication, delay_s: float = 0.0) -> None:
    app.processEvents()
    if delay_s > 0:
        time.sleep(delay_s)
        app.processEvents()


def _configure_window_for_attempt(window, profile: AttemptProfile) -> None:
    window._set_capture_backend_combo(profile.backend)
    window._set_scroll_mode_combo(profile.scroll_mode)
    window._set_wheel_injection_combo(profile.wheel_mode)
    window._set_capture_frame_region_combo("client_area")
    window.capture_include_mouse_checkbox.setChecked(False)
    window.capture_scroll_to_top_checkbox.setChecked(True)
    window.max_pages_spin.setValue(18)


def _pick_target_window(window) -> bool:
    windows = window._capture_service.list_top_windows(int(window.winId()))
    candidates = [
        info
        for info in windows
        if info.process_name.strip().lower() == TARGET_PROCESS
        and TARGET_TITLE_NEEDLE in info.title.strip().lower()
    ]
    if not candidates:
        return False
    selected = min(candidates, key=lambda item: item.sort_key)
    window._set_target(window._picked_window_from_info(selected))
    return True


def _wait_for_capture_finish(app: QApplication, window) -> bool:
    deadline = time.monotonic() + ATTEMPT_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        _process_events(app, 0.05)
        worker = window._capture_worker
        if worker is None:
            _process_events(app, 0.1)
            return True
    return False


def _run_attempt(
    app: QApplication, attempt_number: int, profile: AttemptProfile
) -> tuple[bool, str]:
    _app_identity, _default_log_filename, main_window_type = _load_app_types()
    settings = QSettings()
    settings.clear()
    settings.sync()
    _process_events(app, 0.05)

    window = main_window_type()
    window.show()
    _process_events(app, 0.2)

    if not _pick_target_window(window):
        window.close()
        _process_events(app, 0.1)
        return (False, "target_not_found")

    _configure_window_for_attempt(window, profile)
    log_file = _log_path()
    start_offset = int(log_file.stat().st_size) if log_file.exists() else 0
    queue_before = len(window._queue)

    QTest.mouseClick(window.capture_full_button, Qt.MouseButton.LeftButton)
    finished = _wait_for_capture_finish(app, window)
    if not finished:
        window._request_stop()
        _process_events(app, 0.5)
        window.close()
        _process_events(app, 0.1)
        return (False, "timeout")

    log_chunk = _read_log_delta(log_file, start_offset)
    log_frames, stop_reason = _latest_stop_reason(log_chunk)
    new_item = window._queue[-1] if len(window._queue) > queue_before else None
    queue_frames = int(new_item.frame_count or 0) if new_item is not None else 0
    frame_count = max(log_frames, queue_frames)
    has_client_area = "frame_region=client_area" in log_chunk
    has_scroll_to_top_log = (
        "scroll-to-top preflight wheel_up_ok=True home_sent=True" in log_chunk
    )
    trim_top_px, trim_bottom_px = _latest_trim_values(log_chunk)
    has_trim_log = "trim_top_px=" in log_chunk and "trim_bottom_px=" in log_chunk
    has_any_trim = trim_top_px > 0 or trim_bottom_px > 0

    success = (
        bool(stop_reason)
        and stop_reason != "capture_failed"
        and frame_count > 1
        and has_client_area
        and has_scroll_to_top_log
        and has_trim_log
        and has_any_trim
    )

    outcome = (
        f"attempt={attempt_number} backend={profile.backend} scroll={profile.scroll_mode} "
        f"stop_reason={stop_reason or 'missing'} frames={frame_count} "
        f"client_area={has_client_area} scroll_to_top_log={has_scroll_to_top_log} "
        f"trim_top_px={trim_top_px} trim_bottom_px={trim_bottom_px}"
    )
    print(outcome)

    window.close()
    _process_events(app, 0.1)
    return (success, outcome)


def main() -> int:
    app_identity, _default_log_filename, _main_window = _load_app_types()
    configure_qsettings(app_identity)
    setup_logging_from_identity(app_identity, level=logging.INFO, console=True)
    logging.getLogger("web_pagez_to_pdf.capture").setLevel(logging.DEBUG)
    app = QApplication.instance() or QApplication(sys.argv)
    app.setOrganizationName(app_identity.org_name)
    app.setApplicationName(app_identity.app_name)
    app.setApplicationDisplayName(app_identity.display_name)

    for attempt_index, profile in enumerate(PROFILES[:MAX_ATTEMPTS], start=1):
        success, info = _run_attempt(app, attempt_index, profile)
        if success:
            print(f"SUCCESS {info}")
            return 0
    print("FAIL no successful full-capture attempt after 7 tries")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
