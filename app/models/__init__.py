"""SQLAlchemy models for all modules. Every curated/research record carries lineage (source_key, vintage, retrieved_at)."""
from __future__ import annotations

import enum
from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import JSON, Boolean, Date, DateTime, Enum, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base


def utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


class Role(str, enum.Enum):
    user = "user"
    admin = "admin"


class UserStatus(str, enum.Enum):
    pending = "pending"
    approved = "approved"
    rejected = "rejected"
    suspended = "suspended"


class AiKeyStatus(str, enum.Enum):
    none = "none"
    byok_valid = "byok_valid"
    default_accepted = "default_accepted"


# ---------------------------------------------------------------- M0 core
class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(120))
    org: Mapped[str] = mapped_column(String(160), default="")
    title: Mapped[str] = mapped_column(String(160), default="")
    use_case: Mapped[str] = mapped_column(String(80), default="")
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[Role] = mapped_column(Enum(Role), default=Role.user)
    status: Mapped[UserStatus] = mapped_column(Enum(UserStatus), default=UserStatus.pending, index=True)
    requested_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    approved_by: Mapped[int | None] = mapped_column(Integer, nullable=True)
    approval_source: Mapped[str | None] = mapped_column(String(10), nullable=True)  # admin | auto
    ai_key_status: Mapped[AiKeyStatus] = mapped_column(Enum(AiKeyStatus), default=AiKeyStatus.none)
    preferred_provider: Mapped[str] = mapped_column(String(20), default="anthropic")
    preferred_models: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    theme: Mapped[str] = mapped_column(String(30), default="clinical-clean")
    scope: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    must_change_password: Mapped[bool] = mapped_column(Boolean, default=False)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    modules: Mapped[list[UserModule]] = relationship(back_populates="user", cascade="all, delete-orphan")


class Module(Base):
    __tablename__ = "modules"
    id: Mapped[int] = mapped_column(primary_key=True)
    key: Mapped[str] = mapped_column(String(40), unique=True)
    name: Mapped[str] = mapped_column(String(80))
    description: Mapped[str] = mapped_column(String(255), default="")
    icon: Mapped[str] = mapped_column(String(40), default="box")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    is_default_on_approval: Mapped[bool] = mapped_column(Boolean, default=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)


class UserModule(Base):
    __tablename__ = "user_modules"
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    module_id: Mapped[int] = mapped_column(ForeignKey("modules.id", ondelete="CASCADE"), primary_key=True)
    granted_by: Mapped[int | None] = mapped_column(Integer, nullable=True)
    granted_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    user: Mapped[User] = relationship(back_populates="modules")
    module: Mapped[Module] = relationship()


class Setting(Base):
    __tablename__ = "settings"
    key: Mapped[str] = mapped_column(String(80), primary_key=True)
    value: Mapped[Any] = mapped_column(JSON)


class AuditLog(Base):
    __tablename__ = "audit_log"
    id: Mapped[int] = mapped_column(primary_key=True)
    actor_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    action: Mapped[str] = mapped_column(String(80), index=True)
    entity: Mapped[str] = mapped_column(String(60))
    entity_id: Mapped[str] = mapped_column(String(60), default="")
    before: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    after: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    ip: Mapped[str] = mapped_column(String(64), default="")
    at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)


class PasswordReset(Base):
    __tablename__ = "password_resets"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    token_hash: Mapped[str] = mapped_column(String(128), unique=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime)
    used: Mapped[bool] = mapped_column(Boolean, default=False)


# ---------------------------------------------------------------- M1 AI keys & usage
class UserApiKey(Base):
    __tablename__ = "user_api_keys"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    provider: Mapped[str] = mapped_column(String(20))
    key_ciphertext: Mapped[str] = mapped_column(Text)
    key_last4: Mapped[str] = mapped_column(String(8))
    label: Mapped[str] = mapped_column(String(80), default="")
    is_valid: Mapped[bool] = mapped_column(Boolean, default=False)
    validated_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    __table_args__ = (UniqueConstraint("user_id", "provider"),)


class DefaultKeyPolicy(Base):
    __tablename__ = "default_key_policy"
    id: Mapped[int] = mapped_column(primary_key=True)
    provider: Mapped[str] = mapped_column(String(20), unique=True)
    model_allowlist: Mapped[list[str]] = mapped_column(JSON, default=list)
    max_tokens_per_call: Mapped[int] = mapped_column(Integer, default=800)
    calls_per_day: Mapped[int] = mapped_column(Integer, default=20)
    monthly_budget_usd: Mapped[float] = mapped_column(Float, default=25.0)
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=True)


class ModelPrice(Base):
    __tablename__ = "model_prices"
    id: Mapped[int] = mapped_column(primary_key=True)
    provider: Mapped[str] = mapped_column(String(20))
    model: Mapped[str] = mapped_column(String(80))
    usd_per_mtok_in: Mapped[float] = mapped_column(Float, default=0.0)
    usd_per_mtok_out: Mapped[float] = mapped_column(Float, default=0.0)
    tier: Mapped[str] = mapped_column(String(20), default="standard")  # economy | standard | premium
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    __table_args__ = (UniqueConstraint("provider", "model"),)


class UsageEvent(Base):
    __tablename__ = "usage_events"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    agent_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    hub_key: Mapped[str] = mapped_column(String(30), default="")
    provider: Mapped[str] = mapped_column(String(20))
    model: Mapped[str] = mapped_column(String(80))
    key_type: Mapped[str] = mapped_column(String(10))  # byok | default
    tokens_in: Mapped[int] = mapped_column(Integer, default=0)
    tokens_out: Mapped[int] = mapped_column(Integer, default=0)
    est_cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(20), default="ok")
    verified: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)


class LlmCache(Base):
    __tablename__ = "llm_cache"
    id: Mapped[int] = mapped_column(primary_key=True)
    request_hash: Mapped[str] = mapped_column(String(64), unique=True)
    response: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


# ---------------------------------------------------------------- M2 agents
class Agent(Base):
    __tablename__ = "agents"
    id: Mapped[int] = mapped_column(primary_key=True)
    owner_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=True)
    name: Mapped[str] = mapped_column(String(80))
    icon: Mapped[str] = mapped_column(String(40), default="bot")
    hub_key: Mapped[str] = mapped_column(String(30), default="")  # primary hub, "" = cross-hub
    persona: Mapped[str] = mapped_column(Text, default="")
    goal: Mapped[str] = mapped_column(Text, default="")
    provider: Mapped[str] = mapped_column(String(20), default="anthropic")
    model: Mapped[str] = mapped_column(String(80), default="")
    temperature: Mapped[float] = mapped_column(Float, default=0.2)
    max_tokens: Mapped[int] = mapped_column(Integer, default=800)
    tone: Mapped[str] = mapped_column(String(20), default="executive")
    output_format: Mapped[str] = mapped_column(String(20), default="markdown")
    tools: Mapped[list[str]] = mapped_column(JSON, default=list)
    is_template: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class AgentRun(Base):
    __tablename__ = "agent_runs"
    id: Mapped[int] = mapped_column(primary_key=True)
    agent_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    hub_key: Mapped[str] = mapped_column(String(30), default="")
    question: Mapped[str] = mapped_column(Text)
    answer_md: Mapped[str] = mapped_column(Text)
    citations: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    verified: Mapped[bool] = mapped_column(Boolean, default=False)
    verifier_notes: Mapped[str] = mapped_column(Text, default="")
    usage_event_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    rating: Mapped[int | None] = mapped_column(Integer, nullable=True)
    at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


# ---------------------------------------------------------------- R0 research workspace
class ResearchProject(Base):
    __tablename__ = "research_projects"
    id: Mapped[int] = mapped_column(primary_key=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(String(160))
    scope: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(12), default="active")
    notes_md: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)
    findings: Mapped[list[Finding]] = relationship(back_populates="project", cascade="all, delete-orphan",
                                                   order_by="Finding.sort_order")


class Finding(Base):
    __tablename__ = "findings"
    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("research_projects.id", ondelete="CASCADE"), index=True)
    hub_key: Mapped[str] = mapped_column(String(30))
    title: Mapped[str] = mapped_column(String(200))
    body_md: Mapped[str] = mapped_column(Text, default="")
    chart_spec: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    citations: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    pinned_by: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    project: Mapped[ResearchProject] = relationship(back_populates="findings")


class Watchlist(Base):
    __tablename__ = "watchlists"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(20))  # drug | policy_topic | competitor
    ref: Mapped[str] = mapped_column(String(120))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    __table_args__ = (UniqueConstraint("user_id", "kind", "ref"),)


class Notification(Base):
    __tablename__ = "notifications"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(String(200))
    body: Mapped[str] = mapped_column(Text, default="")
    url: Mapped[str] = mapped_column(String(500), default="")
    read_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Brief(Base):
    __tablename__ = "briefs"
    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("research_projects.id", ondelete="CASCADE"), index=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    content_md: Mapped[str] = mapped_column(Text)
    generated_by_model: Mapped[str] = mapped_column(String(80), default="")
    usage_event_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


# ---------------------------------------------------------------- M5 data pipeline
class DataSource(Base):
    __tablename__ = "data_sources"
    id: Mapped[int] = mapped_column(primary_key=True)
    key: Mapped[str] = mapped_column(String(40), unique=True)
    name: Mapped[str] = mapped_column(String(160))
    publisher: Mapped[str] = mapped_column(String(80))
    hub_key: Mapped[str] = mapped_column(String(30))
    url: Mapped[str] = mapped_column(String(500))
    license_note: Mapped[str] = mapped_column(String(255), default="Public domain (U.S. Government work)")
    refresh_cadence: Mapped[str] = mapped_column(String(20), default="on-demand")
    last_refreshed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    latest_vintage: Mapped[str] = mapped_column(String(40), default="")
    status: Mapped[str] = mapped_column(String(12), default="unknown")  # ok | stale | failed | unknown
    last_error: Mapped[str] = mapped_column(Text, default="")


class SourceCache(Base):
    __tablename__ = "source_cache"
    id: Mapped[int] = mapped_column(primary_key=True)
    source_key: Mapped[str] = mapped_column(String(40), index=True)
    request_hash: Mapped[str] = mapped_column(String(64), unique=True)
    url: Mapped[str] = mapped_column(String(1000))
    payload: Mapped[Any] = mapped_column(JSON)
    retrieved_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime)


class IngestRun(Base):
    __tablename__ = "ingest_runs"
    id: Mapped[int] = mapped_column(primary_key=True)
    source_key: Mapped[str] = mapped_column(String(40), index=True)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    rows_in: Mapped[int] = mapped_column(Integer, default=0)
    rows_out: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(12), default="running")
    error: Mapped[str] = mapped_column(Text, default="")


class NegotiationDrug(Base):
    """R2 — CMS Medicare Drug Price Negotiation Program selected drugs (official CMS publications)."""
    __tablename__ = "cur_negotiation_drugs"
    id: Mapped[int] = mapped_column(primary_key=True)
    ipay: Mapped[int] = mapped_column(Integer, index=True)
    drug: Mapped[str] = mapped_column(String(200))
    generic: Mapped[str] = mapped_column(String(200), default="")
    sponsor: Mapped[str] = mapped_column(String(160), default="")
    part: Mapped[str] = mapped_column(String(4), default="D")  # D | B
    conditions: Mapped[str] = mapped_column(String(300), default="")
    selection_type: Mapped[str] = mapped_column(String(20), default="negotiation")  # negotiation | renegotiation
    medicare_spend_usd: Mapped[float | None] = mapped_column(Float, nullable=True)
    beneficiaries: Mapped[int | None] = mapped_column(Integer, nullable=True)
    list_price_30d: Mapped[float | None] = mapped_column(Float, nullable=True)
    mfp_30d: Mapped[float | None] = mapped_column(Float, nullable=True)
    discount_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    status: Mapped[str] = mapped_column(String(40), default="Selected")
    source_key: Mapped[str] = mapped_column(String(40), default="negotiation_program")
    source_url: Mapped[str] = mapped_column(String(500))
    vintage: Mapped[str] = mapped_column(String(40))
    retrieved_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class PolicyItem(Base):
    """R3 — federal policy documents and actions (Federal Register, Regulations.gov, admin-entered official items)."""
    __tablename__ = "policy_items"
    id: Mapped[int] = mapped_column(primary_key=True)
    external_id: Mapped[str] = mapped_column(String(80), unique=True)
    title: Mapped[str] = mapped_column(String(500))
    doc_type: Mapped[str] = mapped_column(String(40))  # rule | proposed_rule | executive_action | notice | agreement | report
    topic: Mapped[str] = mapped_column(String(20), index=True)  # mfn | tariff | pbm | ira | other
    status: Mapped[str] = mapped_column(String(20))  # Proposed | Final | In effect | Notice | Announced
    status_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    effective_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    comments_close: Mapped[date | None] = mapped_column(Date, nullable=True)
    agency: Mapped[str] = mapped_column(String(300), default="")
    abstract: Mapped[str] = mapped_column(Text, default="")
    summary_md: Mapped[str] = mapped_column(Text, default="")
    affected_channels: Mapped[list[str]] = mapped_column(JSON, default=list)
    source_key: Mapped[str] = mapped_column(String(40))
    source_url: Mapped[str] = mapped_column(String(500))
    first_seen_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    last_changed_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    retrieved_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class FormularyRestriction(Base):
    """R1 — CMS Part D formulary file rows, filtered to tracked RxCUIs."""
    __tablename__ = "cur_formulary_restrictions"
    id: Mapped[int] = mapped_column(primary_key=True)
    contract_id: Mapped[str] = mapped_column(String(10), index=True)
    plan_id: Mapped[str] = mapped_column(String(6))
    state: Mapped[str] = mapped_column(String(4), index=True, default="")
    plan_type: Mapped[str] = mapped_column(String(10), default="")  # PDP | MA-PD
    formulary_id: Mapped[str] = mapped_column(String(12))
    drug: Mapped[str] = mapped_column(String(120), index=True)  # generic name of tracked drug
    rxcui: Mapped[str] = mapped_column(String(12))
    tier: Mapped[int | None] = mapped_column(Integer, nullable=True)
    prior_auth: Mapped[bool] = mapped_column(Boolean, default=False)
    step_therapy: Mapped[bool] = mapped_column(Boolean, default=False)
    quantity_limit: Mapped[bool] = mapped_column(Boolean, default=False)
    enrollment: Mapped[int | None] = mapped_column(Integer, nullable=True)
    source_key: Mapped[str] = mapped_column(String(40), default="partd_formulary")
    vintage: Mapped[str] = mapped_column(String(40))
    retrieved_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class FormularyPlan(Base):
    """R1 — Part D plans by state from the CMS plan information file (denominator for coverage rates)."""
    __tablename__ = "cur_formulary_plans"
    id: Mapped[int] = mapped_column(primary_key=True)
    contract_id: Mapped[str] = mapped_column(String(10), index=True)
    plan_id: Mapped[str] = mapped_column(String(6))
    state: Mapped[str] = mapped_column(String(4), index=True)
    plan_type: Mapped[str] = mapped_column(String(10))
    formulary_id: Mapped[str] = mapped_column(String(12), index=True)
    vintage: Mapped[str] = mapped_column(String(40))
    retrieved_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    __table_args__ = (UniqueConstraint("contract_id", "plan_id", "state", "vintage"),)
