"""Theme tokens for the LCNC A2A Builder UI."""

from __future__ import annotations

from lcnc_a2a.themes.g10 import G10_TOKENS
from lcnc_a2a.themes.g100 import G100_TOKENS
from lcnc_a2a.themes.tokens import ThemeTokens
from lcnc_a2a.themes.v2 import V2_TOKENS

DEFAULT_TOKENS = G100_TOKENS

ALLOWED_THEMES: tuple[str, ...] = ("g100", "g10", "v2")
THEME_LABELS: dict[str, str] = {
    "g100": "Carbon dark",
    "g10": "Carbon light",
    "v2": "EI",
}


def is_valid_theme(name: str | None) -> bool:
    """Return True if ``name`` is one of the registered themes."""
    return name in ALLOWED_THEMES


__all__ = [
    "ALLOWED_THEMES",
    "DEFAULT_TOKENS",
    "G10_TOKENS",
    "G100_TOKENS",
    "THEME_LABELS",
    "V2_TOKENS",
    "ThemeTokens",
    "is_valid_theme",
]
