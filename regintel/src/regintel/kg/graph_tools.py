"""Graph tools for the Copilot and MCP: fixed multi-hop Cypher queries + guarded text-to-Cypher."""
from __future__ import annotations

from collections.abc import Callable
from functools import lru_cache, wraps

from neo4j import GraphDatabase, RoutingControl
from neo4j.exceptions import DriverError, Neo4jError
from pydantic import BaseModel, Field

from regintel.kg.guard import GuardError, run_readonly
from regintel.llm.client import call_llm
from regintel.settings import get_settings

MAX_FIX = 2
RANK = {"NONE": 0, "PARTIAL": 1, "FULL": 2}
PROPERTIES = {
    "Regulation": "id, title, issuer",
    "DocVersion": "id, doc_id, version_label, effective_date",
    "Clause": "id, doc_id, version_id, clause_ref, text, level",
    "Obligation": "id, regulation_id, clause_id, actor, modality, action, topics (list), valid_from, "
                  "valid_to (null = active), supersedes_id, applicable (bool), coverage (FULL|PARTIAL|NONE)",
    "Control": "id, policy_id, name, objective, control_type, nature, frequency, owner_role, systems (list)",
    "Policy": "id, title, owner_role",
    "Role": "id (role name)", "System": "id (system name)", "Topic": "id (e.g. KYC, CONSENT, AML_CFT)",
    "BankEntity": "id, name", "Product": "id, name, inherent_risk",
    "Evidence": "id, title, kind, control_ids (list)",
    "TestResult": "id, control_id, evidence_id, procedure, passed (bool), exceptions, population, exception_rate",
    "Gap": "id, gap_type, band (CRITICAL|HIGH|MEDIUM|LOW), score, title, description, obligation_id, control_id",
    "ChangeEvent": "id, doc_id, from_version, to_version",
    "MAPPED_TO (relationship)": "coverage (FULL|PARTIAL), missing_elements (list), confidence",
    "AFFECTS / SUPERSEDES (relationship)": "change_type",
}


@lru_cache
def driver():
    s = get_settings()
    return GraphDatabase.driver(s.neo4j_uri, auth=(s.neo4j_user, s.neo4j_password.get_secret_value()))


def _read(query: str, **params) -> list[dict]:
    """Trusted, fixed queries only (LLM/user Cypher goes through run_readonly)."""
    records, _, _ = driver().execute_query(query, parameters_=params, routing_=RoutingControl.READ)
    return [r.data() for r in records]


def graph_safe(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except GuardError as e:
            return {"error": f"blocked by read-only guard: {e}"}
        except (Neo4jError, DriverError) as e:
            return {"error": f"graph unavailable or query failed: {e}"[:400]}
    return wrapper


# ---------------- fixed tools ----------------
@graph_safe
def graph_schema() -> dict:
    """Graph schema: node labels with counts, relationship patterns (from)-[type]->(to) with counts, and key
    properties. Call this before run_cypher."""
    labels = _read("MATCH (n) UNWIND labels(n) AS label RETURN label, count(*) AS count ORDER BY label")
    patterns = _read("MATCH (a)-[r]->(b) RETURN labels(a)[0] AS from, type(r) AS rel, labels(b)[0] AS to, "
                     "count(*) AS count ORDER BY rel")
    return {"labels": labels, "patterns": patterns, "properties": PROPERTIES}


IMPACT_Q = """
MATCH (cl:Clause {doc_id: $reg, clause_ref: $ref})-[:YIELDS]->(o:Obligation)
WHERE o.valid_to IS NULL
OPTIONAL MATCH (o)-[m:MAPPED_TO]->(c:Control)
OPTIONAL MATCH (c)-[:OWNED_BY]->(ro:Role)
OPTIONAL MATCH (c)-[:RUNS_ON]->(sy:System)
OPTIONAL MATCH (c)-[:EVIDENCED_BY]->(e:Evidence)
OPTIONAL MATCH (e)-[:TESTED_BY]->(t:TestResult) WHERE t.control_id = c.id
WITH o, collect(DISTINCT {control: c.id, name: c.name, coverage: m.coverage}) AS controls,
     collect(DISTINCT ro.id) AS owners, collect(DISTINCT sy.id) AS systems, collect(DISTINCT e.title) AS evidence,
     collect(DISTINCT {control: t.control_id, test: t.procedure, passed: t.passed, details: t.details}) AS tests
OPTIONAL MATCH (g:Gap) WHERE g.obligation_id = o.id OR g.control_id IN [x IN controls | x.control]
RETURN o.id AS obligation_id, o.action AS action, o.valid_from AS valid_from, controls, owners, systems,
       evidence, tests,
       collect(DISTINCT {gap: g.id, type: g.gap_type, band: g.band, detail: left(coalesce(g.description, ''), 200)}) AS gaps
"""


@graph_safe
def impact_of_clause(clause_ref: str, regulation_id: str = "RBI-KYC-MD") -> dict:
    """Multi-hop impact of one regulatory clause (e.g. '12(a)'): active obligations -> mapped controls -> owners,
    systems, evidence, test results -> gaps. Use for 'what is affected if this clause changes?'."""
    rows = _read(IMPACT_Q, reg=regulation_id, ref=clause_ref.strip())
    obls = [{"obligation_id": r["obligation_id"], "action": r["action"], "valid_from": r["valid_from"],
             "controls": [c for c in r["controls"] if c["control"]], "owners": r["owners"],
             "systems": r["systems"], "evidence": r["evidence"],
             "tests": [t for t in r["tests"] if t["test"]], "gaps": [g for g in r["gaps"] if g["gap"]]}
            for r in rows]
    if not obls:
        return {"clause": f"{regulation_id} {clause_ref}", "obligations": [],
                "note": "no active obligation for this clause; check the clause_ref with search_obligations"}
    every = lambda key, field=None: sorted({x[field] if field else x for o in obls for x in o[key]})  # noqa: E731
    return {"clause": f"{regulation_id} {clause_ref}",
            "summary": {"obligations": len(obls), "controls": every("controls", "control"),
                        "owners": every("owners"), "systems": every("systems"),
                        "gaps": every("gaps", "gap")},
            "obligations": obls}


@graph_safe
def shared_controls(min_regulations: int = 2) -> list[dict]:
    """Controls that satisfy active obligations from at least `min_regulations` different regulations
    (cross-regulation reuse). Use min_regulations=1 to rank all controls by obligations covered."""
    return _read("""MATCH (o:Obligation)-[:MAPPED_TO]->(c:Control) WHERE o.valid_to IS NULL
                    WITH c, collect(DISTINCT o.regulation_id) AS regs, count(DISTINCT o) AS n
                    WHERE size(regs) >= $min
                    RETURN c.id AS control_id, c.name AS name, regs AS regulations, n AS obligations
                    ORDER BY size(regs) DESC, n DESC""", min=int(min_regulations))


def _coverage_change(mappings: list[dict], control_id: str) -> tuple[str, str]:
    def best(ms: list[dict]) -> str:
        return max((m["coverage"] for m in ms), key=lambda c: RANK.get(c, 0), default="NONE")
    return best(mappings), best([m for m in mappings if m["control"] != control_id])


@graph_safe
def coverage_if_control_removed(control_id: str) -> dict:
    """What-if: which obligations lose coverage if this control is retired or fails completely."""
    cid = control_id.strip().upper()
    rows = _read("""MATCH (o:Obligation)-[:MAPPED_TO]->(:Control {id: $cid}) WHERE o.valid_to IS NULL
                    MATCH (o)-[m:MAPPED_TO]->(c:Control)
                    RETURN o.id AS obligation_id, o.regulation_id AS regulation, o.action AS action,
                           collect({control: c.id, coverage: m.coverage}) AS mappings""", cid=cid)
    details = []
    for r in rows:
        before, after = _coverage_change(r["mappings"], cid)
        details.append({"obligation_id": r["obligation_id"], "regulation": r["regulation"],
                        "action": (r["action"] or "")[:120], "before": before, "after": after,
                        "remaining_controls": sorted({m["control"] for m in r["mappings"] if m["control"] != cid})})
    return {"control_id": cid, "obligations_mapped": len(details),
            "would_lose_coverage": sum(d["before"] != d["after"] for d in details),
            "would_become_unmapped": sum(d["after"] == "NONE" for d in details), "details": details}


@graph_safe
def obligation_history(obligation_id: str) -> list[dict] | dict:
    """Version history of an obligation along SUPERSEDES links: what the regulation required before and after
    an amendment, with validity dates and clause text."""
    rows = _read("""MATCH (o:Obligation {id: $oid})
                    OPTIONAL MATCH (o)-[:SUPERSEDES*0..5]->(older:Obligation)
                    OPTIONAL MATCH (newer:Obligation)-[:SUPERSEDES*1..5]->(o)
                    WITH collect(DISTINCT older) + collect(DISTINCT newer) AS chain
                    UNWIND chain AS x
                    OPTIONAL MATCH (v:DocVersion)-[:CONTAINS]->(cl:Clause)-[:YIELDS]->(x)
                    RETURN DISTINCT x.id AS obligation_id, x.action AS action, x.valid_from AS valid_from,
                           x.valid_to AS valid_to, v.version_label AS version, cl.text AS clause_text
                    ORDER BY coalesce(x.valid_from, '')""", oid=obligation_id)
    return rows or {"error": f"obligation {obligation_id} not found in the graph"}


# ---------------- text-to-Cypher ----------------
class CypherDraft(BaseModel):
    cypher: str = Field(description="One read-only Cypher query")
    explanation: str = Field(description="One sentence: what the query returns")


SYSTEM_T2C = """You translate questions about a bank's compliance knowledge graph into ONE read-only Cypher query (Neo4j 5).
Rules:
- Read-only: MATCH, OPTIONAL MATCH, WITH, WHERE, UNWIND, RETURN, ORDER BY, LIMIT, aggregations.
- Use ONLY the labels, relationship types, directions and properties listed below.
- Active obligations: o.valid_to IS NULL. IDs and dates are strings.
- Return readable columns (ids, names, titles), not whole nodes. LIMIT 50 or less.
- Write literal values; do not use parameters ($x).
SCHEMA:
{schema}
EXAMPLES:
{examples}"""

EXAMPLES = """Q: Which controls are owned by the Chief Information Security Officer?
C: MATCH (c:Control)-[:OWNED_BY]->(:Role {id: 'Chief Information Security Officer'}) RETURN c.id, c.name
Q: Which HIGH or CRITICAL gaps are on KYC policy controls?
C: MATCH (g:Gap)-[:ON_CONTROL]->(c:Control) WHERE g.band IN ['HIGH','CRITICAL'] AND c.policy_id = 'KYC-POL' RETURN g.id, g.band, g.gap_type, c.id ORDER BY g.score DESC
Q: Which applicable DPDP obligations have no mapped control?
C: MATCH (o:Obligation {regulation_id: 'DPDP-2023'}) WHERE o.valid_to IS NULL AND o.applicable = true AND NOT (o)-[:MAPPED_TO]->(:Control) RETURN o.id, o.action
Q: Which controls failed their evidence test?
C: MATCH (c:Control)-[:EVIDENCED_BY]->(:Evidence)-[:TESTED_BY]->(t:TestResult) WHERE t.control_id = c.id AND t.passed = false RETURN DISTINCT c.id, t.procedure, t.exception_rate
Q: Which clauses changed in the RBI-KYC-MD amendment and how?
C: MATCH (:ChangeEvent {doc_id: 'RBI-KYC-MD'})-[a:AFFECTS]->(cl:Clause) RETURN cl.clause_ref, a.change_type, left(cl.text, 120) AS text"""


def schema_text() -> str:
    s = graph_schema()
    pats = [f"(:{p['from']})-[:{p['rel']}]->(:{p['to']})  x{p['count']}" for p in s.get("patterns", [])]
    return ("Relationship patterns:\n" + ("\n".join(pats) or "(graph unavailable)") +
            "\nProperties:\n" + "\n".join(f"{k}: {v}" for k, v in PROPERTIES.items()))


def text_to_cypher(question: str, llm: Callable = call_llm, execute: Callable | None = None,
                   schema: str | None = None) -> dict:
    execute = execute or (lambda c: run_readonly(c, driver()))
    system = SYSTEM_T2C.format(schema=schema or schema_text(), examples=EXAMPLES)
    feedback, draft, err = "", None, None
    for attempt in range(1, MAX_FIX + 2):
        draft = llm("text_to_cypher", CypherDraft, system, f"QUESTION: {question}{feedback}")
        try:
            res = execute(draft.cypher)
        except (GuardError, Neo4jError) as e:       # self-correction: show the error, ask for a fix
            err = e
            feedback += (f"\n\nPREVIOUS ATTEMPT {attempt}:\n{draft.cypher}\nERROR: {str(e)[:300]}\n"
                         f"Return a corrected query.")
            continue
        return {"question": question, "cypher": res["cypher"], "explanation": draft.explanation,
                "columns": res.get("columns", []), "rows": res["rows"], "truncated": res.get("truncated", False),
                "attempts": attempt,
                "note": "EXPLORATORY: generated Cypher. Check the query before relying on the result."}
    return {"error": f"no valid read-only query after {MAX_FIX + 1} attempts: {err}"[:400],
            "last_cypher": draft.cypher if draft else None}


@graph_safe
def ask_graph(question: str) -> dict:
    """Answer a graph question that no other tool covers, by generating a read-only Cypher query.
    Results are EXPLORATORY: always show the returned 'cypher' to the user."""
    return text_to_cypher(question)


@graph_safe
def run_cypher(cypher: str) -> dict:
    """Run your own read-only Cypher (Neo4j 5). Writes, admin commands and most procedures are blocked;
    max 50 rows, 10 s timeout. Call graph_schema first."""
    return run_readonly(cypher, driver())