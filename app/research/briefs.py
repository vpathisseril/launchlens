"""R0 — Executive Brief generation from pinned findings (cross-hub), with sources appendix and signature."""
from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.content.about import DISCLAIMER, SIGNATURE  # single source of truth
from app.hubs import HUBS
from app.models import Brief, ResearchProject, User
from app.research.agent import GUARDRAIL, verify
from app.services import ai_access

SECTIONS = ["Situation", "Market access", "Pricing policy", "Policy watch", "Where to win (targeting)",
            "Competitive differentiation", "Risks", "Next 90 days", "Data notes"]


def findings_context(project: ResearchProject) -> tuple[str, list[dict]]:
    cites: list[dict] = []
    blocks = []
    for f in project.findings:
        idxs = []
        for c in f.citations or []:
            cites.append(c)
            idxs.append(len(cites))
        hub = HUBS.get(f.hub_key)
        blocks.append(f"### [{hub.info.short if hub else f.hub_key}] {f.title}\n{f.body_md}\nSources: "
                      + "".join(f"[{i}]" for i in idxs))
    src = "\n".join(f"[{i}] {c.get('dataset')} · {c.get('vintage')} · {c.get('locator')} · {c.get('url')}"
                    for i, c in enumerate(cites, 1))
    return "PINNED FINDINGS\n\n" + "\n\n".join(blocks) + "\n\nSOURCES\n" + src, cites


def sources_appendix(cites: list[dict]) -> str:
    lines = ["## Sources"]
    for i, c in enumerate(cites, 1):
        lines.append(f"{i}. {c.get('dataset')} — vintage {c.get('vintage')}; {c.get('locator')}; retrieved "
                     f"{c.get('retrieved_at')}. <{c.get('url')}>")
    return "\n".join(lines)


def generate(db: Session, user: User, project: ResearchProject) -> Brief:
    ctx, cites = findings_context(project)
    system = GUARDRAIL + "\nYou write one-page executive launch briefs (≤ 600 words)."
    msg = (f"Project: {project.title}\nScope: {project.scope}\n\nWrite an executive brief with these sections as "
           f"## headings: {', '.join(SECTIONS)}. Skip a section only if no finding supports it (write 'No pinned "
           f"evidence yet.'). Cite with [n] using the SOURCES numbering.\n\n{ctx}")
    text, ev, access = ai_access.llm_call(db, user, system, msg, task="brief", hub_key="brief", max_tokens=1600)
    ok, notes = verify(text, ctx, "", len(cites))
    ev.verified = ok
    version = (db.scalar(select(func.max(Brief.version)).where(Brief.project_id == project.id)) or 0) + 1
    body = (f"# {project.title} — Executive Launch Brief\n\n*{DISCLAIMER}*\n\n{text.strip()}\n\n"
            + ("" if ok else f"> ⚠ Verification warning: {notes}\n\n") + sources_appendix(cites)
            + f"\n\n---\n{SIGNATURE}")
    b = Brief(project_id=project.id, version=version, content_md=body, generated_by_model=access.model,
              usage_event_id=ev.id)
    db.add(b)
    db.commit()
    return b


def manual_brief(project: ResearchProject) -> str:
    """Brief without AI: findings grouped by hub. Always available (also used when the user has no AI access)."""
    _, cites = findings_context(project)
    out = [f"# {project.title} — Research Summary", f"*{DISCLAIMER}*"]
    n = 0
    for f in project.findings:
        hub = HUBS.get(f.hub_key)
        marks = "".join(f"[{n + i + 1}]" for i in range(len(f.citations or [])))
        n += len(f.citations or [])
        out.append(f"## {hub.info.short if hub else f.hub_key}: {f.title}\n{f.body_md} {marks}")
    out.append(sources_appendix(cites))
    out.append(f"---\n{SIGNATURE}")
    return "\n\n".join(out)
