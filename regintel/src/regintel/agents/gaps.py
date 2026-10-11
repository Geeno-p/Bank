"""Phase 4-lite gap agent (no LLM): coverage + evidence tests + evidence presence + staleness -> scored gaps.
  python -m regintel.agents.gaps [--no-kg]"""
from __future__ import annotations

import re
import uuid
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from regintel.agents.extraction import load_extracted
from regintel.agents.mapping import RANK, to_days, periods
from regintel.config import get_risk_weights
from regintel.db import audit, get_conn, init_db
from regintel.engines.evidence_tests import LIBRARY, Procedure, as_of_from, load_manifest, run_tests
from regintel.engines.risk import score
from regintel.retrieval.hybrid import tokens
from regintel.schemas.common import Coverage, GapType, Provenance, RiskBand, stable_id, utcnow
from regintel.schemas.control import Control
from regintel.schemas.evidence import ControlTestResult, Evidence, EvidenceKind, TestType
from regintel.schemas.gap import Gap
from regintel.schemas.mapping import ControlMapping, ObligationCoverage
from regintel.schemas.obligation import ApplicabilityDecision, Obligation
from regintel.settings import get_settings
from regintel.agents.extraction import active 

REVIEWED = re.compile(r"Last design review of this control:\s*(\d{4}-\d{2}-\d{2})")


def find_gaps(obls: list[Obligation], ctls: list[Control], decs: list[ApplicabilityDecision],
              cov: list[ObligationCoverage], maps: list[ControlMapping],
              results: list[tuple[Procedure, ControlTestResult]], manifest: list[Evidence],
              as_of: date, run_id: str, root: Path | None = None) -> list[Gap]:
    root = root or get_settings().data_dir
    ob, applicable = {o.id: o for o in obls}, {d.obligation_id for d in decs if d.applicable}
    by_ctl, n_ctl = defaultdict(list), Counter(m.obligation_id for m in maps)
    for m in maps:
        by_ctl[m.control_id].append(m)
    prov, gaps = Provenance(created_by="agent:gap_engine", run_id=run_id), {}

    def add(gt: GapType, oid: str, cid: str | None, title: str, desc: str, refs=(), rate: float = 0.0):
        g = Gap(id=stable_id("GAP", gt.value, oid, cid or "-"), gap_type=gt, obligation_id=oid, control_id=cid,
                title=title, description=desc, evidence_refs=[r for r in refs if r],
                risk=score(gt, ob.get(oid), as_of, n_ctl.get(oid, 0), rate), provenance=prov, valid_from=as_of)
        gaps.setdefault(g.id, g)

    def link(cid: str, requirement: str | None = None) -> str:
        if requirement:
            want = set(tokens(requirement))
            overlap = lambda o: len(want & set(tokens(f"{o.action} {o.source.quote}")))  # noqa: E731
            best = max(obls, key=overlap, default=None)
            if best and overlap(best) >= 3:
                return best.id
        ms = sorted(by_ctl.get(cid, []), key=lambda m: (RANK[m.coverage], m.confidence), reverse=True)
        return ms[0].obligation_id if ms else "UNLINKED"

    # 1) coverage gaps (from 3B)
    for c in cov:
        if c.obligation_id not in applicable:
            continue
        ref = f"{c.regulation_id} {c.clause_ref}"
        if c.coverage == Coverage.NONE:
            o = ob.get(c.obligation_id)
            add(GapType.UNMAPPED, c.obligation_id, None, f"No control addresses {ref}", o.summary() if o else c.rationale)
        elif c.coverage == Coverage.PARTIAL:
            for m in (m for m in maps if m.obligation_id == c.obligation_id and m.coverage == Coverage.PARTIAL):
                mismatch = any("parameter mismatch" in e.lower() for e in m.missing_elements)
                add(GapType.DESIGN_GAP if mismatch else GapType.PARTIAL_COVERAGE, c.obligation_id, m.control_id,
                    f"{m.control_id} only partially meets {ref}", "; ".join(m.missing_elements) or m.rationale)

    # 2) evidence test failures
    for p, r in results:
        if not r.passed:
            gt = GapType.DESIGN_GAP if r.test_type == TestType.DESIGN else GapType.OPERATING_FAILURE
            add(gt, link(r.control_id, p.requirement), r.control_id, f"{r.control_id}: {p.description} - test failed",
                f"{r.details}; exception rate {r.exception_rate:.1%} vs tolerance {r.tolerance:.0%}",
                [r.evidence_id, r.id], r.exception_rate)

    # 3) no evidence at all
    covered = {cid for e in manifest for cid in e.control_ids}
    for k in ctls:
        if k.id not in covered:
            add(GapType.MISSING_EVIDENCE, link(k.id), k.id, f"{k.id}: no evidence available",
                f"Expected: {', '.join(k.evidence_expected) or 'evidence of operation'}; none in the evidence repository")

    # 4) stale control design
    cycle = 365 * get_risk_weights().get("evidence_staleness_multiplier", 1.25)
    for e in manifest:
        path = root / e.path
        if e.kind != EvidenceKind.ATTESTATION or not path.exists():
            continue
        if (m := REVIEWED.search(path.read_text(encoding="utf-8"))) and (age := (as_of - date.fromisoformat(m.group(1))).days) > cycle:
            for cid in e.control_ids:
                add(GapType.STALE_CONTROL, link(cid), cid, f"{cid}: design not reviewed since {m.group(1)}",
                    f"Last design review {age} days ago (cycle {cycle:.0f} days)", [e.id])
    return list(gaps.values())


def add_exposure(gaps: list[Gap], obls: list[Obligation], ctls: list[Control], manifest: list[Evidence],
                 as_of: date, run_id: str, root: Path | None = None) -> None:
    """For design gaps on threshold-based controls: re-run the test at the REGULATORY threshold."""
    ob, procs = {o.id: o for o in obls}, {p.control_id for p in LIBRARY if p.use_control_param}
    for g in gaps:
        o = ob.get(g.obligation_id)
        if g.gap_type != GapType.DESIGN_GAP or g.control_id not in procs or not o:
            continue
        reg = sorted(periods(o.parameters, o.source.quote))
        if not reg:
            continue
        for _, r in run_tests(ctls, manifest, as_of, run_id, {g.control_id: min(reg)}, root):
            if r.control_id == g.control_id:
                g.description += (f" | Exposure at regulatory period ({min(reg):g} days): "
                                  f"{r.exceptions}/{r.population} ({r.exception_rate:.1%}) overdue")
                g.evidence_refs.append(r.evidence_id)
                linked = round((g.risk.factors.blast_radius - 1) / 0.2) if g.risk else 0
                g.risk = score(g.gap_type, o, as_of, linked, r.exception_rate)
                g.metrics = {"threshold_days": float(min(reg)), "exceptions": r.exceptions,
                             "population": r.population, "exception_rate": r.exception_rate}


# ---------- persistence + CLI ----------
def _save(name: str, items: list) -> None:
    p = get_settings().data_dir / "extracted" / f"{name}.jsonl"
    p.write_text("".join(i.model_dump_json() + "\n" for i in items), encoding="utf-8")


def _queue_critical(gaps: list[Gap], run_id: str) -> int:
    rows = [(stable_id("REV", "gap", g.id), "gap", g.id, "CRITICAL_RISK", None, run_id, utcnow().isoformat())
            for g in gaps if g.risk and g.risk.band == RiskBand.CRITICAL]
    with get_conn() as conn:
        conn.executemany("INSERT OR IGNORE INTO review_queue (id, item_type, item_id, reason, confidence, "
                         "run_id, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)", rows)
    return len(rows)


def _to_kg(manifest: list[Evidence], results: list, gaps: list[Gap]) -> None:
    from regintel.kg.repository import KG, props
    with KG() as kg:
        kg.run("MATCH (n) WHERE n:Gap OR n:TestResult DETACH DELETE n")
        kg.run("""UNWIND $rows AS r MERGE (e:Evidence {id: r.id}) SET e += r
                  WITH e, r UNWIND r.control_ids AS cid MATCH (c:Control {id: cid})
                  MERGE (c)-[:EVIDENCED_BY]->(e)""", rows=[props(e) for e in manifest])
        kg.run("""UNWIND $rows AS r MERGE (t:TestResult {id: r.id}) SET t += r
                  WITH t, r MATCH (e:Evidence {id: r.evidence_id}) MERGE (e)-[:TESTED_BY]->(t)""",
               rows=[props(r) for _, r in results])
        kg.run("""UNWIND $rows AS r MERGE (g:Gap {id: r.id}) SET g += r
                  WITH g, r OPTIONAL MATCH (o:Obligation {id: r.obligation_id})
                  FOREACH (_ IN CASE WHEN o IS NULL THEN [] ELSE [1] END | MERGE (g)-[:ON_OBLIGATION]->(o))
                  WITH g, r OPTIONAL MATCH (c:Control {id: r.control_id})
                  FOREACH (_ IN CASE WHEN c IS NULL THEN [] ELSE [1] END | MERGE (g)-[:ON_CONTROL]->(c))""",
               rows=[{**props(g), "band": g.risk.band.value, "score": g.risk.score} for g in gaps])


def main(no_kg: bool = False) -> None:
    init_db()
    con, run_id = Console(), f"RUN-{uuid.uuid4().hex[:10]}"
    obls, ctls = active(load_extracted("obligations", Obligation)), load_extracted("controls", Control)
    decs = load_extracted("applicability", ApplicabilityDecision)
    cov, maps = load_extracted("coverage", ObligationCoverage), load_extracted("mappings", ControlMapping)
    if not (ctls and cov):
        con.print("[yellow]Run extraction, applicability and mapping first.[/]")
        return
    manifest = load_manifest()
    as_of = as_of_from(manifest)
    results = run_tests(ctls, manifest, as_of, run_id)
    gaps = find_gaps(obls, ctls, decs, cov, maps, results, manifest, as_of, run_id)
    add_exposure(gaps, obls, ctls, manifest, as_of, run_id)
    gaps.sort(key=lambda g: -g.risk.score)
    _save("test_results", [r for _, r in results])
    _save("gaps", gaps)
    n_rev = _queue_critical(gaps, run_id)
    if not no_kg:
        _to_kg(manifest, results, gaps)
    types, bands = Counter(g.gap_type.value for g in gaps), Counter(g.risk.band.value for g in gaps)
    audit("agent:gap_engine", "ASSESS", "run", run_id,
          {"tests": len(results), "failed": sum(not r.passed for _, r in results),
           "gaps": len(gaps), "types": dict(types), "bands": dict(bands), "review": n_rev}, run_id)
    t = Table(title=f"Gaps as of {as_of} ({len(gaps)})")
    for col in ("Band", "Score", "Type", "Control", "Title"):
        t.add_column(col)
    for g in gaps[:15]:
        t.add_row(g.risk.band.value, f"{g.risk.score:.2f}", g.gap_type.value, g.control_id or "-", g.title[:60])
    con.print(t)
    con.print(f"tests {len(results)} (failed {sum(not r.passed for _, r in results)}) | {dict(types)} | "
              f"{dict(bands)} | to review {n_rev}\nrun_id={run_id}")


if __name__ == "__main__":
    typer.run(main)