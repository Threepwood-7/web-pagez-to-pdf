"""Stable widget naming helpers for deterministic Qt object identity."""

from __future__ import annotations


def window_widget_id(window_id: str) -> str:
    """Return the canonical widget identifier for the main window."""

    return f"window:{window_id.strip()}"


def control_widget_id(window_id: str, control: str) -> str:
    """Return the canonical widget identifier for a window control."""

    return f"{window_widget_id(window_id)}:control:{control.strip()}"
