"""Carbon Design System theme tokens (g100 dark, g10 light)."""

from __future__ import annotations

from lcnc_a2a.themes.g10 import G10_TOKENS
from lcnc_a2a.themes.g100 import G100_TOKENS
from lcnc_a2a.themes.tokens import ThemeTokens

DEFAULT_TOKENS = G100_TOKENS

__all__ = ["DEFAULT_TOKENS", "G10_TOKENS", "G100_TOKENS", "ThemeTokens"]
