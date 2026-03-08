from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from PySide6.QtWidgets import QWidget

if TYPE_CHECKING:
    from pytestqt.qtbot import QtBot


@pytest.fixture
def window(qtbot: QtBot) -> QWidget:
    widget = QWidget()
    qtbot.addWidget(widget)
    widget.show()
    yield widget
    widget.close()
