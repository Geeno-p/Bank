"""Phase 3B mapping agent: applicable obligation -> top-k candidate controls -> LLM judge.
  python -m regintel.agents.mapping [--k 6] [--limit N] [--no-kg]"""
from __future__ import annotations

import re
import uuid
from collections import Counter, defaultdict
from collections.abc import Callable
from typing import Optional

import typer
from pydantic import Field
from rich.console import Console
from rich.progress import track

from regintel.agents.extraction import _prov, _queue_review, load_extracted, parsed_docs
from regintel.db import audit, init_db
from regintel.guards.span_verifier import make_span
from regintel.llm.client import call_llm
from regintel.retrieval.hybrid import Candidate, ControlIndex
from regintel.schemas.common import Base, Coverage, DocType, Parameter, SourceSpan, stable_id
from regintel.schemas.control import Control
from regintel.schemas.mapping import CandidateJudgement, ControlMapping, ObligationCoverage
from regintel.schemas.obligation import ApplicabilityDecision, Obligation
from regintel.settings import get_settings
from regintel.agents.extraction import active

BATCH, K, UNVERIFIED_CAP = 3, 6, 0.6
RANK = {Coverage.NONE: 0, Coverage.PARTIAL: 1, Coverage.FULL: 2}
WORDNUM = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8,
           "nine": 9, "ten": 10, "twelve": 12, "fifteen": 15, "thirty": 30}
UNIT_DAYS = [("working day", 1.4), ("hour", 1 / 24), ("week", 7), ("month", 30), ("year", 365), ("day", 1)]

PERIOD = re.compile(r"\b(\d+|one|two|three|four|five|six|seven|eight|nine|ten|twelve|fifteen|thirty)?"
                    r"\s*(working day|hour|week|month|year|day)s?\b", re.I)


def periods(params: list[Parameter], text: str = "") -> set[float]:
    """Durations in days from parameters; falls back to the quote text ('every year' -> 365)."""
    out = {d for p in params if (d := to_days(p)) is not None}
    if not out and text:
        for num, unit in PERIOD.findall(text):
            if (d := to_days(Parameter(name="period", value=num or "1", unit=unit))) is not None:
                out.add(d)
    return out

class ObligationJudgement(Base):
    obligation_key: str
    judgements: list[CandidateJudgement] = Field(default_factory=list)


class MappingBatch(Base):
    results: list[ObligationJudgement]


SYSTEM = """You are a bank compliance assessor. For each OBLIGATION, judge which CANDIDATE controls
(from the bank's own policies) address it.
- FULL: the control as written satisfies every element (who, what, when, how often, thresholds, recipients).
- PARTIAL: the control addresses the same requirement but at least one element is missing or weaker
  (longer period, missing recipient, narrower scope). List each shortfall in missing_elements.
- Compare numbers carefully: a control period/deadline LONGER than the regulatory one is PARTIAL.
- Omit candidates that are unrelated or merely share keywords. Return an empty list if none address it.
- control_id must be one of the candidate ids listed for that obligation.
- obligation_quote: exact words from the OBLIGATION text. control_quote: exact words from that CONTROL text.
- rationale: 1-2 sentences. confidence: 0-1.
Return exactly one result per obligation_key."""


# ---------- deterministic parameter guard ----------
def to_days(p: Parameter) -> float | None:
    tok = (p.value.strip().lower().split() or [""])[0]
    n = float(tok) if re.fullmatch(r"\d+(\.\d+)?", tok) else WORDNUM.get(tok)
    unit = f"{p.unit or ''} {p.value}".lower()
    for name, days in UNIT_DAYS:
        if n is not None and name in unit:
            return round(n * days, 1)
    return None


def _fmt(ps: list[Parameter]) -> str:
    return "; ".join(f"{p.name}={p.value}{(' ' + p.unit) if p.unit else ''}" for p in ps) or "-"


def param_guard(o: Obligation, c: Control, j):
    """Works on CandidateJudgement or ControlMapping. Adds a mismatch note when time parameters disagree;
    FULL is downgraded to PARTIAL. Idempotent."""
    if j.coverage == Coverage.NONE or any("parameter mismatch" in e.lower() for e in j.missing_elements):
        return j
    od, cd = periods(o.parameters, o.source.quote), periods(c.parameters, c.source.quote)
    if not od or not cd or od & cd:
        return j
    fmt = lambda s: "/".join(f"{d:g} days" for d in sorted(s))  # noqa: E731
    note = f"Parameter mismatch: regulation {fmt(od)} vs control {fmt(cd)}"
    return j.model_copy(update={"coverage": Coverage.PARTIAL, "missing_elements": [note, *j.missing_elements]})


# ---------- helpers ----------
def clause_lookup() -> dict[str, tuple]:
    out = {}
    for dt in (DocType.REGULATION, DocType.POLICY):
        for ver, clauses, full in parsed_docs(dt):
            out.update({c.id: (c, full, ver.version_label) for c in clauses})
    return out


def _text(clause_id: str, fallback: str, lookup: dict) -> str:
    return lookup[clause_id][0].text if clause_id in lookup else fallback


def _block(key: str, o: Obligation, cands: list[Candidate], lookup: dict) -> str:
    cs = "\n".join(f'  <control id="{x.control.id}" name="{x.control.name}" params="{_fmt(x.control.parameters)}">'
                   f"{_text(x.control.clause_id, x.control.source.quote, lookup)}</control>" for x in cands)
    return (f'<obligation key="{key}" ref="{o.source.clause_ref}" params="{_fmt(o.parameters)}">\n'
            f"  <summary>{o.summary()}</summary>\n  <text>{_text(o.clause_id, o.source.quote, lookup)}</text>\n"
            f"<candidates>\n{cs}\n</candidates>\n</obligation>")


def _span(quote: str, clause_id: str, fallback: SourceSpan, lookup: dict) -> tuple[SourceSpan, bool]:
    if quote and clause_id in lookup:
        cl, full, label = lookup[clause_id]
        if s := make_span(quote, cl, full, label):
            return s, True
    return fallback, False


def coverage_of(obls: list[Obligation], maps: list[ControlMapping]) -> list[ObligationCoverage]:
    by = defaultdict(list)
    for m in maps:
        by[m.obligation_id].append(m)
    out = []
    for o in obls:
        ms = by.get(o.id, [])
        cov = max((m.coverage for m in ms), key=RANK.get, default=Coverage.NONE)
        best = [m for m in ms if m.coverage == cov]
        out.append(ObligationCoverage(
            obligation_id=o.id, regulation_id=o.regulation_id, clause_ref=o.source.clause_ref, coverage=cov,
            control_ids=sorted(m.control_id for m in ms),
            missing_elements=sorted({e for m in best for e in m.missing_elements}) if cov == Coverage.PARTIAL else [],
            rationale=best[0].rationale if best else "No candidate control addresses this obligation"))
    return out


# ---------- agent ----------
def map_obligations(obls: list[Obligation], ctls: list[Control], decs: list[ApplicabilityDecision], run_id: str,
                    llm: Callable = call_llm, k: int = K, limit: int | None = None,
                    index: ControlIndex | None = None, lookup: dict | None = None):
    applicable = {d.obligation_id for d in decs if d.applicable}
    todo = [o for o in obls if o.id in applicable][:limit]
    index = index or ControlIndex(ctls)
    lookup = clause_lookup() if lookup is None else lookup
    by_id, prov = {c.id: c for c in ctls}, _prov("mapping_agent", "mapping_judge", run_id)
    maps, failed = [], set()
    stats = Counter(unverified_quotes=0, invalid_ids=0, guard_downgrades=0)

    for i in track(range(0, len(todo), BATCH), description="mapping", transient=True):
        keyed = {f"O{j + 1}": (o, index.search(o, k=k)) for j, o in enumerate(todo[i:i + BATCH])}
        try:
            res = llm("mapping_judge", MappingBatch, SYSTEM,
                      "\n\n".join(_block(key, o, cs, lookup) for key, (o, cs) in keyed.items()))
        except Exception:  # noqa: BLE001 (one failed batch must not stop the run)
            failed |= {o.id for o, _ in keyed.values()}
            continue
        failed |= {keyed[key][0].id for key in keyed.keys() - {r.obligation_key for r in res.results}}
        for r in res.results:
            if r.obligation_key not in keyed:
                continue
            o, cands = keyed[r.obligation_key]
            allowed, best = {x.control.id for x in cands}, {}
            for j in r.judgements:
                if j.control_id not in allowed:
                    stats["invalid_ids"] += 1
                    continue
                if j.coverage == Coverage.NONE:
                    continue
                g = param_guard(o, by_id[j.control_id], j)
                stats["guard_downgrades"] += g is not j
                #stats["guard_downgrades"] += g.coverage != j.coverage
                if j.control_id not in best or RANK[g.coverage] > RANK[best[j.control_id].coverage]:
                    best[j.control_id] = g
            for cid, j in best.items():
                c = by_id[cid]
                os_, ok1 = _span(j.obligation_quote, o.clause_id, o.source, lookup)
                cs_, ok2 = _span(j.control_quote, c.clause_id, c.source, lookup)
                stats["unverified_quotes"] += (not ok1) + (not ok2)
                maps.append(ControlMapping(
                    id=stable_id("MAP", o.id, cid), obligation_id=o.id, control_id=cid, coverage=j.coverage,
                    rationale=j.rationale, obligation_span=os_, control_span=cs_,
                    missing_elements=j.missing_elements, provenance=prov, valid_from=o.valid_from,
                    confidence=j.confidence if ok1 and ok2 else min(j.confidence, UNVERIFIED_CAP)))
    stats["failed_obligations"] = len(failed)
    return maps, coverage_of([o for o in todo if o.id not in failed], maps), dict(stats)


# ---------- persistence + CLI ----------
def _save(name: str, items: list) -> None:
    p = get_settings().data_dir / "extracted" / f"{name}.jsonl"
    p.write_text("".join(i.model_dump_json() + "\n" for i in items), encoding="utf-8")


def _to_kg(maps: list[ControlMapping], cov: list[ObligationCoverage]) -> None:
    from regintel.kg.repository import KG, props
    with KG() as kg:
        kg.run("MATCH ()-[r:MAPPED_TO]->() DELETE r")
        kg.run("""UNWIND $rows AS r MATCH (o:Obligation {id: r.obligation_id}), (c:Control {id: r.control_id})
                  MERGE (o)-[m:MAPPED_TO {id: r.id}]->(c)
                  SET m.coverage = r.coverage, m.rationale = r.rationale, m.confidence = r.confidence,
                      m.missing_elements = r.missing_elements""", rows=[props(m) for m in maps])
        kg.run("""UNWIND $rows AS r MATCH (o:Obligation {id: r.obligation_id})
                  SET o.coverage = r.coverage, o.missing_elements = r.missing_elements""",
               rows=[c.model_dump(mode="json") for c in cov])


def main(k: int = K, limit: Optional[int] = None, no_kg: bool = False) -> None:
    init_db()
    con, run_id = Console(), f"RUN-{uuid.uuid4().hex[:10]}"
    obls, ctls = active(load_extracted("obligations", Obligation)), load_extracted("controls", Control)
    decs = load_extracted("applicability", ApplicabilityDecision)
    if not (obls and ctls and decs):
        con.print("[yellow]Run extraction (controls + obligations) and applicability first.[/]")
        return
    maps, cov, stats = map_obligations(obls, ctls, decs, run_id, k=k, limit=limit)
    _save("mappings", maps)
    _save("coverage", cov)
    n_rev = _queue_review(maps, "mapping", run_id)
    if not no_kg:
        _to_kg(maps, cov)
    summary = dict(Counter(c.coverage.value for c in cov))
    audit("agent:mapping", "MAP", "run", run_id, {"coverage": summary, **stats, "review": n_rev}, run_id)
    con.print(f"obligations assessed {len(cov)} | coverage {summary} | mappings {len(maps)} | "
              f"review {n_rev} | {stats}\nrun_id={run_id}")


if __name__ == "__main__":
    typer.run(main)