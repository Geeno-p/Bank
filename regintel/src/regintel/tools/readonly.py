"""Read-only tools shared by the Copilot (8B) and the MCP server (Phase 9). They NEVER write."""
from __future__ import annotations

import inspect
import json
from collections import Counter
from typing import Any

from regintel.agents.extraction import active, load_extracted
from regintel.db import get_conn, init_db
from regintel.engines.evidence_tests import load_manifest
from regintel.retrieval.hybrid import tokens
from regintel.schemas.control import Control
from regintel.schemas.evidence import ControlTestResult
from regintel.schemas.gap import Gap
from regintel.schemas.mapping import ControlMapping, ObligationCoverage
from regintel.schemas.obligation import ApplicabilityDecision, Obligation
from regintel.settings import get_settings
from regintel.kg import graph_tools as gt

def _sql(q: str, *args) -> list[dict]:
    init_db()
    with get_conn() as c:
        return [dict(r) for r in c.execute(q, args).fetchall()]


def _obls() -> list[Obligation]:
    return active(load_extracted("obligations", Obligation))


def _cov() -> dict[str, ObligationCoverage]:
    return {c.obligation_id: c for c in load_extracted("coverage", ObligationCoverage)}


def _gap(g: Gap) -> dict:
    return {"gap_id": g.id, "band": g.risk.band.value if g.risk else None,
            "score": g.risk.score if g.risk else None, "type": g.gap_type.value,
            "control_id": g.control_id, "obligation_id": g.obligation_id, "title": g.title}


# ---------------- tools ----------------
def overview() -> dict:
    """Overall compliance posture: obligation coverage, gaps by band and type, runs awaiting review."""
    app = {d.obligation_id: d for d in load_extracted("applicability", ApplicabilityDecision)}
    obls, cov, gaps = _obls(), _cov(), load_extracted("gaps", Gap)
    applicable = [o for o in obls if app.get(o.id) and app[o.id].applicable]
    return {"obligations": len(obls), "applicable": len(applicable),
            "coverage": dict(Counter(cov[o.id].coverage.value if o.id in cov else "NOT_ASSESSED" for o in applicable)),
            "gaps_by_band": dict(Counter(g.risk.band.value for g in gaps if g.risk)),
            "gaps_by_type": dict(Counter(g.gap_type.value for g in gaps)),
            "runs_awaiting_review": len(_sql("SELECT run_id FROM runs WHERE status='PAUSED'"))}


def search_obligations(query: str, regulation_id: str = "", limit: int = 8) -> list[dict]:
    """Find regulatory obligations by keywords or clause reference (e.g. 'periodic updation high risk', '12(a)')."""
    q, cov = set(tokens(query)), _cov()
    scored = []
    for o in _obls():
        if regulation_id and o.regulation_id != regulation_id:
            continue
        s = len(q & set(tokens(f"{o.action} {o.source.quote} {' '.join(o.topics)}")))
        s += 5 * (query.strip() == o.source.clause_ref)
        if s:
            scored.append((s, o))
    scored.sort(key=lambda x: -x[0])
    return [{"obligation_id": o.id, "regulation_id": o.regulation_id, "clause_ref": o.source.clause_ref,
             "summary": o.summary(), "quote": o.source.quote[:200],
             "coverage": cov[o.id].coverage.value if o.id in cov else "NOT_ASSESSED"}
            for _, o in scored[: int(limit)]]


def get_gaps(band: str = "", gap_type: str = "", control_id: str = "", limit: int = 10) -> list[dict]:
    """List open gaps, highest risk first. Optional filters: band (CRITICAL/HIGH/MEDIUM/LOW),
    gap_type (UNMAPPED, PARTIAL_COVERAGE, DESIGN_GAP, OPERATING_FAILURE, MISSING_EVIDENCE, STALE_CONTROL), control_id."""
    gaps = sorted(load_extracted("gaps", Gap), key=lambda g: -(g.risk.score if g.risk else 0))
    sel = [g for g in gaps if (not band or (g.risk and g.risk.band.value == band.upper()))
           and (not gap_type or g.gap_type.value == gap_type.upper())
           and (not control_id or g.control_id == control_id.upper())]
    return [_gap(g) for g in sel[: int(limit)]]


def get_traceability(obligation_id: str) -> dict:
    """Full chain for one obligation: source quote -> applicability -> mapped controls -> tests -> gaps."""
    o = next((x for x in load_extracted("obligations", Obligation) if x.id == obligation_id), None)
    if not o:
        return {"error": f"obligation {obligation_id} not found; use search_obligations first"}
    app = next((d for d in load_extracted("applicability", ApplicabilityDecision) if d.obligation_id == o.id), None)
    maps = [m for m in load_extracted("mappings", ControlMapping) if m.obligation_id == o.id]
    cids = {m.control_id for m in maps}
    tests = [t for t in load_extracted("test_results", ControlTestResult) if t.control_id in cids]
    gaps = [g for g in load_extracted("gaps", Gap) if g.obligation_id == o.id or g.control_id in cids]
    return {"obligation": {"id": o.id, "regulation": o.regulation_id, "clause_ref": o.source.clause_ref,
                           "summary": o.summary(), "quote": o.source.quote, "valid_from": str(o.valid_from),
                           "valid_to": str(o.valid_to), "supersedes": o.supersedes_id},
            "applicability": app.model_dump(mode="json") if app else None,
            "mappings": [{"control_id": m.control_id, "coverage": m.coverage.value, "rationale": m.rationale,
                          "missing": m.missing_elements} for m in maps],
            "tests": [{"control_id": t.control_id, "procedure": t.procedure, "passed": t.passed,
                       "details": t.details} for t in tests],
            "gaps": [_gap(g) | {"description": g.description[:300]} for g in gaps]}


def get_control(control_id: str) -> dict:
    """One internal control: definition, policy, owner, evidence, latest test result and gaps."""
    cid = control_id.upper()
    c = next((x for x in load_extracted("controls", Control) if x.id == cid), None)
    if not c:
        return {"error": f"control {control_id} not found"}
    return {"control": {"id": c.id, "policy": c.policy_id, "name": c.name, "objective": c.objective,
                        "type": c.control_type.value, "nature": c.nature.value, "owner": c.owner_role,
                        "frequency": c.frequency.value if c.frequency else None,
                        "parameters": [p.model_dump() for p in c.parameters], "text": c.source.quote},
            "evidence": [e.title for e in load_manifest() if cid in e.control_ids],
            "tests": [{"procedure": t.procedure, "passed": t.passed, "details": t.details}
                      for t in load_extracted("test_results", ControlTestResult) if t.control_id == cid],
            "gaps": [_gap(g) for g in load_extracted("gaps", Gap) if g.control_id == cid]}


def explain_risk(gap_id: str) -> dict:
    """Why a gap has its risk score: factors (severity, inherent, weakness, urgency, blast radius) and formula."""
    g = next((x for x in load_extracted("gaps", Gap) if x.id == gap_id), None)
    if not g or not g.risk:
        return {"error": f"gap {gap_id} not found"}
    return _gap(g) | {"factors": g.risk.factors.model_dump(), "formula": g.risk.explanation,
                      "description": g.description, "evidence_refs": g.evidence_refs}


def get_changes(regulation_id: str = "") -> list[dict]:
    """Detected regulatory changes (clause-level), with change type and changed parameters."""
    d = get_settings().data_dir / "extracted" / "changes"
    out = []
    for p in sorted(d.glob("*.json")) if d.exists() else []:
        ev = json.loads(p.read_text(encoding="utf-8"))
        if regulation_id and ev["doc_id"] != regulation_id:
            continue
        out.append({"event_id": ev["id"], "regulation": ev["doc_id"], "from": ev["from_version"],
                    "to": ev["to_version"], "changes": [
                        {"type": c["change_type"], "summary": c["summary"], "parameters": c.get("changed_parameters", [])}
                        for c in ev["changes"]]})
    return out


def get_runs(status: str = "", limit: int = 10) -> list[dict]:
    """Recent agent workflow runs. Optional status filter: PAUSED, COMMITTED, REJECTED, FAILED."""
    if status:
        return _sql("SELECT run_id, event_type, status, started_at FROM runs WHERE status=? "
                    "ORDER BY started_at DESC LIMIT ?", status.upper(), int(limit))
    return _sql("SELECT run_id, event_type, status, started_at FROM runs ORDER BY started_at DESC LIMIT ?", int(limit))


def get_kris() -> list[dict]:
    """Latest value of each key risk indicator (KRI) versus its threshold."""
    return _sql("SELECT k.kri_id, k.value, k.threshold, k.status, k.ts FROM kri_snapshots k "
                "JOIN (SELECT kri_id, MAX(id) AS id FROM kri_snapshots GROUP BY kri_id) m ON k.id = m.id")


TOOLS: dict[str, Any] = {f.__name__: f for f in (
    overview, search_obligations, get_gaps, get_traceability, get_control, explain_risk, get_changes,
    get_runs, get_kris, gt.graph_schema, gt.impact_of_clause, gt.shared_controls,
    gt.coverage_if_control_removed, gt.obligation_history, gt.ask_graph, gt.run_cypher)}


def describe(tools: dict[str, Any] | None = None) -> str:
    """Tool list for prompts: name(signature): docstring."""
    tools = tools or TOOLS
    return "\n".join(f"- {n}{inspect.signature(f)}: {' '.join((f.__doc__ or '').split())}" for n, f in tools.items())