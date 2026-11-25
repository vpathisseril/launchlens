"""Regression: seed data must fit its columns (v1.0.0 failed on PostgreSQL), and upgrades are idempotent."""
from sqlalchemy import String

from app.core import db as dbmod
from app.core.schema_upgrade import upgrade
from app.data import seed as S
from app.models import NegotiationDrug


def test_seed_strings_fit_columns():
    cols = {c.name: c.type.length for c in NegotiationDrug.__table__.columns if isinstance(c.type, String)}
    vintages = [r[-1][1] for r in S.NEGOTIATION] + [r[-1][1] for r in S.IPAY_2028]
    statuses = [r[12] for r in S.NEGOTIATION] + ["Negotiating in 2026; prices effective Jan 1, 2028",
                                                 "Selected for renegotiation; effective Jan 1, 2028"]
    assert max(map(len, vintages)) <= cols["vintage"]
    assert max(map(len, statuses)) <= cols["status"]


def test_schema_upgrade_is_safe_to_rerun(client):
    upgrade(dbmod.get_engine())
    upgrade(dbmod.get_engine())
    assert client.get("/healthz").json() == {"status": "ok"}
