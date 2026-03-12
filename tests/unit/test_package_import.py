"""Import-level smoke tests for the package."""

import importlib


def test_package_importable() -> None:
    assert importlib.import_module("web_pagez_to_pdf")
