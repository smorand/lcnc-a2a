"""CSRF token generation and validation via itsdangerous."""

from __future__ import annotations

from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

CSRF_FIELD_NAME = "csrf_token"


class CSRFManager:
    """Issues and validates short-lived CSRF tokens."""

    __slots__ = ("_max_age", "_serializer")

    def __init__(self, secret: str, *, max_age_seconds: int = 3600) -> None:
        self._serializer = URLSafeTimedSerializer(secret, salt="csrf")
        self._max_age = max_age_seconds

    def generate(self) -> str:
        """Mint a new CSRF token."""
        return self._serializer.dumps("csrf")

    def validate(self, token: str | None) -> bool:
        """Return True iff the token is well-signed and unexpired."""
        if not token:
            return False
        try:
            self._serializer.loads(token, max_age=self._max_age)
        except (BadSignature, SignatureExpired):
            return False
        return True
