"""Phase 6B impact agent: change -> obligations -> controls -> owners/systems/evidence/tests -> gaps -> exposure,
plus an LLM executive summary whose numbers are checked against the facts.
  python -m regintel.agents.impact --doc RBI-KYC-MD [--frm v-demo --to v-amended] [--no-kg]"""
from __future__ import annotations

import json
import re
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from typing import Optional

import typer
from rich.console import Console

from regintel.agents import change as chg
from regintel.agents.extraction import load_extracted
from regintel.agents.mapping import RANK
from regintel.engines.evidence_tests import as_of_from, load_manifest
from regintel.llm.client import call_llm
from regintel.schemas.change import ChangeEvent
from regintel.schemas.common import ChangeType, RiskBand, stable_id
from regintel.schemas.control import Control
from regintel.schemas.evidence import ControlTestResult, Evidence
from regintel.schemas.gap import Gap
from regintel.schemas.impact import ChangeImpact, ImpactedControl, ImpactedGap, ImpactNarrative, ImpactReport
from regintel.schemas.mapping import ControlMapping
from regintel.schemas.obligation import Obligation
from regintel.schemas.regulation import DocVersion
from regintel.settings import get_settings

ORDER = {RiskBand.CRITICAL: 3, RiskBand.HIGH: 2, RiskBand.MEDIUM: 1, RiskBand.LOW: 0}
NUM = re.compile(r"\d+(?:[.,]\d+)?")

SYSTEM = """You write a regulatory impact brief for a bank's Chief Compliance Officer.
Use ONLY the FACTS JSON. Every number you write must appear in FACTS. Never invent controls, owners,
systems, dates or figures.
- headline: one line, max 15 words.
- executive_summary: 3-5 sentences: what changed, when it takes effect, which controls/owners/systems
  are affected, the customer exposure, and the risk.
- key_risks: 2-4 bullets.
- recommended_next_steps: 2-4 concrete bullets (owner + action). No dates that are not in FACTS."""


@dataclass
class State:
    """Proposed state (files) at assessment time. Neo4j is only updated at commit."""
    obligations: list[Obligation]
    controls: list[Control]
    mappings: list[ControlMapping]
    gaps: list[Gap]
    tests: list[ControlTestResult]
    evidence: list[Evidence]
    clause_refs: dict[str, str]


def current_state(doc_id: str, frm: str | None, to: str) -> State:
    refs = {}
    for label in filter(None, (frm, to)):
        _, cls, _ = chg.load_version(doc_id, label)
        refs.update({c.id: c.clause_ref for c in cls})
    return State(load_extracted("obligations", Obligation), load_extracted("controls", Control),
                 load_extracted("mappings", ControlMapping), load_extracted("gaps", Gap),
                 load_extracted("test_results", ControlTestResult), load_manifest(), refs)


def _ig(g: Gap) -> ImpactedGap:
    return ImpactedGap(gap_id=g.id, gap_type=g.gap_type.value, band=g.risk.band.value if g.risk else None,
                       score=g.risk.score if g.risk else None, title=g.title)


def _exposure(g: Gap) -> str:
    m = g.metrics
    return (f"{g.control_id}: {int(m['exceptions'])}/{int(m['population'])} ({m['exception_rate']:.1%}) "
            f"non-compliant at the regulatory period of {m['threshold_days']:g} days")


# ---------- deterministic report ----------
def build_report(event: ChangeEvent, new_ver: DocVersion, st: State, as_of: date,
                 run_id: str | None = None) -> ImpactReport:
    ob, ct = {o.id: o for o in st.obligations}, {c.id: c for c in st.controls}
    test = {t.control_id: t for t in st.tests}
    by_ctl = defaultdict(set)
    for m in st.mappings:
        by_ctl[m.control_id].add(m.obligation_id)
    changes, issues, all_gaps = [], [], {}
    for ch in event.material_changes:
        retired = [o.id for o in st.obligations if ch.old_clause_id and o.clause_id == ch.old_clause_id and o.valid_to]
        new = [o.id for o in st.obligations if ch.new_clause_id and o.clause_id == ch.new_clause_id and not o.valid_to]
        ids = set(retired) | set(new)
        ref = st.clause_refs.get(ch.new_clause_id or ch.old_clause_id, "?")
        if ch.change_type != ChangeType.DELETED and not new:
            issues.append(f"NO_OBLIGATION_EXTRACTED {ref}")
        controls = []
        for m in sorted((m for m in st.mappings if m.obligation_id in ids), key=lambda m: -RANK[m.coverage]):
            c = ct.get(m.control_id)
            if not c or any(x.control_id == c.id for x in controls):
                continue
            t = test.get(c.id)
            others = sorted(f"{ob[i].regulation_id} {ob[i].source.clause_ref}"
                            for i in by_ctl[c.id] - ids if i in ob and not ob[i].valid_to)
            controls.append(ImpactedControl(
                control_id=c.id, name=c.name, coverage=m.coverage, owner_role=c.owner_role, systems=c.systems,
                evidence=[e.title for e in st.evidence if c.id in e.control_ids],
                test=t.details if t else None, test_passed=t.passed if t else None, also_covers=others))
        cids = {c.control_id for c in controls}
        gaps = sorted((g for g in st.gaps if g.obligation_id in ids or g.control_id in cids),
                      key=lambda g: -(g.risk.score if g.risk else 0))
        all_gaps.update({g.id: g for g in gaps})
        changes.append(ChangeImpact(clause_ref=ref, change_type=ch.change_type,
                                    changed_parameters=ch.changed_parameters, summary=ch.summary,
                                    retired_obligation_ids=retired, new_obligation_ids=new,
                                    controls=controls, gaps=[_ig(g) for g in gaps]))

    ctl_all = {c.control_id: c for ch in changes for c in ch.controls}
    owners = sorted({c.owner_role for c in ctl_all.values() if c.owner_role})
    systems = sorted({s for c in ctl_all.values() for s in c.systems})
    bands = [g.risk.band for g in all_gaps.values() if g.risk]
    eff = new_ver.effective_date
    return ImpactReport(
        id=stable_id("IMP", event.id), change_event_id=event.id, regulation_id=event.doc_id,
        from_version=event.from_version, to_version=event.to_version, effective_date=eff, as_of=as_of,
        days_to_effective=(eff - as_of).days if eff else None, changes=changes, owners=owners, systems=systems,
        exposure=[_exposure(g) for g in all_gaps.values() if g.metrics],
        totals={"material_changes": len(changes),
                "new_obligations": sum(len(c.new_obligation_ids) for c in changes),
                "retired_obligations": sum(len(c.retired_obligation_ids) for c in changes),
                "controls": len(ctl_all), "owners": len(owners), "systems": len(systems),
                "gaps": len(all_gaps),
                "high_or_critical_gaps": sum(b in (RiskBand.CRITICAL, RiskBand.HIGH) for b in bands)},
        max_band=max(bands, key=ORDER.get, default=None), issues=issues, run_id=run_id)


# ---------- grounded narrative ----------
def _nums(s: str) -> set[float]:
    return {float(x.replace(",", "")) for x in NUM.findall(s)}


def ungrounded_numbers(n: ImpactNarrative, facts: str) -> list[str]:
    allowed = _nums(facts)
    text = " ".join([n.headline, n.executive_summary, *n.key_risks, *n.recommended_next_steps])
    return sorted({x for x in NUM.findall(text) if float(x.replace(",", "")) not in allowed})


def write_narrative(r: ImpactReport, llm: Callable = call_llm) -> ImpactReport:
    facts = json.dumps(r.model_dump(mode="json", exclude={"id", "narrative", "issues", "run_id", "created_at"}),
                       default=str)
    try:
        n = llm("impact_narrative", ImpactNarrative, SYSTEM, f"FACTS:\n{facts}\n\nWrite the brief.")
        bad = ungrounded_numbers(n, facts)
        if bad:   # self-correction with a concrete, checkable trigger
            n = llm("impact_narrative", ImpactNarrative, SYSTEM,
                    f"FACTS:\n{facts}\n\nYour previous draft used numbers that are not in FACTS: {bad}.\n"
                    f"Rewrite it using only numbers from FACTS.\nPREVIOUS DRAFT:\n{n.model_dump_json()}")
            bad = ungrounded_numbers(n, facts)
        return r.model_copy(update={"narrative": n, "issues": r.issues + ([f"UNGROUNDED_NUMBERS {bad}"] if bad else [])})
    except Exception as e:  # noqa: BLE001 (the deterministic report is still useful without a narrative)
        return r.model_copy(update={"issues": r.issues + [f"NARRATIVE_FAILED {e}"[:200]]})


# ---------- persistence ----------
def _dir():
    d = get_settings().data_dir / "extracted" / "impacts"
    d.mkdir(parents=True, exist_ok=True)
    return d


def save(r: ImpactReport) -> None:
    (_dir() / f"{r.id}.json").write_text(r.model_dump_json(indent=2), encoding="utf-8")


def load_report_by_id(report_id: str) -> ImpactReport | None:
    p = _dir() / f"{report_id}.json"
    return ImpactReport.model_validate_json(p.read_text(encoding="utf-8")) if p.exists() else None


def load_reports() -> list[ImpactReport]:
    reps = [ImpactReport.model_validate_json(p.read_text(encoding="utf-8")) for p in _dir().glob("*.json")]
    return sorted(reps, key=lambda r: r.created_at, reverse=True)


def load_event(event_id: str) -> ChangeEvent:
    p = get_settings().data_dir / "extracted" / "changes" / f"{event_id}.json"
    return ChangeEvent.model_validate_json(p.read_text(encoding="utf-8"))


def brief(r: ImpactReport) -> dict:
    n = r.narrative
    return {"report_id": r.id, "regulation": r.regulation_id, "versions": f"{r.from_version} -> {r.to_version}",
            "effective_date": str(r.effective_date), "days_to_effective": r.days_to_effective,
            "headline": n.headline if n else f"Impact of {r.regulation_id} change",
            "summary": n.executive_summary if n else "", "next_steps": n.recommended_next_steps if n else [],
            "totals": r.totals, "owners": r.owners, "systems": r.systems, "exposure": r.exposure,
            "max_band": r.max_band.value if r.max_band else None, "issues": r.issues}


def brief_of(report_id: str | None) -> dict | None:
    r = load_report_by_id(report_id) if report_id else None
    return brief(r) if r else None


def _to_kg(r: ImpactReport) -> None:
    from regintel.kg.repository import KG, props
    with KG() as kg:
        kg.run("MERGE (r:ImpactReport {id: $p.id}) SET r += $p "
               "WITH r MATCH (e:ChangeEvent {id: $p.change_event_id}) MERGE (r)-[:FOR_CHANGE]->(e)", p=props(r))
        kg.run("MATCH (r:ImpactReport {id: $id}) UNWIND $cids AS cid MATCH (c:Control {id: cid}) "
               "MERGE (r)-[:IMPACTS]->(c)", id=r.id,
               cids=sorted({c.control_id for ch in r.changes for c in ch.controls}))
        kg.run("MATCH (r:ImpactReport {id: $id}) UNWIND $gids AS gid MATCH (g:Gap {id: gid}) "
               "MERGE (r)-[:IMPACTS]->(g)", id=r.id, gids=sorted({g.gap_id for ch in r.changes for g in ch.gaps}))


# ---------- CLI ----------
def main(doc: str = "RBI-KYC-MD", frm: Optional[str] = None, to: Optional[str] = None, no_kg: bool = False) -> None:
    con = Console()
    frm, to = chg.resolve_versions(doc, frm, to)
    new_ver, _, _ = chg.load_version(doc, to)
    r = build_report(load_event(stable_id("CHG", doc, frm, to)), new_ver, current_state(doc, frm, to),
                     as_of_from(load_manifest()))
    r = write_narrative(r)
    save(r)
    if not no_kg:
        _to_kg(r)
    b = brief(r)
    con.print(f"[bold]{b['headline']}[/]\n{b['summary']}\n")
    con.print(f"totals {r.totals} | owners {r.owners} | systems {r.systems} | max band {b['max_band']}")
    for e in r.exposure:
        con.print(f"[yellow]exposure[/] {e}")
    for i in r.issues:
        con.print(f"[red]issue[/] {i}")


if __name__ == "__main__":
    typer.run(main)