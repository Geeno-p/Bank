"""Ingest policies + regulations.  python -m regintel.ingestion.run [--only all|policies|regs] [--engine docling|fast]"""
from __future__ import annotations

import hashlib
import json
from datetime import date
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from regintel.config import get_sources
from regintel.ingestion.clause_tree import build_clauses
from regintel.ingestion.parser import parse_document
from regintel.schemas.common import DocType, stable_id
from regintel.schemas.regulation import Clause, DocVersion
from regintel.settings import get_settings
from regintel.db import audit, init_db

def out_dir(doc_id: str, version_label: str) -> Path:
    return get_settings().data_dir / "parsed" / doc_id / version_label


def ingest(path: Path, doc_id: str, version_label: str, doc_type: DocType, effective: date | None,
           engine: str = "docling", force: bool = False) -> tuple[DocVersion, list[Clause], bool]:
    sha = hashlib.sha256(path.read_bytes()).hexdigest()
    out = out_dir(doc_id, version_label)
    meta_f, meta = out / "ingest_meta.json", {"sha256": sha, "engine": engine}
    if not force and meta_f.exists() and json.loads(meta_f.read_text(encoding="utf-8")) == meta:
        ver = DocVersion.model_validate_json((out / "version.json").read_text(encoding="utf-8"))
        return ver, load_clauses(doc_id, version_label), False          # unchanged -> skip

    ver = DocVersion(id=stable_id("VER", doc_id, version_label), doc_id=doc_id, doc_type=doc_type,
                     version_label=version_label, effective_date=effective, valid_from=effective,
                     source_path=str(path), sha256=sha)
    clauses, full = build_clauses(parse_document(path, engine), doc_id, ver.id, version_label, doc_type)
    out.mkdir(parents=True, exist_ok=True)
    (out / "text.txt").write_text(full, encoding="utf-8")
    (out / "version.json").write_text(ver.model_dump_json(indent=2), encoding="utf-8")
    with (out / "clauses.jsonl").open("w", encoding="utf-8") as f:
        for c in clauses:
            f.write(c.model_dump_json(exclude={"context_text"}) + "\n")
    meta_f.write_text(json.dumps(meta), encoding="utf-8")
    audit("agent:ingestion", "INGEST", "doc_version", ver.id,
          {"doc_id": doc_id, "version": version_label, "clauses": len(clauses), "engine": engine})
    return ver, clauses, True


def load_clauses(doc_id: str, version_label: str) -> list[Clause]:
    lines = (out_dir(doc_id, version_label) / "clauses.jsonl").read_text(encoding="utf-8").splitlines()
    return [Clause.model_validate_json(line) for line in lines if line]


def _targets(only: str) -> list[tuple]:
    s, t = get_settings(), []
    if only in ("all", "policies"):
        for p in json.loads((s.data_dir / "policies" / "index.json").read_text(encoding="utf-8")):
            t.append((s.data_dir / p["docx"], p["policy_id"], f"v{p['version']}", DocType.POLICY,
                      date.fromisoformat(p["approved_on"])))
    if only in ("all", "regs"):
        for r in get_sources().regulations:
            for v in r.versions:
                t.append((s.root / v.path, r.id, v.label, DocType.REGULATION, v.effective_date))
    return t


def main(only: str = "all", engine: str = "docling", force: bool = False) -> None:
    init_db()
    con, table = Console(), Table(title=f"Ingestion (engine={engine})")
    for col in ("Doc", "Version", "Clauses", "Pages", "Status"):
        table.add_column(col)
    for path, doc_id, label, dtype, eff in _targets(only):
        if not path.exists():
            con.print(f"[yellow]skip[/] {doc_id}/{label}: {path} not found")
            continue
        _, clauses, changed = ingest(path, doc_id, label, dtype, eff, engine, force)
        pages = max((c.page_end or 0 for c in clauses), default=0) or "-"
        table.add_row(doc_id, label, str(len(clauses)), str(pages), "ingested" if changed else "unchanged")
    con.print(table)


if __name__ == "__main__":
    typer.run(main)