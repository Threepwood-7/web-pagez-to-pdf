"""Stable widget naming helpers for deterministic Qt object identity."""

from __future__ import annotations

import re


def window_widget_id(window_id: str) -> str:
    """Return the canonical widget identifier for the main window."""

    return f"window:{window_id.strip()}"


def control_widget_id(window_id: str, control: str) -> str:
    """Return the canonical widget identifier for a window control."""

    return f"{window_widget_id(window_id)}:control:{control.strip()}"


def object_name_for_id(widget_id: str) -> str:
    """Convert a widget identifier into a Qt-compatible object name."""

    normalized = re.sub(r"[^A-Za-z0-9_]+", "_", str(widget_id)).strip("_")
    if not normalized:
        return "widget"
    if normalized[0].isdigit():
        return f"w_{normalized}"
    return normalized
