"""Phase 5A change intelligence agent: clause-level diff between two regulation versions,
change classification, and targeted re-extraction of changed clauses only.
  python -m regintel.agents.change --doc RBI-KYC-MD [--frm v-demo --to v-amended] [--no-kg]"""
from __future__ import annotations

import re
import uuid
from collections import Counter, defaultdict
from collections.abc import Callable
from datetime import date
from difflib import SequenceMatcher
from typing import Optional

import numpy as np
import typer
from rich.console import Console
from rich.table import Table
from scipy.optimize import linear_sum_assignment

from regintel.agents.extraction import _log_rejected, extract_obligations, load_extracted
from regintel.config import get_sources
from regintel.db import audit, init_db
from regintel.ingestion.run import load_clauses, out_dir
from regintel.llm.client import call_llm
from regintel.retrieval.hybrid import tokens
from regintel.schemas.change import ChangeClassification, ChangeEvent, ClauseChange
from regintel.schemas.common import ChangeType, stable_id
from regintel.schemas.obligation import Obligation
from regintel.schemas.regulation import Clause, DocVersion
from regintel.settings import get_settings

MIN_SIM = 0.5
MARKER = re.compile(r"^\S+\s+")
NOT_FOR_PAIRS = {ChangeType.NEW, ChangeType.DELETED, ChangeType.UNCHANGED}

SYSTEM = """You compare two versions of ONE regulatory clause and classify the change for a bank:
- TIGHTENED: stricter (shorter period, lower threshold, extra duty element, wider scope).
- RELAXED: less strict.
- CLARIFICATION: wording changed but the legal effect is the same.
- EDITORIAL: formatting, punctuation or word order only.
changed_parameters: every number, period, deadline or threshold that changed, as name/old/new,
e.g. {"name": "periodicity", "old": "two years", "new": "one year"}.
summary: one sentence a compliance officer understands. confidence: 0-1."""


# ---------- alignment ----------
def _body(c: Clause) -> str:
    return MARKER.sub("", c.text, count=1)


def _sim(a: Clause, b: Clause) -> float:
    ratio = SequenceMatcher(None, _body(a).lower(), _body(b).lower()).ratio()
    return 0.7 * ratio + 0.3 * (a.clause_ref == b.clause_ref)


def align(old: list[Clause], new: list[Clause]) -> list[tuple[Clause | None, Clause | None, float]]:
    """1) identical content hash, 2) Hungarian assignment on similarity, 3) leftovers = NEW / DELETED."""
    by_hash: dict[str, list[Clause]] = defaultdict(list)
    for c in old:
        by_hash[c.content_hash].append(c)
    pairs, rest_new = [], []
    for n in new:
        if by_hash.get(n.content_hash):
            pairs.append((by_hash[n.content_hash].pop(0), n, 1.0))
        else:
            rest_new.append(n)
    rest_old = [c for lst in by_hash.values() for c in lst]
    used_o, used_n = set(), set()
    if rest_old and rest_new:
        sim = np.array([[_sim(o, n) for n in rest_new] for o in rest_old])
        for i, j in zip(*linear_sum_assignment(-sim)):
            if sim[i, j] >= MIN_SIM:
                pairs.append((rest_old[i], rest_new[j], float(sim[i, j])))
                used_o.add(i)
                used_n.add(j)
    pairs += [(o, None, 0.0) for i, o in enumerate(rest_old) if i not in used_o]
    pairs += [(None, n, 0.0) for j, n in enumerate(rest_new) if j not in used_n]
    return pairs


# ---------- classification ----------
def _bag(s: str) -> list[str]:
    return sorted(re.findall(r"[a-z0-9]+", s.lower()))


def classify(o: Clause, n: Clause, llm: Callable) -> ChangeClassification:
    if _bag(_body(o)) == _bag(_body(n)):
        return ChangeClassification(change_type=ChangeType.EDITORIAL, confidence=1.0,
                                    summary="Same words, different order or punctuation")
    res = llm("change_classification", ChangeClassification, SYSTEM,
              f'<old ref="{o.clause_ref}">{o.text}</old>\n<new ref="{n.clause_ref}">{n.text}</new>')
    if res.change_type in NOT_FOR_PAIRS:                     # invalid for an aligned pair
        res = res.model_copy(update={"change_type": ChangeType.CLARIFICATION})
    return res


def diff_clauses(old: list[Clause], new: list[Clause], doc_id: str,
                 llm: Callable = call_llm) -> tuple[list[ClauseChange], int]:
    changes, unchanged = [], 0
    for o, n, s in align(old, new):
        cid = stable_id("CC", doc_id, o.id if o else "-", n.id if n else "-")
        if o and n and o.content_hash == n.content_hash:
            unchanged += 1
            continue
        if o and n:
            r = classify(o, n, llm)
            changes.append(ClauseChange(id=cid, doc_id=doc_id, old_clause_id=o.id, new_clause_id=n.id,
                                        change_type=r.change_type, similarity=round(s, 3),
                                        changed_parameters=r.changed_parameters, summary=r.summary))
        else:
            ct = ChangeType.NEW if n else ChangeType.DELETED
            changes.append(ClauseChange(id=cid, doc_id=doc_id, old_clause_id=o.id if o else None,
                                        new_clause_id=n.id if n else None, change_type=ct,
                                        summary=f"Clause {(n or o).clause_ref} {ct.value.lower()}"))
    return changes, unchanged


# ---------- apply: retire old obligations, extract new ones ----------
def apply_change(event: ChangeEvent, new_ver: DocVersion, new_clauses: list[Clause], new_full: str,
                 run_id: str, llm: Callable = call_llm) -> tuple[list[Obligation], list[Obligation]]:
    material = event.material_changes
    targets = {c.new_clause_id for c in material if c.new_clause_id}
    new_obls, rejected = (extract_obligations(new_ver, new_clauses, new_full, run_id, llm=llm, only=targets)
                          if targets else ([], []))
    _log_rejected(rejected, new_ver, run_id)
    eff = new_ver.effective_date or date.today()
    retire_from = {c.old_clause_id for c in material if c.old_clause_id}
    pair = {c.new_clause_id: c.old_clause_id for c in material if c.old_clause_id and c.new_clause_id}

    updated, retired = [], []
    for o in load_extracted("obligations", Obligation):
        if o.clause_id in retire_from and o.valid_to in (None, eff):
            o = o.model_copy(update={"valid_to": eff})
            retired.append(o)
        updated.append(o)

    olds = defaultdict(list)
    for o in retired:
        olds[o.clause_id].append(o)
    linked = []
    for n in new_obls:
        if cands := olds.get(pair.get(n.clause_id), []):
            best = max(cands, key=lambda o: len(set(tokens(o.action)) & set(tokens(n.action))))
            n = n.model_copy(update={"supersedes_id": best.id})
        linked.append(n)

    have = {o.id for o in updated}
    final = [o for o in updated if o.id not in {n.id for n in linked}] + linked
    p = get_settings().data_dir / "extracted" / "obligations.jsonl"
    p.write_text("".join(o.model_dump_json() + "\n" for o in final), encoding="utf-8")
    return retired, [n for n in linked if n.id not in have or n.supersedes_id]


# ---------- persistence ----------
def load_version(doc_id: str, label: str) -> tuple[DocVersion, list[Clause], str]:
    d = out_dir(doc_id, label)
    ver = DocVersion.model_validate_json((d / "version.json").read_text(encoding="utf-8"))
    return ver, load_clauses(doc_id, label), (d / "text.txt").read_text(encoding="utf-8")


def resolve_versions(doc_id: str, frm: str | None, to: str | None) -> tuple[str, str]:
    reg = next(r for r in get_sources().regulations if r.id == doc_id)
    vs = sorted(reg.versions, key=lambda v: v.effective_date or date.min)
    if len(vs) < 2 and not (frm and to):
        raise typer.BadParameter(f"{doc_id} has only one version in sources.yaml")
    return frm or vs[-2].label, to or vs[-1].label


def _to_kg(event: ChangeEvent, new_ver: DocVersion, new_clauses: list[Clause],
           retired: list[Obligation], new_obls: list[Obligation]) -> None:
    from regintel.kg.repository import KG
    with KG() as kg:
        kg.upsert_version(new_ver)
        kg.upsert_clauses(new_clauses)
        kg.upsert_obligations(retired + new_obls)
        kg.run("MERGE (e:ChangeEvent {id: $id}) SET e.doc_id = $doc, e.from_version = $frm, "
               "e.to_version = $to, e.detected_at = $at", id=event.id, doc=event.doc_id,
               frm=event.from_version, to=event.to_version, at=event.detected_at.isoformat())
        kg.run("""UNWIND $rows AS r MATCH (e:ChangeEvent {id: $eid})
                  OPTIONAL MATCH (n:Clause {id: r.new}) OPTIONAL MATCH (o:Clause {id: r.old})
                  FOREACH (_ IN CASE WHEN n IS NULL THEN [] ELSE [1] END |
                      MERGE (e)-[:AFFECTS {change_type: r.type}]->(n))
                  FOREACH (_ IN CASE WHEN n IS NULL OR o IS NULL THEN [] ELSE [1] END |
                      MERGE (n)-[:SUPERSEDES {change_type: r.type}]->(o))
                  FOREACH (_ IN CASE WHEN n IS NOT NULL OR o IS NULL THEN [] ELSE [1] END |
                      MERGE (e)-[:AFFECTS {change_type: r.type}]->(o))""", eid=event.id,
               rows=[{"new": c.new_clause_id, "old": c.old_clause_id, "type": c.change_type.value}
                     for c in event.changes])
        kg.run("""UNWIND $rows AS r MATCH (n:Obligation {id: r.new}), (o:Obligation {id: r.old})
                  MERGE (n)-[:SUPERSEDES]->(o)""",
               rows=[{"new": n.id, "old": n.supersedes_id} for n in new_obls if n.supersedes_id])


# ---------- CLI ----------
def main(doc: str = "RBI-KYC-MD", frm: Optional[str] = None, to: Optional[str] = None,
         no_kg: bool = False) -> None:
    init_db()
    con, run_id = Console(), f"RUN-{uuid.uuid4().hex[:10]}"
    frm, to = resolve_versions(doc, frm, to)
    old_ver, old_cl, _ = load_version(doc, frm)
    new_ver, new_cl, new_full = load_version(doc, to)
    changes, unchanged = diff_clauses(old_cl, new_cl, doc)
    event = ChangeEvent(id=stable_id("CHG", doc, frm, to), doc_id=doc, from_version=frm,
                        to_version=to, changes=changes)
    retired, new_obls = apply_change(event, new_ver, new_cl, new_full, run_id)

    out = get_settings().data_dir / "extracted" / "changes"
    out.mkdir(parents=True, exist_ok=True)
    (out / f"{event.id}.json").write_text(
        event.model_dump_json(indent=2, exclude={"changes": {"__all__": {"is_material"}}}), encoding="utf-8")
    if not no_kg:
        _to_kg(event, new_ver, new_cl, retired, new_obls)
    audit("agent:change", "DIFF", "change_event", event.id,
          {"from": frm, "to": to, "unchanged": unchanged,
           "types": dict(Counter(c.change_type.value for c in changes)),
           "retired": len(retired), "new_obligations": len(new_obls)}, run_id)

    refs = {c.id: c.clause_ref for c in old_cl + new_cl}
    t = Table(title=f"{doc}: {frm} -> {to} (effective {new_ver.effective_date}) | unchanged {unchanged}")
    for col in ("Clause", "Change", "Parameters", "Summary"):
        t.add_column(col)
    for c in changes:
        params = "; ".join(f"{p.name}: {p.old} -> {p.new}" for p in c.changed_parameters)
        t.add_row(refs.get(c.new_clause_id or c.old_clause_id, "?"), c.change_type.value, params, c.summary[:70])
    con.print(t)
    con.print(f"material {len(event.material_changes)} | obligations retired {len(retired)} | "
              f"new {len(new_obls)} {[n.summary()[:60] for n in new_obls]}\nrun_id={run_id}")


if __name__ == "__main__":
    typer.run(main)