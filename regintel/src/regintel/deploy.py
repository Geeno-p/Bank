"""Deployment helpers.
  python -m regintel.deploy export-cache   # LLM cache -> data/llm_cache.jsonl (commit it)
  python -m regintel.deploy sync-kg        # push all local results to the Neo4j in env/.env (e.g. AuraDB)"""
from __future__ import annotations

import json

import typer

from regintel.db import get_conn, init_db
from regintel.settings import get_settings

cli = typer.Typer(add_completion=False)
COLS = ("key", "task", "model", "response", "created_at")


def _cache_file():
    return get_settings().data_dir / "llm_cache.jsonl"


@cli.command("export-cache")
def export_cache() -> int:
    init_db()
    with get_conn() as c:
        rows = [dict(r) for r in c.execute(f"SELECT {', '.join(COLS)} FROM llm_cache")]
    _cache_file().write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    print(f"exported {len(rows)} cached LLM responses -> {_cache_file()}")
    return len(rows)


def bootstrap() -> dict:
    """Run once per app process: create tables and seed the LLM cache (avoids re-spending free quota)."""
    init_db()
    p, n = _cache_file(), 0
    if p.exists():
        rows = [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x.strip()]
        with get_conn() as c:
            c.executemany(f"INSERT OR IGNORE INTO llm_cache ({', '.join(COLS)}) VALUES "
                          f"({', '.join(':' + k for k in COLS)})", rows)
        n = len(rows)
    return {"llm_cache_seeded": n}


@cli.command("sync-kg")
def sync_kg() -> None:
    from regintel.agents import gaps as gap_ag
    from regintel.agents import mapping as map_ag
    from regintel.agents.extraction import load_extracted
    from regintel.engines.evidence_tests import load_manifest
    from regintel.kg import load as kg_load
    from regintel.kg.repository import KG
    from regintel.schemas.control import Control
    from regintel.schemas.evidence import ControlTestResult
    from regintel.schemas.gap import Gap
    from regintel.schemas.mapping import ControlMapping, ObligationCoverage
    from regintel.schemas.obligation import ApplicabilityDecision, Obligation

    print(f"target: {get_settings().neo4j_uri}")
    kg_load.main()                                          # bank, regulations, policies, versions, clauses
    obls = load_extracted("obligations", Obligation)
    with KG() as kg:
        kg.upsert_obligations(obls)
        kg.upsert_controls(load_extracted("controls", Control))
        kg.run("UNWIND $rows AS r MATCH (o:Obligation {id: r.obligation_id}) "
               "SET o.applicable = r.applicable, o.applicability_reason = r.reason",
               rows=[d.model_dump(mode="json") for d in load_extracted("applicability", ApplicabilityDecision)])
        kg.run("UNWIND $rows AS r MATCH (n:Obligation {id: r.new}), (o:Obligation {id: r.old}) "
               "MERGE (n)-[:SUPERSEDES]->(o)",
               rows=[{"new": o.id, "old": o.supersedes_id} for o in obls if o.supersedes_id])
    map_ag._to_kg(load_extracted("mappings", ControlMapping), load_extracted("coverage", ObligationCoverage))
    gap_ag._to_kg(load_manifest(), [(None, r) for r in load_extracted("test_results", ControlTestResult)],
                  load_extracted("gaps", Gap))
    print("✓ knowledge graph synced")


if __name__ == "__main__":
    cli()