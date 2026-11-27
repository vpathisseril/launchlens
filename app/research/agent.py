"""R0 — Research agent runtime: route → run read-only hub tools → synthesize with numbered citations → verify.

Provider-agnostic by design (ADR-007): tools run server-side, so every number the model sees comes with a citation,
and the verifier can check every number in the answer against tool output.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.orm import Session

from app.hubs import HUBS
from app.models import Agent, AgentRun, User
from app.research.contract import Citation, ToolResult
from app.services import ai_access

GUARDRAIL = """You are a research analyst inside LaunchLens, a research prototype for US drug-launch strategy.
Rules you must follow:
1. Use ONLY the facts in the TOOL RESULTS block. Never use outside knowledge for numbers, dates, prices or statuses.
2. Every sentence that contains a number or a factual claim ends with a citation marker like [1] or [2][3] that refers to
   the numbered sources listed in the TOOL RESULTS.
3. If the data cannot answer the question, say "This is not in the available data" and name the source that would be
   needed.
4. Describe policy neutrally: what it says, its status and its date. Never present a proposal as law. No advocacy.
5. No medical, legal or financial advice; no promotional claims about any product.
6. Medicare data is a proxy for the total market; say so when it matters.
7. Healthcare & life-sciences precision: use regulatory terms exactly (FDA-approved indication, line of therapy, label
   section, IPAY, MFP, PA/step therapy). Never infer efficacy, safety or comparative effectiveness beyond the cited
   source; distinguish labeled indications from trial endpoints; FAERS counts never imply causation.
8. Data is aggregate public data (no patient-level data). Never speculate about individual patients or prescribers'
   intent; describe prescriber data as Medicare Part D volumes only.
Write for an executive: lead with the answer, then 2–5 supporting bullets."""

TONE = {"executive": "Board-ready, crisp, decision-oriented.", "analytical": "Precise, structured, show the numbers.",
        "concise": "Maximum 120 words.", "coaching": "Explain the reasoning so a junior analyst can learn."}
FORMAT = {"markdown": "Use short markdown with bullets.", "bullets": "Use bullets only.",
          "table": "Include one compact markdown table.", "json": "Return valid JSON with keys answer, bullets."}

NUM_RE = re.compile(r"(?<![\w\[])\$?\d[\d,]*(?:\.\d+)?\s?(?:%|[KMB]\b)?")


@dataclass
class AgentAnswer:
    answer_md: str
    citations: list[dict[str, Any]]
    verified: bool
    notes: str
    tools_used: list[str] = field(default_factory=list)
    run_id: int | None = None
    model: str = ""
    key_type: str = ""
    notice: str = ""


def available_tools(hub_keys: list[str]) -> dict[str, tuple[str, str]]:
    """'hub.tool' -> (hub_key, description)."""
    out = {}
    for hk in hub_keys:
        hub = HUBS.get(hk)
        if hub:
            for name, desc in hub.info.tools.items():
                out[f"{hk}.{name}"] = (hk, desc)
    return out


KEYWORDS = {
    "access": ["access", "formulary", "prior auth", "step", "coverage", "payer", "plan", "tier", "restriction"],
    "pricing": ["price", "negotiat", "mfp", "ira", "mfn", "most favored", "discount", "candidate", "spend"],
    "policy": ["policy", "rule", "tariff", "232", "pbm", "regulation", "deadline", "comment", "federal", "law", "bill"],
    "targeting": ["target", "prescriber", "hcp", "decile", "white space", "state", "peak", "ramp", "segment"],
    "differentiation": ["competitor", "label", "pipeline", "trial", "differentiat", "market size", "evidence", "phase"],
}


def route(question: str, tools: dict[str, tuple[str, str]], primary_hub: str = "", limit: int = 4) -> list[str]:
    """Deterministic keyword router (no LLM cost). Always includes the primary hub's first tool."""
    q = question.lower()
    scored: list[tuple[int, str]] = []
    for full, (hk, desc) in tools.items():
        s = sum(2 for kw in KEYWORDS.get(hk, []) if kw in q)
        s += sum(1 for w in re.findall(r"[a-z]{5,}", desc.lower()) if w in q)
        if hk == primary_hub:
            s += 1
        scored.append((s, full))
    scored.sort(key=lambda x: -x[0])
    chosen = [f for s, f in scored if s > 0][:limit]
    if primary_hub:
        first = next((f for f in tools if f.startswith(primary_hub + ".")), None)
        if first and first not in chosen:
            chosen = [first, *chosen][:limit]
    return chosen or list(tools)[:2]


def run_tools(db: Session, scope: dict[str, Any], chosen: list[str]) -> tuple[list[ToolResult], list[Citation]]:
    results, cites = [], []
    for full in chosen:
        hk, name = full.split(".", 1)
        hub = HUBS[hk]
        tr = hub.tool(db, scope, name)
        results.append(tr)
        cites.extend(tr.citations)
    seen, uniq = set(), []
    for c in cites:
        k = (c.url, c.locator)
        if k not in seen:
            seen.add(k)
            uniq.append(c)
    return results, uniq[:20]


def build_context(results: list[ToolResult], cites: list[Citation]) -> str:
    parts = ["TOOL RESULTS"]
    for r in results:
        parts.append(f"## {r.name}\n{r.digest[:3500]}")
    parts.append("SOURCES")
    for i, c in enumerate(cites, 1):
        parts.append(f"[{i}] {c.dataset} · vintage {c.vintage} · {c.locator} · retrieved {c.retrieved_at}")
    return "\n\n".join(parts)


def _norm_num(s: str) -> str:
    s = s.replace("$", "").replace(",", "").replace(" ", "").rstrip("%KMB")
    try:
        f = float(s)
        return f"{f:.2f}".rstrip("0").rstrip(".")
    except ValueError:
        return s


def verify(answer: str, context: str, question: str, n_sources: int) -> tuple[bool, str]:
    """Checks (1) every [n] maps to a source; (2) every number in the answer appears in tool output or the question."""
    notes = []
    refs = [int(x) for x in re.findall(r"\[(\d{1,2})\]", answer)]
    bad = sorted({r for r in refs if r < 1 or r > n_sources})
    if bad:
        notes.append(f"Citation markers without a source: {bad}")
    has_numbers = bool(NUM_RE.search(re.sub(r"\[\d{1,2}\]", "", answer)))
    if has_numbers and not refs:
        notes.append("Numbers present but no citations.")
    hay = {_norm_num(m) for m in NUM_RE.findall(context + " " + question)}
    unsupported = []
    for m in NUM_RE.findall(re.sub(r"\[\d{1,2}\]", "", answer)):
        n = _norm_num(m)
        if not n or len(n) <= 1:
            continue
        if n not in hay and n not in {"10", "100", "50", "2", "3", "4", "5"}:
            unsupported.append(m.strip())
    if unsupported:
        notes.append("Numbers not found in source data: " + ", ".join(sorted(set(unsupported))[:8]))
    return (not notes), "; ".join(notes)


def ask(db: Session, user: User, question: str, scope: dict[str, Any], hub_keys: list[str], primary_hub: str = "",
        agent: Agent | None = None) -> AgentAnswer:
    if agent and agent.tools:
        allowed = {t for t in agent.tools if t.split(".")[0] in hub_keys}
        tools = {k: v for k, v in available_tools(hub_keys).items() if k in allowed}
    else:
        tools = available_tools(hub_keys)
    if not tools:
        return AgentAnswer("You don't have access to any research hubs yet.", [], True, "")
    chosen = route(question, tools, primary_hub or (agent.hub_key if agent else ""))
    results, cites = run_tools(db, scope, chosen)
    context = build_context(results, cites)
    system = GUARDRAIL
    if agent:
        system += f"\n\nPersona: {agent.persona}\nGoal: {agent.goal}\nTone: {TONE.get(agent.tone, '')}\n" \
                  f"Format: {FORMAT.get(agent.output_format, '')}"
    scope_line = f"Scope: {json.dumps({k: v for k, v in scope.items() if v})}"
    user_msg = f"{scope_line}\n\nQUESTION: {question}\n\n{context}"
    # The agent's provider applies only when it pins a model; otherwise follow the user's current preference.
    text, ev, access = ai_access.llm_call(
        db, user, system, user_msg, task="answer", hub_key=primary_hub, agent_id=agent.id if agent else None,
        model=agent.model if agent and agent.model else None, provider=agent.provider if agent and agent.model else None,
        max_tokens=agent.max_tokens if agent else 900, temperature=agent.temperature if agent else 0.2)
    ok, notes = verify(text, context, question, len(cites))
    if not ok:  # one corrective retry
        retry_msg = user_msg + f"\n\nYour previous draft failed verification ({notes}). Rewrite using only numbers " \
                               "that appear in TOOL RESULTS, each with a valid [n] citation."
        text2, ev, access = ai_access.llm_call(db, user, system, retry_msg, task="answer", hub_key=primary_hub,
                                               model=access.model, provider=access.provider,
                                               max_tokens=access.max_tokens, use_cache=False)
        ok2, notes2 = verify(text2, context, question, len(cites))
        if ok2 or len(notes2) < len(notes):
            text, ok, notes = text2, ok2, notes2
    ev.verified = ok
    run = AgentRun(agent_id=agent.id if agent else None, user_id=user.id, hub_key=primary_hub, question=question,
                   answer_md=text, citations=[c.as_dict() for c in cites], verified=ok, verifier_notes=notes,
                   usage_event_id=ev.id)
    db.add(run)
    db.commit()
    return AgentAnswer(text, [c.as_dict() for c in cites], ok, notes, chosen, run.id, access.model, access.key_type,
                       access.notice)
