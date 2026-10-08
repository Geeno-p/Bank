"""Load bank profile, regulations, policies and parsed clauses into Neo4j.  python -m regintel.kg.load"""
from __future__ import annotations

import json
from datetime import date

from rich.console import Console
from rich.table import Table

from regintel.config import get_bank_profile, get_sources
from regintel.ingestion.run import load_clauses, out_dir
from regintel.kg.repository import KG
from regintel.schemas.control import Policy
from regintel.schemas.regulation import DocVersion, Regulation
from regintel.settings import get_settings


def _load_version(kg: KG, doc_id: str, label: str) -> int:
    d = out_dir(doc_id, label)
    if not (d / "version.json").exists():
        return 0
    kg.upsert_version(DocVersion.model_validate_json((d / "version.json").read_text(encoding="utf-8")))
    clauses = load_clauses(doc_id, label)
    kg.upsert_clauses(clauses)
    return len(clauses)


def main() -> None:
    con, s = Console(), get_settings()
    with KG() as kg:
        kg.init_schema()
        kg.upsert_bank(get_bank_profile())
        for r in get_sources().regulations:
            kg.upsert_regulation(Regulation(**r.model_dump(exclude={"versions"})))
            for v in r.versions:
                n = _load_version(kg, r.id, v.label)
                con.print(f"{'[green]✓' if n else '[yellow]! not parsed'}[/] {r.id}/{v.label}: {n} clauses")
        for p in json.loads((s.data_dir / "policies" / "index.json").read_text(encoding="utf-8")):
            label = f"v{p['version']}"
            kg.upsert_policy(Policy(id=p["policy_id"], title=p["title"], owner_role=p["owner_role"],
                                    version_label=label, doc_id=p["policy_id"], topics=p["topics"],
                                    valid_from=date.fromisoformat(p["approved_on"])))
            n = _load_version(kg, p["policy_id"], label)
            con.print(f"{'[green]✓' if n else '[yellow]! not parsed'}[/] {p['policy_id']}/{label}: {n} clauses")

        t = Table(title="Neo4j node counts")
        t.add_column("Label")
        t.add_column("Count", justify="right")
        for label, c in kg.counts().items():
            t.add_row(label, str(c))
        con.print(t)


if __name__ == "__main__":
    main()