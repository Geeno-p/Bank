# RegIntel evaluation scorecard

Synthetic bank data and paraphrased regulation excerpts. Computed without LLM calls (`python -m regintel.evals.scorecard`).

| Area | Metric | Value | Target | Status |
|---|---|---|---|---|
| Extraction | Planted controls extracted | 42/42 (100%) | ≥ 100% | ✓ |
| Extraction | Needed obligations found (keyword match) | 46/47 (98%) | ≥ 90% | ✓ |
| Extraction | Active obligations | 46 |  | – |
| Grounding | Quotes matching the source text exactly | 89/89 (100%) | ≥ 100% | ✓ |
| Mapping | Expected obligation→control links found | 38/41 (93%) | ≥ 85% | ✓ |
| Mapping | Planted coverage verdicts correct (G01, G02, G05, G06) | 3/4 (75%) | ≥ 100% | ✗ |
| Gaps | Planted gaps detected (all 6 gap types) | 9/10 (90%) | ≥ 100% | ✗ |
| Gaps | Clean controls wrongly flagged | 2 DPR-C10, KYC-C09 | 0 | ✗ |
| Gaps | Open gaps (HIGH/CRITICAL) | 14 (11) |  | – |
| Change demo | KYC-C09 overdue: 2-year rule → 1-year rule | 37/1840 (2.0%) → 312/1840 (17.0%) | pass → fail | ✓ |
| Change demo | Design gap raised after the amendment | yes | yes | ✓ |
| Remediation | HIGH/CRITICAL gaps with a plan | 1/11 (9%) | ≥ 100% | ✗ |
| Remediation | Plans passing all automatic checks | 1/1 (100%) | ≥ 90% | ✓ |
| Remediation | Plans fixed by self-revision / approved | 0 / 1 |  | – |
| Cross-regulation | Planted contradictions found (X1, X2) | n/a (6D not applied yet) | 2/2 | – |
| Text-to-Cypher | Execution accuracy | 10/11 (91%) | ≥ 75% | ✓ |
| Efficiency | Cached LLM responses (re-runs cost 0 calls) | 39 |  | – |
| Efficiency | Deterministic decisions (applicability, tests, gap rules, risk) | 91 |  | – |
| Operations | Workflow runs by status | {"COMMITTED": 9, "FAILED": 1, "REJECTED": 1} |  | – |
| Operations | Human review items by status | {"APPROVED": 6, "PENDING": 6} |  | – |
