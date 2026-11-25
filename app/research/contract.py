"""R0 — the shared research contract every hub implements (Kpi, ViewResult, ToolResult, Citation)."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any


@dataclass
class Citation:
    source_key: str
    dataset: str
    vintage: str
    url: str
    retrieved_at: str
    locator: str = ""  # row filter / document section

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)

    @staticmethod
    def now_iso(dt: datetime | None = None) -> str:
        return (dt or datetime.utcnow()).strftime("%Y-%m-%d %H:%M UTC")  # noqa: DTZ003


@dataclass
class Kpi:
    label: str
    display: str
    value: float | None = None
    delta: str = ""
    delta_dir: str = ""  # up | down | flat
    spark: list[float] = field(default_factory=list)
    citation: Citation | None = None
    hint: str = ""


@dataclass
class ViewResult:
    id: str
    title: str
    insight: str = ""
    chart: dict[str, Any] | None = None  # ECharts option
    table: dict[str, Any] | None = None  # {"columns": [...], "rows": [[...]]}
    citations: list[Citation] = field(default_factory=list)
    kpis: list[Kpi] = field(default_factory=list)
    banner: str = ""  # caveats: suppression, truncation, missing source
    empty: str = ""  # empty-state guidance


@dataclass
class ToolResult:
    """What a hub tool returns to the research agent: a compact text digest + the citations backing it."""
    name: str
    digest: str
    citations: list[Citation] = field(default_factory=list)
    data: Any = None


@dataclass
class HubInfo:
    key: str
    title: str
    short: str
    icon: str
    rank: int
    question: str
    views: list[tuple[str, str]]  # (id, title)
    tools: dict[str, str]  # tool name -> description


def empty_scope() -> dict[str, Any]:
    return {"therapeutic_area": "", "indication": "", "drugs": [], "states": [], "places_measure": "", "preset": ""}


def norm_scope(scope: dict[str, Any] | None) -> dict[str, Any]:
    s = empty_scope()
    s.update({k: v for k, v in (scope or {}).items() if k in s})
    s["drugs"] = [d.strip() for d in s.get("drugs") or [] if d and d.strip()][:8]
    s["states"] = [x.strip().upper() for x in s.get("states") or [] if x and x.strip()][:10]
    return s
