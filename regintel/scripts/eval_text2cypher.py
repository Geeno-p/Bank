"""Text-to-Cypher execution accuracy vs reference queries.  python scripts/eval_text2cypher.py
A case passes if any returned column has exactly the same set of values as the reference's first column."""
from rich.console import Console
from rich.table import Table
import json
from regintel.kg.graph_tools import driver, schema_text, text_to_cypher
from regintel.kg.guard import run_readonly
from regintel.settings import get_settings

CASES = [
    ("List controls that run on the CBS system",
     "MATCH (c:Control)-[:RUNS_ON]->(:System {id: 'CBS'}) RETURN c.id"),
    ("Which policy defines control KYC-C09?",
     "MATCH (:Control {id: 'KYC-C09'})-[:DEFINED_IN]->(p:Policy) RETURN p.id"),
    ("How many active obligations does each regulation have?",
     "MATCH (o:Obligation) WHERE o.valid_to IS NULL RETURN o.regulation_id, count(*)"),
    ("Which controls have more than one gap?",
     "MATCH (g:Gap)-[:ON_CONTROL]->(c:Control) WITH c, count(g) AS n WHERE n > 1 RETURN c.id"),
    ("Which gaps are of type MISSING_EVIDENCE?",
     "MATCH (g:Gap {gap_type: 'MISSING_EVIDENCE'}) RETURN g.id"),
    ("Which controls are only partially mapped to some obligation?",
     "MATCH (:Obligation)-[:MAPPED_TO {coverage: 'PARTIAL'}]->(c:Control) RETURN DISTINCT c.id"),
    ("Which obligations were superseded by a regulatory change?",
     "MATCH (:Obligation)-[:SUPERSEDES]->(o:Obligation) RETURN o.id"),
    ("Which roles own a control that has a HIGH or CRITICAL gap?",
     "MATCH (g:Gap)-[:ON_CONTROL]->(:Control)-[:OWNED_BY]->(r:Role) WHERE g.band IN ['HIGH','CRITICAL'] RETURN DISTINCT r.id"),
    ("Which evidence supports control ITO-C06?",
     "MATCH (:Control {id: 'ITO-C06'})-[:EVIDENCED_BY]->(e:Evidence) RETURN e.title"),
    ("Which active DPDP-2023 obligations are about consent?",
     "MATCH (o:Obligation {regulation_id: 'DPDP-2023'})-[:ABOUT]->(:Topic {id: 'CONSENT'}) WHERE o.valid_to IS NULL RETURN o.id"),
    ("Which obligations are not applicable to the bank?",
     "MATCH (o:Obligation) WHERE o.applicable = false RETURN o.id"),
    ("Which systems are used by controls whose evidence test failed?",
     "MATCH (c:Control)-[:EVIDENCED_BY]->(:Evidence)-[:TESTED_BY]->(t:TestResult), (c)-[:RUNS_ON]->(s:System) "
     "WHERE t.control_id = c.id AND t.passed = false RETURN DISTINCT s.id"),
]

con, schema = Console(), schema_text()
t = Table(title="Text-to-Cypher evaluation")
for col in ("Question", "Result", "Tries", "Generated Cypher"):
    t.add_column(col)
ok = n = 0
for q, ref in CASES:
    ref_rows = run_readonly(ref, driver())["rows"]
    if not ref_rows:
        t.add_row(q[:55], "[dim]skip (empty reference)[/]", "", "")
        continue
    n += 1
    target = {str(next(iter(r.values()))) for r in ref_rows}
    res = text_to_cypher(q, schema=schema)
    if "error" in res:
        t.add_row(q[:55], "[red]✗ error[/]", "-", res["error"][:70])
        continue
    hit = any({str(r.get(c)) for r in res["rows"]} == target for c in res["columns"])
    ok += hit
    t.add_row(q[:55], "[green]✓[/]" if hit else "[red]✗[/]", str(res["attempts"]), res["cypher"].replace("\n", " ")[:80])
con.print(t)
con.print(f"[bold]Execution accuracy:[/] {ok}/{n} = {ok / max(n, 1):.0%}")
out = get_settings().data_dir / "evals"
out.mkdir(parents=True, exist_ok=True)
(out / "text2cypher.json").write_text(json.dumps({"passed": ok, "total": n, "accuracy": round(ok / max(n, 1), 3)}),
                                      encoding="utf-8")