"""Evaluation scorecard: one table for README and slides. No LLM calls.
  python -m regintel.evals.scorecard"""
from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import asdict, dataclass

from rich.console import Console
from rich.table import Table

from regintel.agents.extraction import active, load_extracted
from regintel.db import get_conn, init_db
from regintel.engines.evidence_tests import as_of_from, load_manifest, run_tests
from regintel.evals.match import best_match
from regintel.schemas.control import Control
from regintel.schemas.evidence import ControlTestResult
from regintel.schemas.gap import Gap, Remediation
from regintel.schemas.mapping import ControlMapping, ObligationCoverage
from regintel.schemas.obligation import ApplicabilityDecision, Obligation
from regintel.settings import get_settings

MARK = {True: "✓", False: "✗", None: "–"}
EXPECT = {"G01": "PARTIAL", "G05": "PARTIAL", "G02": "NONE", "G06": "NONE"}


@dataclass
class Metric:
    area: str
    metric: str
    value: str
    target: str = ""
    ok: bool | None = None          # None = informational or not available


def ratio(area: str, metric: str, a: int, b: int, target: float) -> Metric:
    return Metric(area, metric, f"{a}/{b} ({a / b:.0%})" if b else "n/a", f"≥ {target:.0%}",
                  (a / b >= target) if b else None)


def _match(by_reg: dict, reg: str, hint: str) -> Obligation | None:
    o, s = best_match(hint, by_reg.get(reg, []))
    return o if o and s >= 0.5 else None


# ---------- sections ----------
def extraction(key, obls, ctls, by_reg) -> list[Metric]:
    spec = {c["control_id"] for c in key["controls"]}
    hints = {(g["regulation_id"], g["obligation_hint"]) for g in key["planted_gaps"]}
    hints |= {(lk["regulation_id"], lk["hint"]) for c in key["controls"] for lk in c["expected_links"]}
    hints = [h for h in hints if h[0] in by_reg]
    found = sum(_match(by_reg, r, h) is not None for r, h in hints)
    return [ratio("Extraction", "Planted controls extracted", len(spec & {c.id for c in ctls}), len(spec), 1.0),
            ratio("Extraction", "Needed obligations found (keyword match)", found, len(hints), 0.9),
            Metric("Extraction", "Active obligations", str(len(obls)))]


def grounding(all_obls, ctls) -> list[Metric]:
    texts, ok, n = {}, 0, 0
    for s in [o.source for o in all_obls] + [c.source for c in ctls]:
        if s.char_start is None:
            continue
        p = get_settings().data_dir / "parsed" / s.doc_id / s.version / "text.txt"
        if p not in texts:
            texts[p] = p.read_text(encoding="utf-8") if p.exists() else None
        if texts[p] is not None:
            n += 1
            ok += texts[p][s.char_start:s.char_end] == s.quote
    return [ratio("Grounding", "Quotes matching the source text exactly", ok, n, 1.0)]


def mapping(key, maps, cov, by_reg) -> list[Metric]:
    pairs = {(m.obligation_id, m.control_id): m for m in maps}
    hits = total = 0
    for c in key["controls"]:
        for lk in c["expected_links"]:
            if o := _match(by_reg, lk["regulation_id"], lk["hint"]):
                total += 1
                hits += (o.id, c["control_id"]) in pairs
    right = 0
    for g in key["planted_gaps"]:
        if g["id"] in EXPECT and (o := _match(by_reg, g["regulation_id"], g["obligation_hint"])):
            if g["control_id"]:
                m = pairs.get((o.id, g["control_id"]))
                got = m.coverage.value if m else "NOT MAPPED"
            else:
                got = cov[o.id].coverage.value if o.id in cov else "?"
            right += got == EXPECT[g["id"]]
    return [ratio("Mapping", "Expected obligation→control links found", hits, total, 0.85),
            ratio("Mapping", "Planted coverage verdicts correct (G01, G02, G05, G06)", right, len(EXPECT), 1.0)]


def gap_metrics(key, gaps, by_reg) -> list[Metric]:
    found = 0
    for pg in key["planted_gaps"]:
        if pg["control_id"]:
            found += any(g.control_id == pg["control_id"] and g.gap_type.value == pg["gap_type"] for g in gaps)
        elif o := _match(by_reg, pg["regulation_id"], pg["obligation_hint"]):
            found += any(g.obligation_id == o.id and g.gap_type.value == "UNMAPPED" for g in gaps)
    clean = set(key["false_positive_traps"]["clean_controls"])
    fp = sorted({g.control_id for g in gaps if g.control_id in clean})
    high = sum(bool(g.risk) and g.risk.band.value in ("HIGH", "CRITICAL") for g in gaps)
    return [ratio("Gaps", "Planted gaps detected (all 6 gap types)", found, len(key["planted_gaps"]), 1.0),
            Metric("Gaps", "Clean controls wrongly flagged", f"{len(fp)} {', '.join(fp)}".strip(), "0", not fp),
            Metric("Gaps", "Open gaps (HIGH/CRITICAL)", f"{len(gaps)} ({high})")]


def change_demo(ctls, gaps) -> list[Metric]:
    man = load_manifest()
    as_of = as_of_from(man)

    def kyc_c09(overrides):
        return next((r for _, r in run_tests(ctls, man, as_of, "SCORECARD", overrides) if r.control_id == "KYC-C09"), None)

    out, base, hyp = [], kyc_c09({}), kyc_c09({"KYC-C09": 365})
    if base and hyp:
        out.append(Metric("Change demo", "KYC-C09 overdue: 2-year rule → 1-year rule",
                          f"{base.exceptions}/{base.population} ({base.exception_rate:.1%}) → "
                          f"{hyp.exceptions}/{hyp.population} ({hyp.exception_rate:.1%})",
                          "pass → fail", base.passed and not hyp.passed))
    hit = any(g.control_id == "KYC-C09" and g.gap_type.value == "DESIGN_GAP" for g in gaps)
    out.append(Metric("Change demo", "Design gap raised after the amendment", "yes" if hit else "no", "yes", hit))
    return out


def remediation(gaps) -> list[Metric]:
    plans = {r.gap_id: r for r in load_extracted("remediations", Remediation)}
    high = [g for g in gaps if g.risk and g.risk.band.value in ("HIGH", "CRITICAL")]
    got = [plans[g.id] for g in high if g.id in plans]
    return [ratio("Remediation", "HIGH/CRITICAL gaps with a plan", len(got), len(high), 1.0),
            ratio("Remediation", "Plans passing all automatic checks", sum(not r.issues for r in got), len(got), 0.9),
            Metric("Remediation", "Plans fixed by self-revision / approved",
                   f"{sum(r.revised for r in got)} / {sum(r.status == 'APPROVED' for r in got)}")]


def crossreg(key, by_reg) -> list[Metric]:
    p = get_settings().data_dir / "extracted" / "relations.jsonl"
    if not p.exists():
        return [Metric("Cross-regulation", "Planted contradictions found (X1, X2)", "n/a (6D not applied yet)", "2/2")]
    rels = [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x.strip()]
    pair = {frozenset((r["obligation_a_id"], r["obligation_b_id"])): r["relation"] for r in rels}
    found = 0
    for x in key["contradictions"]:
        if x["scope"] == "REG_VS_REG":
            ids = [getattr(_match(by_reg, s["ref"], s["hint"]), "id", None) for s in (x["side_a"], x["side_b"])]
        else:
            ids = [x["side_a"]["ref"], x["side_b"]["ref"]]
        found += None not in ids and pair.get(frozenset(ids)) in x["acceptable_relations"]
    return [ratio("Cross-regulation", "Planted contradictions found (X1, X2)", found, len(key["contradictions"]), 1.0),
            Metric("Cross-regulation", "Relationships found", str(len(rels)))]


def text2cypher() -> list[Metric]:
    p = get_settings().data_dir / "evals" / "text2cypher.json"
    if not p.exists():
        return [Metric("Text-to-Cypher", "Execution accuracy", "n/a (run scripts/eval_text2cypher.py)", "≥ 75%")]
    d = json.loads(p.read_text(encoding="utf-8"))
    return [ratio("Text-to-Cypher", "Execution accuracy", d["passed"], d["total"], 0.75)]


def efficiency(gaps) -> list[Metric]:
    init_db()
    with get_conn() as c:
        cached = c.execute("SELECT count(*) FROM llm_cache").fetchone()[0]
        runs = {r[0]: r[1] for r in c.execute("SELECT status, count(*) FROM runs GROUP BY status")}
        review = {r[0]: r[1] for r in c.execute("SELECT status, count(*) FROM review_queue GROUP BY status")}
    det = (len(load_extracted("applicability", ApplicabilityDecision)) +
           len(load_extracted("test_results", ControlTestResult)) + 2 * len(gaps))      # gap rule + risk score
    return [Metric("Efficiency", "Cached LLM responses (re-runs cost 0 calls)", str(cached)),
            Metric("Efficiency", "Deterministic decisions (applicability, tests, gap rules, risk)", str(det)),
            Metric("Operations", "Workflow runs by status", json.dumps(runs)),
            Metric("Operations", "Human review items by status", json.dumps(review))]


# ---------- compute / output ----------
def compute() -> list[Metric]:
    s = get_settings()
    key = json.loads((s.data_dir / "golden" / "answer_key.json").read_text(encoding="utf-8"))
    all_obls, ctls = load_extracted("obligations", Obligation), load_extracted("controls", Control)
    obls, gaps, maps = active(all_obls), load_extracted("gaps", Gap), load_extracted("mappings", ControlMapping)
    cov = {c.obligation_id: c for c in load_extracted("coverage", ObligationCoverage)}
    by_reg = defaultdict(list)
    for o in obls:
        by_reg[o.regulation_id].append(o)
    sections = [("Extraction", extraction, (key, obls, ctls, by_reg)), ("Grounding", grounding, (all_obls, ctls)),
                ("Mapping", mapping, (key, maps, cov, by_reg)), ("Gaps", gap_metrics, (key, gaps, by_reg)),
                ("Change demo", change_demo, (ctls, gaps)), ("Remediation", remediation, (gaps,)),
                ("Cross-regulation", crossreg, (key, by_reg)), ("Text-to-Cypher", text2cypher, ()),
                ("Efficiency", efficiency, (gaps,))]
    out = []
    for area, fn, args in sections:
        try:
            out += fn(*args)
        except Exception as e:  # noqa: BLE001 (one missing input must not hide the rest)
            out.append(Metric(area, "error", str(e)[:80]))
    return out


def to_markdown(ms: list[Metric]) -> str:
    lines = ["# RegIntel evaluation scorecard", "",
             "Synthetic bank data and paraphrased regulation excerpts. Computed without LLM calls "
             "(`python -m regintel.evals.scorecard`).", "",
             "| Area | Metric | Value | Target | Status |", "|---|---|---|---|---|"]
    lines += [f"| {m.area} | {m.metric} | {m.value} | {m.target} | {MARK[m.ok]} |" for m in ms]
    return "\n".join(lines) + "\n"


def save(ms: list[Metric]) -> None:
    s = get_settings()
    d = s.data_dir / "evals"
    d.mkdir(parents=True, exist_ok=True)
    (d / "scorecard.json").write_text(json.dumps([asdict(m) for m in ms], indent=2), encoding="utf-8")
    (s.root / "SCORECARD.md").write_text(to_markdown(ms), encoding="utf-8")


def main() -> None:
    ms = compute()
    save(ms)
    t = Table(title="RegIntel evaluation scorecard")
    for col in ("Area", "Metric", "Value", "Target", ""):
        t.add_column(col)
    for m in ms:
        colour = {True: "green", False: "red", None: "dim"}[m.ok]
        t.add_row(m.area, m.metric, m.value, m.target, f"[{colour}]{MARK[m.ok]}[/]")
    Console().print(t)
    print("written: SCORECARD.md, data/evals/scorecard.json")


if __name__ == "__main__":
    main()