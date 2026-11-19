"""The few colours the app chooses itself. Everything else follows the system theme."""

from __future__ import annotations

from PySide6.QtGui import QColor, QPalette

# (light theme, dark theme) pairs, each readable on that theme's window background.
_TONES = {
    "red": ("#b3261e", "#ff8a80"),
    "amber": ("#855800", "#ffc857"),
    "green": ("#1a7f37", "#7ee787"),
    "track": ("#d8dde3", "#4a4f57"),
    "bar": ("#3b82c4", "#5aa2e6"),
}


def tone(name: str, palette: QPalette) -> QColor:
    dark = palette.window().color().lightness() < 128
    return QColor(_TONES[name][dark])
