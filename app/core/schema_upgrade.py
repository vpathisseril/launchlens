"""Idempotent, additive schema upgrades for databases created by an earlier release (ADR-011).

`create_all` creates missing tables but never alters existing ones. Each statement here only widens a column,
so it is safe to run on every start. Statements are fixed strings (no interpolation).
"""
from __future__ import annotations

import logging

from sqlalchemy import text
from sqlalchemy.engine import Engine

log = logging.getLogger("launchlens.schema")

POSTGRES_UPGRADES: tuple[str, ...] = (
    # v1.0.1 — negotiation vintage/status labels exceed 40 chars
    "ALTER TABLE cur_negotiation_drugs ALTER COLUMN vintage TYPE VARCHAR(120)",
    "ALTER TABLE cur_negotiation_drugs ALTER COLUMN status TYPE VARCHAR(120)",
)


def upgrade(engine: Engine) -> None:
    if engine.dialect.name != "postgresql":
        return  # SQLite does not enforce VARCHAR length; nothing to do
    with engine.begin() as conn:
        for stmt in POSTGRES_UPGRADES:
            conn.execute(text(stmt))
    log.info("schema upgrades applied: %d", len(POSTGRES_UPGRADES))
