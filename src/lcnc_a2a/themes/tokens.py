"""Carbon Design System theme tokens."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ThemeTokens:
    """Carbon Design System token set; field names match the web-a2a sibling."""

    bg_primary: str
    bg_secondary: str
    bg_tertiary: str
    text_primary: str
    text_secondary: str
    text_inverse: str
    border: str
    interactive: str
    interactive_hover: str
    danger: str
    success: str
    warning: str
    font_family: str
    font_mono: str
