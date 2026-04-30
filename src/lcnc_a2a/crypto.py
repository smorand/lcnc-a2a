"""Fernet symmetric encryption utility and startup key validation."""

from __future__ import annotations

import sys

from cryptography.fernet import Fernet, InvalidToken

ENCRYPTION_KEY_REQUIRED_MESSAGE = "LCNC_A2A_ENCRYPTION_KEY is required"


class CryptoService:
    """Encrypt and decrypt secrets at rest using Fernet."""

    __slots__ = ("_fernet",)

    def __init__(self, key: str) -> None:
        try:
            self._fernet = Fernet(key.encode("utf-8") if isinstance(key, str) else key)
        except (ValueError, TypeError) as exc:
            raise InvalidEncryptionKeyError(str(exc)) from exc

    def encrypt(self, data: bytes) -> bytes:
        """Encrypt raw bytes."""
        return self._fernet.encrypt(data)

    def decrypt(self, token: bytes) -> bytes:
        """Decrypt a Fernet token to raw bytes."""
        try:
            return self._fernet.decrypt(token)
        except InvalidToken as exc:
            raise InvalidEncryptionKeyError("invalid Fernet token") from exc


class InvalidEncryptionKeyError(ValueError):
    """Raised when the encryption key is missing or malformed."""


def fail_missing_key() -> None:
    """Write the required-message to stderr and exit with status 1."""
    sys.stderr.write(ENCRYPTION_KEY_REQUIRED_MESSAGE + "\n")
    sys.stderr.flush()
    sys.exit(1)
