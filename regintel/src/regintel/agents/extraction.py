"""Phase 2B extraction agents: regulation clauses -> Obligations, policy clauses -> Controls.
Batched (5 clauses/call), span-verified, cached.
  python -m regintel.agents.extraction [--what all|obligations|controls] [--doc ID] [--limit N] [--no-kg]
"""
from __future__ import annotations

import json
import re
import uuid
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Optional, TypeVar

import typer
from pydantic import BaseModel, Field
from rich.console import Console
from rich.progress import track

from regintel.config import get_model_routing, get_taxonomy
from regintel.db import audit, get_conn, init_db
from regintel.guards.span_verifier import make_span
from regintel.ingestion.clause_tree import with_ancestor_context
from regintel.ingestion.run import load_clauses
from regintel.llm.client import call_llm
from regintel.schemas.common import Base, DocType, Provenance, stable_id, utcnow
from regintel.schemas.control import Control, ControlDraft
from regintel.schemas.obligation import Obligation, ObligationDraft
from regintel.schemas.regulation import Clause, DocVersion
from regintel.settings import get_settings
from datetime import date

PROMPT_VERSION = "2b-v1"
BATCH, REVIEW_BELOW, CONTEXT_CHARS = 5, 0.7, 1500
DUTY = re.compile(r"\b(shall|must|required|ensure|prohibited|should|not permitted)\b", re.I)
TAG = re.compile(r"\[([A-Z]{3}-C\d{2})\]")
M = TypeVar("M", bound=BaseModel)


# ---------- batch response models (LLM output) ----------
class ClauseObligations(Base):
    clause_key: str
    obligations: list[ObligationDraft] = Field(default_factory=list)


class ObligationBatch(Base):
    results: list[ClauseObligations]


class ClauseControls(Base):
    clause_key: str
    controls: list[ControlDraft] = Field(default_factory=list)


class ControlBatch(Base):
    results: list[ClauseControls]


SYSTEM_OBL = """You are a banking regulatory analyst. Extract ATOMIC obligations from each TARGET clause.
Rules:
- One obligation = one actor + one duty. Split clauses that impose several duties.
- modality: MUST (shall/must/required), MUST_NOT (shall not/prohibited), SHOULD, MAY.
- CONTEXT is parent text: use it only to understand the duty (a lead-in "shall ... as under:" makes items MUST).
- quote: copy exact words from the TARGET text only. No paraphrase, no ellipsis. 1-2 sentences.
- parameters: every number defining the duty (periods, deadlines, thresholds) as name/value/unit,
  e.g. {{"name": "periodicity", "value": "2", "unit": "years"}}.
- frequency: only if one of the allowed values fits exactly, else null.
- topics: choose from [{topics}].
- Definitions, preamble, headings or clauses with no duty: return an empty list for that clause.
- confidence: 0-1.
Return exactly one result per clause_key."""

SYSTEM_CTL = """You are an internal-controls analyst. Each TARGET clause of a bank policy defines exactly ONE control.
Extract it:
- name; objective (risk addressed); description (what is done, by whom, how).
- control_type: PREVENTIVE / DETECTIVE / CORRECTIVE. nature: MANUAL / AUTOMATED / HYBRID.
- frequency: one of the allowed values or null. owner_role and systems if stated.
- parameters: every number defining the control (periods, deadlines, thresholds) as name/value/unit.
- evidence_expected: records an auditor would request to prove the control operates.
- topics: choose from [{topics}].
- quote: exact words from the TARGET text stating the requirement. No paraphrase.
- confidence: 0-1.
Return exactly one result per clause_key, each with exactly one control."""


# ---------- candidate selection (no LLM) ----------
def obligation_candidates(clauses: list[Clause]) -> list[tuple[Clause, str]]:
    ctx = with_ancestor_context(clauses)
    parents = {c.parent_id for c in clauses if c.parent_id}
    out = []
    for c in clauses:
        body = c.text.strip()
        if c.level == 0 or not DUTY.search(ctx[c.id]):
            continue
        if c.id in parents and (body.endswith((":", "-", "\u2014")) or len(body) < 60):
            continue  # lead-in/heading: duties are extracted from its children (with this as context)
        out.append((c, ctx[c.id]))
    return out


def control_candidates(clauses: list[Clause]) -> list[tuple[Clause, str]]:
    ctx = with_ancestor_context(clauses)
    tagged = [c for c in clauses if TAG.search(c.text)]
    pool = tagged or [c for c in clauses if c.level > 0 and DUTY.search(c.text)]  # untagged policies
    return [(c, ctx[c.id]) for c in pool]


# ---------- helpers ----------
def _user(keyed: dict[str, tuple[Clause, str]]) -> str:
    parts = []
    for key, (c, ctx) in keyed.items():
        context = ctx[: -len(c.text)].strip()[-CONTEXT_CHARS:] or "-"
        parts.append(f'<clause key="{key}" ref="{c.clause_ref}">\n<context>{context}</context>\n'
                     f"<target>{c.text}</target>\n</clause>")
    return "\n\n".join(parts)


def _topic_ids() -> str:
    return ", ".join(sorted(get_taxonomy().ids()))


def _topics(proposed: list[str], text: str) -> list[str]:
    tax = get_taxonomy()
    ok = sorted({t for t in proposed if t in tax.ids()})
    low = text.lower()
    return ok or [t.id for t in tax.topics if any(k in low for k in t.keywords)]


def _prov(agent: str, task: str, run_id: str) -> Provenance:
    return Provenance(created_by=f"agent:{agent}", run_id=run_id,
                      model=get_model_routing().for_task(task).model, prompt_version=PROMPT_VERSION)


def _run_batches(task: str, batch_model: type[M], system: str, cands: list[tuple[Clause, str]],
                 llm: Callable[..., M]) -> tuple[list[tuple[Clause, BaseModel]], list[dict]]:
    pairs, errors = [], []
    for i in track(range(0, len(cands), BATCH), description=task, transient=True):
        keyed = {f"C{j + 1}": cc for j, cc in enumerate(cands[i:i + BATCH])}
        try:
            res = llm(task, batch_model, system, _user(keyed))
        except Exception as e:  # noqa: BLE001 (one bad batch must not stop the run)
            errors += [{"clause_ref": c.clause_ref, "reason": f"LLM_ERROR: {e}"[:300]} for c, _ in keyed.values()]
            continue
        returned = {r.clause_key for r in res.results}
        errors += [{"clause_ref": keyed[k][0].clause_ref, "reason": "NO_RESULT"} for k in keyed if k not in returned]
        pairs += [(keyed[r.clause_key][0], r) for r in res.results if r.clause_key in keyed]
    return pairs, errors


# ---------- agents ----------
def extract_obligations(ver: DocVersion, clauses: list[Clause], full: str, run_id: str,
                        llm: Callable = call_llm, limit: int | None = None, only: set[str] | None = None):
    cands = [cc for cc in obligation_candidates(clauses) if only is None or cc[0].id in only][:limit]
    pairs, rejected = _run_batches("obligation_extraction", ObligationBatch,
                                   SYSTEM_OBL.format(topics=_topic_ids()), cands, llm)
    prov, kept = _prov("obligation_extractor", "obligation_extraction", run_id), {}
    for c, r in pairs:
        for d in r.obligations:
            span = make_span(d.quote, c, full, ver.version_label)
            if span is None:
                rejected.append({"clause_ref": c.clause_ref, "reason": "SPAN_NOT_FOUND", "quote": d.quote})
                continue
            o = Obligation(id=stable_id("OBL", c.id, span.quote, d.action.lower()), regulation_id=ver.doc_id,
                           clause_id=c.id, source=span, topics=_topics(d.topics, c.text), provenance=prov,
                           valid_from=ver.effective_date, **d.model_dump(exclude={"quote", "topics"}))
            kept[o.id] = o
    return list(kept.values()), rejected


def extract_controls(ver: DocVersion, clauses: list[Clause], full: str, run_id: str,
                     llm: Callable = call_llm, limit: int | None = None):
    cands = control_candidates(clauses)[:limit]
    pairs, rejected = _run_batches("control_extraction", ControlBatch,
                                   SYSTEM_CTL.format(topics=_topic_ids()), cands, llm)
    prov, kept = _prov("control_extractor", "control_extraction", run_id), {}
    for c, r in pairs:
        if not r.controls:
            rejected.append({"clause_ref": c.clause_ref, "reason": "NO_CONTROL_RETURNED"})
            continue
        d = max(r.controls, key=lambda x: x.confidence)
        span = make_span(d.quote, c, full, ver.version_label)
        if span is None:
            rejected.append({"clause_ref": c.clause_ref, "reason": "SPAN_NOT_FOUND", "quote": d.quote})
            continue
        tag = TAG.search(c.text)
        k = Control(id=tag.group(1) if tag else stable_id("CTL", c.id), policy_id=ver.doc_id, clause_id=c.id,
                    source=span, topics=_topics(d.topics, c.text), provenance=prov,
                    valid_from=ver.effective_date, **d.model_dump(exclude={"quote", "topics"}))
        kept[k.id] = k
    return list(kept.values()), rejected


# ---------- persistence ----------
def _out(name: str) -> Path:
    return get_settings().data_dir / "extracted" / f"{name}.jsonl"


def load_extracted(name: str, model: type[M]) -> list[M]:
    p = _out(name)
    lines = p.read_text(encoding="utf-8").splitlines() if p.exists() else []
    return [model.model_validate_json(x) for x in lines if x.strip()]


def _save(name: str, items: list[BaseModel], doc_field: str, doc_ids: set[str]) -> None:
    p = _out(name)
    p.parent.mkdir(parents=True, exist_ok=True)
    old = [x for x in (p.read_text(encoding="utf-8").splitlines() if p.exists() else [])
           if x.strip() and json.loads(x)[doc_field] not in doc_ids]  # replace only re-extracted docs
    p.write_text("".join(f"{x}\n" for x in old + [i.model_dump_json() for i in items]), encoding="utf-8")


def _log_rejected(rows: list[dict], ver: DocVersion, run_id: str) -> None:
    if rows:
        with _out("rejected").open("a", encoding="utf-8") as f:
            for r in rows:
                f.write(json.dumps({"run_id": run_id, "doc_id": ver.doc_id, "version": ver.version_label, **r}) + "\n")


def _queue_review(items: list, item_type: str, run_id: str) -> int:
    rows = [(stable_id("REV", item_type, i.id), item_type, i.id, "LOW_CONFIDENCE", i.confidence,
             run_id, utcnow().isoformat()) for i in items if i.confidence < REVIEW_BELOW]
    with get_conn() as conn:
        conn.executemany("INSERT OR IGNORE INTO review_queue (id, item_type, item_id, reason, confidence, "
                         "run_id, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)", rows)
    return len(rows)


def _to_kg(obls: list, reg_ids: set[str], ctls: list, pol_ids: set[str]) -> None:
    from regintel.kg.repository import KG
    with KG() as kg:
        if reg_ids:
            kg.run("MATCH (o:Obligation) WHERE o.regulation_id IN $ids DETACH DELETE o", ids=sorted(reg_ids))
            kg.upsert_obligations(obls)
        if pol_ids:
            kg.run("MATCH (k:Control) WHERE k.policy_id IN $ids DETACH DELETE k", ids=sorted(pol_ids))
            kg.upsert_controls(ctls)


def parsed_docs(doc_type: DocType, only: str | None = None,
                base_only: bool = False) -> Iterator[tuple[DocVersion, list[Clause], str]]:
    """base_only=True -> earliest version per document (later versions are handled by the change agent)."""
    vers = []
    for vf in (get_settings().data_dir / "parsed").glob("*/*/version.json"):
        ver = DocVersion.model_validate_json(vf.read_text(encoding="utf-8"))
        if ver.doc_type == doc_type and (only is None or ver.doc_id == only):
            vers.append((ver, vf.parent))
    vers.sort(key=lambda x: (x[0].doc_id, x[0].effective_date or date.min, x[0].version_label))
    seen: set[str] = set()
    for ver, d in vers:
        if base_only and ver.doc_id in seen:
            continue
        seen.add(ver.doc_id)
        yield ver, load_clauses(ver.doc_id, ver.version_label), (d / "text.txt").read_text(encoding="utf-8")


def active(items: list[M]) -> list[M]:
    """Forward-looking view: everything not yet superseded (valid_to is None)."""
    return [i for i in items if getattr(i, "valid_to", None) is None]


# ---------- CLI ----------
def main(what: str = "all", doc: Optional[str] = None, limit: Optional[int] = None, no_kg: bool = False) -> None:
    init_db()
    con, run_id = Console(), f"RUN-{uuid.uuid4().hex[:10]}"
    results = {"obligation": ([], set()), "control": ([], set())}
    jobs = [(DocType.REGULATION, extract_obligations, "obligation")] if what in ("all", "obligations") else []
    jobs += [(DocType.POLICY, extract_controls, "control")] if what in ("all", "controls") else []
    for dtype, fn, kind in jobs:
        items_all, ids = results[kind]
        for ver, clauses, full in parsed_docs(dtype, doc, base_only=True):
            items, rejected = fn(ver, clauses, full, run_id, limit=limit)
            items_all += items
            ids.add(ver.doc_id)
            n_rev = _queue_review(items, kind, run_id)
            _log_rejected(rejected, ver, run_id)
            audit(f"agent:{kind}_extractor", "EXTRACT", "doc_version", ver.id,
                  {"items": len(items), "rejected": len(rejected), "review": n_rev}, run_id)
            con.print(f"{ver.doc_id}/{ver.version_label}: [green]{len(items)} {kind}s[/] | "
                      f"rejected {len(rejected)} | to review {n_rev}")
    (obls, reg_ids), (ctls, pol_ids) = results["obligation"], results["control"]
    if reg_ids:
        _save("obligations", obls, "regulation_id", reg_ids)
    if pol_ids:
        _save("controls", ctls, "policy_id", pol_ids)
    if not no_kg and (reg_ids or pol_ids):
        _to_kg(obls, reg_ids, ctls, pol_ids)
        con.print("[green]✓[/] written to Neo4j")
    con.print(f"run_id={run_id}")


if __name__ == "__main__":
    typer.run(main)