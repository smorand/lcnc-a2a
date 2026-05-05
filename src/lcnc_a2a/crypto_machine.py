"""Derive a stable Fernet key from a per-machine identifier.

Used as a development-time fallback when ``LCNC_A2A_ENCRYPTION_KEY`` is not
provided. Produces the same key on the same machine across reboots, and a
different key on every other machine. **Never** rely on this in production or
across replicas; set the env var explicitly there.

Sources of machine identity:
  - macOS: ``IOPlatformUUID`` from ``ioreg -rd1 -c IOPlatformExpertDevice``.
  - Linux: ``/etc/machine-id`` (systemd), fallback ``/var/lib/dbus/machine-id``.

Windows is not supported; we raise ``UnsupportedPlatformError``.
"""

from __future__ import annotations

import base64
import platform
import re
import subprocess
from pathlib import Path

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

_HKDF_SALT = b"lcnc-a2a-v1"
_HKDF_INFO = b"encryption-key"
_IOREG_REGEX = re.compile(r'"IOPlatformUUID"\s*=\s*"([^"]+)"')


class UnsupportedPlatformError(RuntimeError):
    """Raised when no machine-id source is available for the current OS."""


class MachineIdUnavailableError(RuntimeError):
    """Raised when the platform is supported but the machine-id is missing/empty."""


def _read_macos_uuid() -> str:
    proc = subprocess.run(
        ["ioreg", "-rd1", "-c", "IOPlatformExpertDevice"],
        capture_output=True,
        text=True,
        timeout=5,
        check=False,
    )
    if proc.returncode != 0:
        raise MachineIdUnavailableError(f"ioreg failed: {proc.stderr.strip()!r}")
    match = _IOREG_REGEX.search(proc.stdout)
    if match is None:
        raise MachineIdUnavailableError("IOPlatformUUID not found in ioreg output")
    return match.group(1).strip()


def _read_linux_machine_id() -> str:
    for candidate in (Path("/etc/machine-id"), Path("/var/lib/dbus/machine-id")):
        if candidate.is_file():
            value = candidate.read_text(encoding="ascii").strip()
            if value:
                return value
    raise MachineIdUnavailableError("no /etc/machine-id or /var/lib/dbus/machine-id found")


def read_machine_id() -> str:
    """Return a stable identifier for the current machine."""
    system = platform.system()
    if system == "Darwin":
        return _read_macos_uuid()
    if system == "Linux":
        return _read_linux_machine_id()
    raise UnsupportedPlatformError(
        f"machine-id derivation not supported on {system!r}; set LCNC_A2A_ENCRYPTION_KEY explicitly."
    )


def derive_machine_fernet_key() -> str:
    """Derive a Fernet-compatible urlsafe-base64 key from the machine id."""
    machine_id = read_machine_id()
    hkdf = HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=_HKDF_SALT,
        info=_HKDF_INFO,
    )
    raw = hkdf.derive(machine_id.encode("utf-8"))
    return base64.urlsafe_b64encode(raw).decode("ascii")
