"""Rule-based applicability agent (no LLM): does an obligation apply to this bank?
  python -m regintel.agents.applicability"""
from __future__ import annotations

import re

from rich.console import Console

from regintel.config import get_bank_profile
from regintel.db import audit, get_conn, init_db
from regintel.schemas.bank import BankProfile
from regintel.schemas.common import stable_id, utcnow
from regintel.schemas.obligation import ApplicabilityDecision, Obligation
from regintel.settings import get_settings
from regintel.agents.extraction import active 

REVIEW_BELOW = 0.8
OTHER_ACTOR = re.compile(r"\b(central government|state government|data protection board|reserve bank|"
                         r"\brbi\b|fiu-ind|director|registry|ckycr|data principal|consent manager|"
                         r"appellate|adjudicat)", re.I)
ENTITY_SCOPE = {r"payments? banks?": "is_payments_bank", r"\bnbfcs?\b|non-banking financial": "is_nbfc"}
OTHER_LICENCES = re.compile(r"co-operative bank|regional rural bank|small finance bank|local area bank", re.I)
ACTIVITY = {r"cross[- ]border|wire transfer": "offers_cross_border",
            r"digital lending|lending service provider": "offers_digital_lending",
            r"\bcloud\b": "uses_cloud", r"outsourc": "uses_it_outsourcing"}
SDF = re.compile(r"significant data fiduciar", re.I)
IT_TOPICS = {"IT_OUTSOURCING", "VENDOR_RISK", "CYBER_SECURITY", "BCP_DR"}


def _d(o: Obligation, ok: bool, scope: list[str], reason: str, conf: float, rule: str) -> ApplicabilityDecision:
    return ApplicabilityDecision(obligation_id=o.id, applicable=ok, scope=scope if ok else [],
                                 reason=reason, confidence=conf, decided_by=f"rule:{rule}")


def decide(o: Obligation, bp: BankProfile) -> ApplicabilityDecision:
    scoped = " ".join(x for x in (o.actor, o.action, o.condition) if x)
    every = [e.id for e in bp.entities]
    scope = ["BUB-HO", "BUB-IT"] if set(o.topics) & IT_TOPICS else every

    if OTHER_ACTOR.search(o.actor):
        return _d(o, False, [], f"Duty falls on '{o.actor}', not on the bank", 0.9, "other_actor")
    for pat, attr in ENTITY_SCOPE.items():
        if re.search(pat, scoped, re.I) and not bp.has(attr):
            return _d(o, False, [], f"Scoped to entity type matching '{pat}'; bank is {bp.licence_type}",
                      0.75, "entity_scope")
    if OTHER_LICENCES.search(scoped) and "all regulated entities" not in scoped.lower():
        return _d(o, False, [], f"Scoped to a different licence type; bank is {bp.licence_type}",
                  0.75, "entity_scope")
    for pat, attr in ACTIVITY.items():
        if re.search(pat, scoped, re.I):
            if not bp.has(attr):
                return _d(o, False, [], f"Bank does not perform activity '{attr}'", 0.85, "activity_scope")
            return _d(o, True, scope, f"Bank performs activity '{attr}'", 0.9, "activity_scope")
    if SDF.search(" ".join([o.actor, scoped, o.source.quote])):
        return _d(o, True, scope, "Significant Data Fiduciary duty: bank not yet notified as SDF; "
                                  "treated as applicable (prudent) pending notification", 0.6, "sdf_uncertain")
    return _d(o, True, scope, f"Applies to regulated entities; bank is {bp.licence_type}", 0.9, "default")


def decide_all(obls: list[Obligation], run_id: str, to_kg: bool = True) -> list[ApplicabilityDecision]:
    bp = get_bank_profile()
    decs = [decide(o, bp) for o in obls]
    out = get_settings().data_dir / "extracted" / "applicability.jsonl"
    out.write_text("".join(d.model_dump_json() + "\n" for d in decs), encoding="utf-8")
    rows = [(stable_id("REV", "applicability", d.obligation_id), "applicability", d.obligation_id,
             "LOW_CONFIDENCE", d.confidence, run_id, utcnow().isoformat())
            for d in decs if d.confidence < REVIEW_BELOW]
    with get_conn() as conn:
        conn.executemany("INSERT OR IGNORE INTO review_queue (id, item_type, item_id, reason, confidence, "
                         "run_id, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)", rows)
    if to_kg:
        from regintel.kg.repository import KG
        with KG() as kg:
            kg.run("""UNWIND $rows AS r MATCH (o:Obligation {id: r.obligation_id})
                      SET o.applicable = r.applicable, o.applicability_reason = r.reason,
                          o.applicability_confidence = r.confidence
                      WITH o, r OPTIONAL MATCH (o)-[old:APPLIES_TO]->() DELETE old
                      WITH DISTINCT o, r UNWIND r.scope AS eid
                      MATCH (e:BankEntity {id: eid}) MERGE (o)-[:APPLIES_TO {reason: r.reason}]->(e)""",
                   rows=[d.model_dump(mode="json") for d in decs])
    audit("agent:applicability", "DECIDE", "run", run_id,
          {"total": len(decs), "applicable": sum(d.applicable for d in decs), "review": len(rows)}, run_id)
    return decs


def main(no_kg: bool = False) -> None:
    import typer  # noqa: F401
    from collections import Counter
    import uuid
    from regintel.agents.extraction import load_extracted
    init_db()
    obls = active(load_extracted("obligations", Obligation))
    if not obls:
        Console().print("[yellow]No obligations found. Run: python -m regintel.agents.extraction --what obligations[/]")
        return    
    decs = decide_all(obls, f"RUN-{uuid.uuid4().hex[:10]}", to_kg=not no_kg)
    con = Console()
    con.print(f"[green]{sum(d.applicable for d in decs)}[/]/{len(decs)} applicable | "
              f"rules {dict(Counter(d.decided_by for d in decs))} | "
              f"to review {sum(d.confidence < REVIEW_BELOW for d in decs)}")


if __name__ == "__main__":
    import typer
    typer.run(main)