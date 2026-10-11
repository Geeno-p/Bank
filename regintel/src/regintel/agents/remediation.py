"""Phase 6C remediation agent: HIGH/CRITICAL gaps -> pattern-guided LLM draft -> deterministic checks
-> one revision -> proposed Remediation for human review.
  python -m regintel.agents.remediation [--bands HIGH,CRITICAL] [--gap GAP-ID] [--force] [--no-kg]"""
from __future__ import annotations

import json
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Optional

import typer
import yaml
from rich.console import Console
from rich.table import Table

from regintel.agents.extraction import load_extracted
from regintel.agents.mapping import periods
from regintel.config import get_model_routing
from regintel.db import audit, get_conn, init_db
from regintel.engines.evidence_tests import as_of_from, load_manifest
from regintel.llm.client import call_llm
from regintel.schemas.common import Provenance, RiskBand, stable_id, utcnow
from regintel.schemas.control import Control
from regintel.schemas.gap import ActionType, Gap, Remediation, RemediationDraft
from regintel.schemas.obligation import Obligation
from regintel.settings import get_settings

PROMPT_VERSION = "6c-v1"
ORDER = {RiskBand.LOW: 0, RiskBand.MEDIUM: 1, RiskBand.HIGH: 2, RiskBand.CRITICAL: 3}
TEXT_ACTIONS = {ActionType.POLICY_UPDATE, ActionType.NEW_CONTROL}

SYSTEM = """You are a bank compliance remediation lead. Propose a remediation plan for ONE gap.
Rules:
- Follow the PATTERN for this gap type: use only its allowed action types and include at least one required type.
- owner_role: choose ONLY from known_roles in CONTEXT.
- due_in_days: at most max_due_days for every action.
- POLICY_UPDATE and NEW_CONTROL actions must include draft_text: the exact policy/control wording to adopt,
  stating any regulatory period, deadline or threshold exactly as the regulation requires.
- Base everything on CONTEXT. Do not invent regulations, systems, figures or roles.
- effort: S (< 2 weeks), M (2-6 weeks), L (> 6 weeks).
- expected_residual_band: the risk band once the plan is complete.
- rationale: 2-3 sentences linking the actions to the regulatory requirement."""


@dataclass
class Ctx:
    obligations: dict[str, Obligation]
    controls: dict[str, Control]
    roles: list[str]
    as_of: date
    cfg: dict


def load_ctx() -> Ctx:
    s = get_settings()
    ctls = load_extracted("controls", Control)
    pols = json.loads((s.data_dir / "policies" / "index.json").read_text(encoding="utf-8"))
    roles = sorted({c.owner_role for c in ctls if c.owner_role} | {p["owner_role"] for p in pols})
    cfg = yaml.safe_load((s.config_dir / "remediation_patterns.yaml").read_text(encoding="utf-8"))
    return Ctx({o.id: o for o in load_extracted("obligations", Obligation)}, {c.id: c for c in ctls},
               roles, as_of_from(load_manifest()), cfg)


def max_due(g: Gap, o: Obligation | None, ctx: Ctx) -> int:
    """Before a future effective date if there is one; otherwise the SLA for the gap's band."""
    if o and o.valid_from and o.valid_from > ctx.as_of:
        return (o.valid_from - ctx.as_of).days
    return ctx.cfg["sla_days"][g.risk.band.value if g.risk else "LOW"]


# ---------- deterministic checks ----------
def check(d: RemediationDraft, g: Gap, o: Obligation | None, ctx: Ctx) -> list[str]:
    p, issues = ctx.cfg["patterns"][g.gap_type.value], []
    types = {a.action_type.value for a in d.actions}
    if not types & set(p["required"]):
        issues.append(f"must include one of {p['required']}")
    if bad := sorted(types - set(p["allowed"])):
        issues.append(f"action types {bad} are not suitable for {g.gap_type.value}")
    known, limit = {r.lower() for r in ctx.roles}, max_due(g, o, ctx)
    for i, a in enumerate(d.actions, 1):
        if a.owner_role.lower() not in known:
            issues.append(f"action {i}: owner '{a.owner_role}' is not a known role")
        if a.due_in_days > limit:
            issues.append(f"action {i}: due in {a.due_in_days} days exceeds the limit of {limit}")
        if a.action_type in TEXT_ACTIONS and not (a.draft_text or "").strip():
            issues.append(f"action {i}: {a.action_type.value} needs draft_text")
    reg = periods(o.parameters, o.source.quote) if o else set()
    texts = " ".join(a.draft_text or "" for a in d.actions if a.action_type in TEXT_ACTIONS)
    if reg and texts.strip() and not any(abs(x - r) <= 0.05 * r for x in periods([], texts) for r in reg):
        issues.append(f"draft_text must state the regulatory period ({'/'.join(f'{r:g} days' for r in sorted(reg))})")
    if g.risk and ORDER[d.expected_residual_band] > ORDER[g.risk.band]:
        issues.append("expected_residual_band cannot be higher than the current band")
    return issues


# ---------- drafting ----------
def _context(g: Gap, o: Obligation | None, c: Control | None, ctx: Ctx) -> str:
    limit = max_due(g, o, ctx)
    return json.dumps({
        "gap": {"type": g.gap_type.value, "title": g.title, "description": g.description,
                "band": g.risk.band.value if g.risk else None, "metrics": g.metrics},
        "obligation": {"regulation": o.regulation_id, "clause": o.source.clause_ref, "text": o.source.quote,
                       "parameters": [x.model_dump() for x in o.parameters],
                       "effective_from": str(o.valid_from)} if o else None,
        "control": {"id": c.id, "name": c.name, "text": c.source.quote, "owner": c.owner_role,
                    "systems": c.systems, "parameters": [x.model_dump() for x in c.parameters]} if c else None,
        "pattern": ctx.cfg["patterns"][g.gap_type.value], "known_roles": ctx.roles,
        "max_due_days": limit, "deadline": str(ctx.as_of + timedelta(days=limit)),
        "assessment_date": str(ctx.as_of)}, default=str, indent=1)


def draft_one(g: Gap, ctx: Ctx, run_id: str, llm: Callable = call_llm) -> Remediation:
    o, c = ctx.obligations.get(g.obligation_id), ctx.controls.get(g.control_id or "")
    user = f"CONTEXT:\n{_context(g, o, c, ctx)}\n\nPropose the remediation plan."
    d = llm("remediation_drafting", RemediationDraft, SYSTEM, user)
    issues = check(d, g, o, ctx)
    revised = bool(issues)
    if issues:   # one revision with the failed checks listed
        d = llm("remediation_drafting", RemediationDraft, SYSTEM,
                f"{user}\n\nYour previous plan failed these checks:\n- " + "\n- ".join(issues) +
                f"\nFix every issue.\nPREVIOUS PLAN:\n{d.model_dump_json()}")
        issues = check(d, g, o, ctx)
    return Remediation(
        id=stable_id("REM", g.id), gap_id=g.id, actions=d.actions, rationale=d.rationale,
        expected_residual_band=d.expected_residual_band, issues=issues, revised=revised,
        status="CHECKS_FAILED" if issues else "PROPOSED", valid_from=ctx.as_of,
        provenance=Provenance(created_by="agent:remediation", run_id=run_id, prompt_version=PROMPT_VERSION,
                              model=get_model_routing().for_task("remediation_drafting").model))


def propose(gaps: list[Gap], ctx: Ctx, run_id: str, llm: Callable = call_llm) -> list[Remediation]:
    out = []
    for g in gaps:
        try:
            out.append(draft_one(g, ctx, run_id, llm))
        except Exception as e:  # noqa: BLE001 (one failed gap must not stop the others)
            audit("agent:remediation", "ERROR", "gap", g.id, {"error": str(e)[:300]}, run_id)
    return out


# ---------- persistence ----------
def load_all() -> list[Remediation]:
    return load_extracted("remediations", Remediation)


def _write(rs: list[Remediation]) -> None:
    p = get_settings().data_dir / "extracted" / "remediations.jsonl"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("".join(r.model_dump_json() + "\n" for r in rs), encoding="utf-8")


def save(new: list[Remediation]) -> None:
    by = {r.gap_id: r for r in load_all()}
    by.update({r.gap_id: r for r in new})
    _write(list(by.values()))


def queue(rs: list[Remediation], run_id: str) -> None:
    with get_conn() as c:
        c.executemany("INSERT OR REPLACE INTO review_queue (id, item_type, item_id, reason, confidence, run_id, "
                      "created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                      [(stable_id("REV", "remediation", r.id), "remediation", r.id,
                        "CHECKS_FAILED" if r.issues else "REMEDIATION_PROPOSED", None, run_id,
                        utcnow().isoformat()) for r in rs])


def set_status(ids: list[str], status: str, reviewer: str | None = None, notes: str | None = None) -> None:
    if not ids:
        return
    want = set(ids)
    _write([r.model_copy(update={"status": status}) if r.id in want else r for r in load_all()])
    with get_conn() as c:
        c.executemany("UPDATE review_queue SET status=?, reviewer=?, decision_notes=? "
                      "WHERE item_type='remediation' AND item_id=?", [(status, reviewer, notes, i) for i in ids])


def brief(r: Remediation) -> dict:
    return {"remediation_id": r.id, "gap_id": r.gap_id, "status": r.status,
            "residual_band": r.expected_residual_band.value, "rationale": r.rationale, "issues": r.issues,
            "actions": [{"type": a.action_type.value, "owner": a.owner_role, "due_in_days": a.due_in_days,
                         "effort": a.effort, "description": a.description, "draft_text": a.draft_text}
                        for a in r.actions]}


def briefs(ids: list[str] | None) -> list[dict]:
    by = {r.id: r for r in load_all()}
    return [brief(by[i]) for i in ids or [] if i in by]


def _to_kg(rs: list[Remediation]) -> None:
    from regintel.kg.repository import KG, props
    with KG() as kg:
        kg.run("""UNWIND $rows AS r MERGE (m:Remediation {id: r.id}) SET m += r
                  WITH m, r MATCH (g:Gap {id: r.gap_id}) MERGE (g)-[:REMEDIATED_BY]->(m)""",
               rows=[props(r) for r in rs])
        kg.run("""UNWIND $rows AS r MATCH (m:Remediation {id: r.id}) UNWIND r.owners AS o
                  MERGE (ro:Role {id: o}) MERGE (m)-[:ASSIGNED_TO]->(ro)""",
               rows=[{"id": r.id, "owners": sorted({a.owner_role for a in r.actions})} for r in rs])


# ---------- entry points ----------
def select_gaps(bands: set[str], gap_id: str | None = None, force: bool = False) -> list[Gap]:
    gaps = load_extracted("gaps", Gap)
    if gap_id:
        return [g for g in gaps if g.id == gap_id]
    done = {r.gap_id for r in load_all() if r.status == "APPROVED"}
    return [g for g in gaps if g.risk and g.risk.band.value in bands and (force or g.id not in done)]


def run(bands: set[str] | None = None, gap_id: str | None = None, force: bool = False,
        kg: bool = True, llm: Callable = call_llm) -> list[Remediation]:
    init_db()
    run_id = f"RUN-{uuid.uuid4().hex[:10]}"
    gaps = select_gaps(bands or {"HIGH", "CRITICAL"}, gap_id, force)
    if not gaps:
        return []
    rs = propose(gaps, load_ctx(), run_id, llm)
    save(rs)
    queue(rs, run_id)
    if kg and rs:
        _to_kg(rs)
    audit("agent:remediation", "PROPOSE", "run", run_id,
          {"gaps": len(gaps), "plans": len(rs), "revised": sum(r.revised for r in rs),
           "checks_failed": sum(bool(r.issues) for r in rs)}, run_id)
    return rs


def main(bands: str = "HIGH,CRITICAL", gap: Optional[str] = None, force: bool = False, no_kg: bool = False) -> None:
    con = Console()
    rs = run({b.strip().upper() for b in bands.split(",")}, gap, force, kg=not no_kg)
    if not rs:
        con.print("[yellow]No gaps selected (or all already have an approved plan; use --force).[/]")
        return
    gaps = {g.id: g for g in load_extracted("gaps", Gap)}
    t = Table(title=f"Remediation plans ({len(rs)})")
    for col in ("Gap", "Band", "Status", "Actions", "Owners", "Max due", "Revised", "Issues"):
        t.add_column(col)
    for r in rs:
        g = gaps.get(r.gap_id)
        t.add_row(f"{g.control_id or '-'} {g.gap_type.value}" if g else r.gap_id,
                  g.risk.band.value if g and g.risk else "-", r.status,
                  ", ".join(a.action_type.value for a in r.actions),
                  ", ".join(sorted({a.owner_role for a in r.actions})),
                  f"{max((a.due_in_days for a in r.actions), default=0)}d",
                  "yes" if r.revised else "no", "; ".join(r.issues)[:60] or "-")
    con.print(t)


if __name__ == "__main__":
    typer.run(main)