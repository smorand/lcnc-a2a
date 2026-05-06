"""Dialect-agnostic column type helpers.

The app supports two backends: PostgreSQL (production) and SQLite (local
self-host without Docker). We keep PG's optimisations where they matter
(JSONB on PG, native UUID on PG) and degrade gracefully on SQLite via
SQLAlchemy's ``with_variant`` — application code never sees the
difference because it always reads/writes Python objects.

Usage in models::

    from lcnc_a2a.models.types import PkUuid, JsonField

    id: Mapped[uuid.UUID] = mapped_column(PkUuid(), primary_key=True, default=uuid.uuid4)
    payload: Mapped[Any | None] = mapped_column(JsonField(), nullable=True)
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import JSON, Uuid
from sqlalchemy.dialects.postgresql import JSONB


def PkUuid() -> Uuid[uuid.UUID]:
    """UUID column type: native ``uuid`` on PG, CHAR(32) on SQLite."""
    return Uuid(as_uuid=True)


def JsonField() -> JSON:
    """JSON-blob column type: ``JSONB`` on PG (binary, indexable, GIN-able);
    portable ``JSON`` (TEXT with JSON1 validation) elsewhere.

    Application code reads/writes Python ``dict``/``list`` regardless.
    PG-specific operators (``@>``, ``->>``, GIN indexes) remain available
    on the PG path; SQLite path stores the same data as text. Migrations
    that want PG-only optimisations should branch on
    ``op.get_bind().dialect.name``.
    """
    json_type: Any = JSON().with_variant(JSONB(), "postgresql")
    return json_type  # type: ignore[no-any-return]


__all__ = ["JsonField", "PkUuid"]
