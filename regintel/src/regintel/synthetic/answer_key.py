"""Ground truth for evals. NEVER expose data/golden/* to agents."""
from __future__ import annotations

from collections import defaultdict
from datetime import date
from typing import Any

from regintel.schemas.evidence import Evidence, EvidenceKind
from regintel.synthetic.gaps import CHANGE_DEMO, CONTRADICTIONS, PLANTED_GAPS, WITHHELD_EVIDENCE
from regintel.synthetic.spec import POLICIES, effective_evidence

TOLERANCE = 0.05


def _evidence_stats(key: str | None, stats: dict[str, dict[str, Any]]) -> dict[str, Any] | None:
    return stats.get(key[4:]) if key and key.startswith("csv:") else None


def build_answer_key(seed: int, as_of: date, stats: dict[str, dict[str, Any]],
                     manifest: list[Evidence]) -> dict[str, Any]:
    gaps_by_ctl: dict[str, list[str]] = defaultdict(list)
    for g in PLANTED_GAPS:
        if g.control_id:
            gaps_by_ctl[g.control_id].append(g.id)
    contra_ctls = {s.ref for x in CONTRADICTIONS for s in (x.side_a, x.side_b) if s.kind == "control"}

    controls = []
    for p in POLICIES:
        for c in p.controls():
            controls.append({
                "control_id": c.id, "policy_id": p.id, "clause_ref": f"{p.id} {c.clause_no}",
                "name": c.name, "expected_links": [lnk.model_dump() for lnk in c.maps_to],
                "evidence_keys": effective_evidence(c), "planted_gaps": gaps_by_ctl.get(c.id, []),
                "in_contradiction": c.id in contra_ctls, "is_clean": not gaps_by_ctl.get(c.id),
                "has_regulatory_link": bool(c.maps_to),
            })

    kyc = stats["kyc_periodic_updation"]
    change_demo = {
        **CHANGE_DEMO.model_dump(mode="json"),
        "population": kyc["high_population"], "tolerance": TOLERANCE,
        "baseline": {"overdue": kyc["high_overdue_vs_2y"], "rate": kyc["high_rate_vs_2y"],
                     "passes_tolerance": kyc["high_rate_vs_2y"] <= TOLERANCE},
        "hypothetical": {"overdue": kyc["high_overdue_vs_1y"], "rate": kyc["high_rate_vs_1y"],
                         "passes_tolerance": kyc["high_rate_vs_1y"] <= TOLERANCE},
    }

    summary = {
        "policies": len(POLICIES), "controls": len(controls),
        "clean_controls": sum(c["is_clean"] for c in controls),
        "controls_without_regulatory_link": sum(not c["has_regulatory_link"] for c in controls),
        "evidence_csv": sum(e.kind != EvidenceKind.ATTESTATION for e in manifest),
        "attestations": sum(e.kind == EvidenceKind.ATTESTATION for e in manifest),
        "withheld_evidence": len(WITHHELD_EVIDENCE), "planted_gaps": len(PLANTED_GAPS),
        "gap_types_covered": len({g.gap_type for g in PLANTED_GAPS}),
        "contradictions": len(CONTRADICTIONS),
    }

    return {
        "meta": {"bank": "Bharat Unity Bank Ltd. (synthetic)", "seed": seed,
                 "as_of": as_of.isoformat(),
                 "warning": "GROUND TRUTH - for evaluation only, never feed to agents"},
        "summary": summary,
        "planted_gaps": [{**g.model_dump(mode="json"),
                          "evidence_stats": _evidence_stats(g.evidence_key, stats)}
                         for g in PLANTED_GAPS],
        "contradictions": [x.model_dump(mode="json") for x in CONTRADICTIONS],
        "change_demo": change_demo,
        "false_positive_traps": {
            "clean_controls": [c["control_id"] for c in controls if c["is_clean"]],
            "controls_without_regulatory_link":
                [c["control_id"] for c in controls if not c["has_regulatory_link"]],
        },
        "withheld_evidence": sorted(WITHHELD_EVIDENCE),
        "controls": controls,
    }