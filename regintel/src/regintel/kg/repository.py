"""Neo4j repository. All writes are idempotent MERGEs keyed on deterministic IDs."""
from __future__ import annotations

import json
from typing import Any

from neo4j import GraphDatabase
from pydantic import BaseModel

from regintel.schemas.bank import BankProfile
from regintel.schemas.control import Control, Policy
from regintel.schemas.obligation import Obligation
from regintel.schemas.regulation import Clause, DocVersion, Regulation
from regintel.settings import get_settings

LABELS = ["Regulation", "Policy", "DocVersion", "Clause", "Obligation", "Control",
          "Topic", "BankEntity", "Product"]


def props(m: BaseModel, exclude: set[str] | None = None) -> dict[str, Any]:
    """Neo4j stores only primitives / lists of primitives: nested objects become JSON strings."""
    out = {}
    for k, v in m.model_dump(mode="json", exclude=exclude or set()).items():
        if isinstance(v, dict) or (isinstance(v, list) and any(isinstance(x, (dict, list)) for x in v)):
            v = json.dumps(v)
        out[k] = v
    return out


class KG:
    def __init__(self) -> None:
        s = get_settings()
        self.driver = GraphDatabase.driver(
            s.neo4j_uri, auth=(s.neo4j_user, s.neo4j_password.get_secret_value()))

    def __enter__(self) -> "KG":
        return self

    def __exit__(self, *exc) -> None:
        self.driver.close()

    def run(self, query: str, **params: Any) -> list[dict[str, Any]]:
        records, _, _ = self.driver.execute_query(query, parameters_=params)
        return [r.data() for r in records]

    def init_schema(self) -> None:
        for label in LABELS:
            self.run(f"CREATE CONSTRAINT {label.lower()}_id IF NOT EXISTS "
                     f"FOR (n:{label}) REQUIRE n.id IS UNIQUE")

    # ---------- documents ----------
    def upsert_bank(self, bp: BankProfile) -> None:
        self.run("UNWIND $rows AS r MERGE (n:BankEntity {id: r.id}) SET n += r",
                 rows=[props(e) for e in bp.entities])
        self.run("UNWIND $rows AS r MERGE (n:Product {id: r.id}) SET n += r",
                 rows=[props(p) for p in bp.products])

    def upsert_regulation(self, reg: Regulation) -> None:
        self.run("MERGE (n:Regulation {id: $p.id}) SET n += $p", p=props(reg))

    def upsert_policy(self, pol: Policy) -> None:
        self.run("MERGE (n:Policy {id: $p.id}) SET n += $p", p=props(pol))

    def upsert_version(self, v: DocVersion) -> None:
        self.run("""MERGE (n:DocVersion {id: $p.id}) SET n += $p
                    WITH n MATCH (d {id: $p.doc_id}) WHERE d:Regulation OR d:Policy
                    MERGE (d)-[:HAS_VERSION]->(n)""", p=props(v))

    def upsert_clauses(self, clauses: list[Clause]) -> None:
        rows = [props(c, {"context_text"}) for c in clauses]
        self.run("""UNWIND $rows AS r MERGE (c:Clause {id: r.id}) SET c += r
                    WITH c, r MATCH (v:DocVersion {id: r.version_id})
                    MERGE (v)-[:CONTAINS]->(c)""", rows=rows)
        self.run("""UNWIND $rows AS r WITH r WHERE r.parent_id IS NOT NULL
                    MATCH (c:Clause {id: r.id}), (p:Clause {id: r.parent_id})
                    MERGE (p)-[:PARENT_OF]->(c)""", rows=rows)

    # ---------- extracted entities (written in 2B) ----------
    def upsert_obligations(self, obls: list[Obligation]) -> None:
        self.run("""UNWIND $rows AS r MERGE (o:Obligation {id: r.id}) SET o += r
                    WITH o, r MATCH (c:Clause {id: r.clause_id}) MERGE (c)-[:YIELDS]->(o)
                    WITH o, r UNWIND r.topics AS t
                    MERGE (tp:Topic {id: t}) MERGE (o)-[:ABOUT]->(tp)""",
                 rows=[props(o) for o in obls])

    def upsert_controls(self, ctls: list[Control]) -> None:
        self.run("""UNWIND $rows AS r MERGE (k:Control {id: r.id}) SET k += r
                    WITH k, r MATCH (c:Clause {id: r.clause_id}) MERGE (c)-[:DEFINES]->(k)
                    WITH k, r MATCH (p:Policy {id: r.policy_id}) MERGE (k)-[:DEFINED_IN]->(p)
                    WITH k, r UNWIND r.topics AS t
                    MERGE (tp:Topic {id: t}) MERGE (k)-[:ABOUT]->(tp)""",
                 rows=[props(c) for c in ctls])

    # ---------- utilities ----------
    def clear_derived(self, *labels: str) -> None:
        for label in labels:
            self.run(f"MATCH (n:{label}) DETACH DELETE n")

    def counts(self) -> dict[str, int]:
        return {lb: self.run(f"MATCH (n:{lb}) RETURN count(n) AS c")[0]["c"] for lb in LABELS}