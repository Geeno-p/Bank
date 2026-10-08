"""Phase 5B LangGraph orchestration: event -> router -> agents -> critic -> HITL -> commit."""
from __future__ import annotations

import json
import operator
import sqlite3
import time
from datetime import date
from functools import wraps
from typing import Annotated, Any, TypedDict

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from regintel.agents import applicability as app_ag
from regintel.agents import change as chg
from regintel.agents import gaps as gap_ag
from regintel.agents import mapping as map_ag
from regintel.agents.extraction import active, load_extracted
from regintel.config import get_sources
from regintel.db import audit, get_conn
from regintel.engines.evidence_tests import as_of_from, load_manifest, run_tests
from regintel.ingestion.run import ingest
from regintel.schemas.change import ChangeEvent
from regintel.schemas.common import DocType, RiskBand, stable_id, utcnow
from regintel.schemas.control import Control
from regintel.schemas.evidence import ControlTestResult
from regintel.schemas.gap import Gap
from regintel.schemas.mapping import ControlMapping, ObligationCoverage
from regintel.schemas.obligation import ApplicabilityDecision, Obligation
from regintel.settings import get_settings

REVIEW_BANDS = {RiskBand.CRITICAL, RiskBand.HIGH}
ROUTES = {"NEW_VERSION": "ingest", "EVIDENCE_UPLOADED": "assess", "SCHEDULED_TEST": "assess"}


class FlowState(TypedDict, total=False):
    run_id: str
    event_type: str
    payload: dict[str, Any]
    auto_approve: bool
    no_kg: bool
    doc_id: str
    from_version: str
    to_version: str
    change_event_id: str
    material: int
    new_obligation_ids: list[str]
    retired_ids: list[str]
    impacted_gap_ids: list[str]
    review_ids: list[str]
    issues: list[str]
    decision: dict[str, Any]
    approved: bool
    status: str
    trace: Annotated[list[dict[str, Any]], operator.add]


def step(name: str):
    """Times each node, appends a trace entry and writes an audit record."""
    def deco(fn):
        @wraps(fn)
        def wrapper(state: FlowState) -> dict:
            t0 = time.perf_counter()
            out = dict(fn(state) or {})
            info = out.pop("_info", "")
            ms = round((time.perf_counter() - t0) * 1000)
            audit(f"agent:{name}", "STEP", "run", state["run_id"], {"info": info, "ms": ms}, state["run_id"])
            return {**out, "trace": [{"node": name, "ms": ms, "info": info}]}
        return wrapper
    return deco


def _queue(gap_ids: list[str], run_id: str) -> None:
    with get_conn() as c:
        c.executemany("INSERT OR IGNORE INTO review_queue (id, item_type, item_id, reason, confidence, "
                      "run_id, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                      [(stable_id("REV", "gap", g), "gap", g, "HIGH_RISK_IMPACT", None, run_id,
                        utcnow().isoformat()) for g in gap_ids])


# ---------- nodes ----------
@step("router")
def router(s: FlowState) -> dict:
    with get_conn() as c:
        c.execute("INSERT OR REPLACE INTO runs (run_id, event_type, status, started_at) VALUES (?, ?, ?, ?)",
                  (s["run_id"], s["event_type"], "RUNNING", utcnow().isoformat()))
    return {"_info": f"{s['event_type']} -> {ROUTES.get(s['event_type'], 'commit')}"}


@step("ingest")
def ingest_node(s: FlowState) -> dict:
    doc, label = s["payload"]["doc_id"], s["payload"]["version"]
    reg = next(r for r in get_sources().regulations if r.id == doc)
    vs = sorted(reg.versions, key=lambda v: v.effective_date or date.min)
    i = next(k for k, v in enumerate(vs) if v.label == label)
    if i == 0:
        raise ValueError(f"{doc}/{label} is the first version: nothing to compare against")
    _, clauses, changed = ingest(get_settings().root / vs[i].path, doc, label, DocType.REGULATION,
                                 vs[i].effective_date, "fast")
    return {"doc_id": doc, "from_version": vs[i - 1].label, "to_version": label,
            "_info": f"{doc}/{label}: {len(clauses)} clauses ({'ingested' if changed else 'unchanged'})"}


@step("change")
def change_node(s: FlowState) -> dict:
    doc, frm, to = s["doc_id"], s["from_version"], s["to_version"]
    _, old_cl, _ = chg.load_version(doc, frm)
    new_ver, new_cl, new_full = chg.load_version(doc, to)
    changes, unchanged = chg.diff_clauses(old_cl, new_cl, doc)
    event = ChangeEvent(id=stable_id("CHG", doc, frm, to), doc_id=doc, from_version=frm, to_version=to,
                        changes=changes)
    retired, new_obls = chg.apply_change(event, new_ver, new_cl, new_full, s["run_id"])
    out = get_settings().data_dir / "extracted" / "changes"
    out.mkdir(parents=True, exist_ok=True)
    (out / f"{event.id}.json").write_text(
        event.model_dump_json(indent=2, exclude={"changes": {"__all__": {"is_material"}}}), encoding="utf-8")
    if not s.get("no_kg"):
        chg._to_kg(event, new_ver, new_cl, retired, new_obls)
    kinds = sorted({c.change_type.value for c in changes})
    return {"change_event_id": event.id, "material": len(event.material_changes),
            "new_obligation_ids": [o.id for o in new_obls], "retired_ids": [o.id for o in retired],
            "_info": f"unchanged {unchanged} | {kinds} | material {len(event.material_changes)} | "
                     f"retired {len(retired)} | new {len(new_obls)}"}


@step("applicability")
def applicability_node(s: FlowState) -> dict:
    decs = app_ag.decide_all(active(load_extracted("obligations", Obligation)), s["run_id"],
                             to_kg=not s.get("no_kg"))
    new = set(s.get("new_obligation_ids", []))
    ok = [d for d in decs if d.obligation_id in new and d.applicable]
    return {"_info": f"{len(ok)}/{len(new)} new obligation(s) applicable"}


@step("map_new")
def map_node(s: FlowState) -> dict:
    obls = active(load_extracted("obligations", Obligation))
    ctls = load_extracted("controls", Control)
    decs = load_extracted("applicability", ApplicabilityDecision)
    new, live = set(s.get("new_obligation_ids", [])), {o.id for o in obls}
    keep = [m for m in load_extracted("mappings", ControlMapping)
            if m.obligation_id in live and m.obligation_id not in new]   
    ob_by, ct_by = {o.id: o for o in obls}, {c.id: c for c in ctls}
    keep = [map_ag.param_guard(ob_by[m.obligation_id], ct_by[m.control_id], m)
            for m in keep if m.control_id in ct_by]       # reuse, drop retired
    maps, _, stats = map_ag.map_obligations([o for o in obls if o.id in new], ctls, decs, s["run_id"])
    applicable = {d.obligation_id for d in decs if d.applicable}
    cov = map_ag.coverage_of([o for o in obls if o.id in applicable], keep + maps)
    map_ag._save("mappings", keep + maps)
    map_ag._save("coverage", cov)
    return {"_info": f"judged {len(new)} new | reused {len(keep)} mappings | "
                     f"new {[(m.control_id, m.coverage.value) for m in maps]} | {stats}"}


@step("assess")
def assess_node(s: FlowState) -> dict:
    run_id = s["run_id"]
    obls = active(load_extracted("obligations", Obligation))
    ctls = load_extracted("controls", Control)
    decs = load_extracted("applicability", ApplicabilityDecision)
    cov, maps = load_extracted("coverage", ObligationCoverage), load_extracted("mappings", ControlMapping)
    prev = {g.id for g in load_extracted("gaps", Gap)}
    manifest = load_manifest()
    as_of = as_of_from(manifest)
    results = run_tests(ctls, manifest, as_of, run_id)
    gaps = gap_ag.find_gaps(obls, ctls, decs, cov, maps, results, manifest, as_of, run_id)
    gap_ag.add_exposure(gaps, obls, ctls, manifest, as_of, run_id)
    gaps.sort(key=lambda g: -g.risk.score)
    gap_ag._save("test_results", [r for _, r in results])
    gap_ag._save("gaps", gaps)
    new = set(s.get("new_obligation_ids", []))
    impacted = [g for g in gaps if g.obligation_id in new or g.id not in prev]
    review = [g.id for g in impacted if g.risk and g.risk.band in REVIEW_BANDS]
    _queue(review, run_id)
    return {"impacted_gap_ids": [g.id for g in impacted], "review_ids": review,
            "_info": f"{len(gaps)} gaps | impacted {len(impacted)}: " +
                     "; ".join(f"{g.risk.band.value} {g.gap_type.value} {g.control_id or '-'}" for g in impacted[:5])}


@step("critic")
def critic_node(s: FlowState) -> dict:
    issues, new = [], set(s.get("new_obligation_ids", []))
    if new:
        _, _, full = chg.load_version(s["doc_id"], s["to_version"])
        for o in load_extracted("obligations", Obligation):
            if o.id in new and full[o.source.char_start:o.source.char_end] != o.source.quote:
                issues.append(f"SPAN_MISMATCH {o.id}")
        issues += [f"UNVERIFIED_MAPPING {m.id}" for m in load_extracted("mappings", ControlMapping)
                   if m.obligation_id in new and m.confidence <= map_ag.UNVERIFIED_CAP]
    gaps = {g.id: g for g in load_extracted("gaps", Gap)}
    issues += [f"NO_RISK_SCORE {gid}" for gid in s.get("impacted_gap_ids", []) if gid in gaps and not gaps[gid].risk]
    return {"issues": issues, "_info": f"{len(issues)} issue(s)" + (f": {issues[:3]}" if issues else "")}


@step("hitl")
def hitl_node(s: FlowState) -> dict:
    if s.get("auto_approve"):
        decision = {"action": "approve", "reviewer": "auto", "notes": "auto-approved (demo flag)"}
    else:
        gaps = {g.id: g for g in load_extracted("gaps", Gap)}
        decision = interrupt({"run_id": s["run_id"], "issues": s.get("issues", []), "review": [
            {"gap_id": gid, "band": gaps[gid].risk.band.value, "title": gaps[gid].title,
             "detail": gaps[gid].description[:220]} for gid in s.get("review_ids", []) if gid in gaps]})
    approved = decision.get("action") == "approve"
    status = "APPROVED" if approved else "REJECTED"
    with get_conn() as c:
        c.executemany("UPDATE review_queue SET status=?, reviewer=?, decision_notes=? "
                      "WHERE item_type='gap' AND item_id=? AND status='PENDING'",
                      [(status, decision.get("reviewer"), decision.get("notes"), g) for g in s.get("review_ids", [])])
    audit(f"human:{decision.get('reviewer', 'unknown')}", status, "run", s["run_id"], decision, s["run_id"])
    return {"decision": decision, "approved": approved, "_info": f"{status} by {decision.get('reviewer')}"}


@step("commit")
def commit_node(s: FlowState) -> dict:
    if s["event_type"] not in ROUTES:
        status = "UNSUPPORTED"
    elif s.get("material") == 0:
        status = "NO_MATERIAL_CHANGE"
    elif s.get("approved") is False:
        status = "REJECTED"                       # proposed state stays in files; Neo4j untouched
    else:
        status = "COMMITTED"
        if not s.get("no_kg"):
            map_ag._to_kg(load_extracted("mappings", ControlMapping), load_extracted("coverage", ObligationCoverage))
            gap_ag._to_kg(load_manifest(), [(None, r) for r in load_extracted("test_results", ControlTestResult)],
                          load_extracted("gaps", Gap))
    summary = {k: s.get(k) for k in ("change_event_id", "new_obligation_ids", "impacted_gap_ids",
                                     "review_ids", "issues", "decision")} | {"trace": s.get("trace", [])}
    with get_conn() as c:
        c.execute("UPDATE runs SET status=?, finished_at=?, summary=? WHERE run_id=?",
                  (status, utcnow().isoformat(), json.dumps(summary, default=str), s["run_id"]))
    return {"status": status, "_info": status}


# ---------- routing + graph ----------
def route_event(s: FlowState) -> str:
    return ROUTES.get(s["event_type"], "commit")


def after_change(s: FlowState) -> str:
    return "applicability" if s.get("material") else "commit"


def after_critic(s: FlowState) -> str:
    return "hitl" if s.get("review_ids") or s.get("issues") else "commit"


NODES = {"router": router, "ingest": ingest_node, "change": change_node, "applicability": applicability_node,
         "map_new": map_node, "assess": assess_node, "critic": critic_node, "hitl": hitl_node,
         "commit": commit_node}


def build(checkpointer=None):
    g = StateGraph(FlowState)
    for name, fn in NODES.items():
        g.add_node(name, fn)
    g.add_edge(START, "router")
    g.add_conditional_edges("router", route_event, {"ingest": "ingest", "assess": "assess", "commit": "commit"})
    g.add_edge("ingest", "change")
    g.add_conditional_edges("change", after_change, {"applicability": "applicability", "commit": "commit"})
    g.add_edge("applicability", "map_new")
    g.add_edge("map_new", "assess")
    g.add_edge("assess", "critic")
    g.add_conditional_edges("critic", after_critic, {"hitl": "hitl", "commit": "commit"})
    g.add_edge("hitl", "commit")
    g.add_edge("commit", END)
    return g.compile(checkpointer=checkpointer)


def sqlite_checkpointer() -> SqliteSaver:
    path = get_settings().data_dir / "checkpoints.db"
    return SqliteSaver(sqlite3.connect(path, check_same_thread=False))