"""Deterministic risk engine: an LLM may explain a score later, but never computes it."""
from __future__ import annotations

from datetime import date

from regintel.config import get_risk_weights, get_sources
from regintel.schemas.common import GapType, RiskBand
from regintel.schemas.gap import RiskFactors, RiskScore
from regintel.schemas.obligation import Obligation


def _issuer(reg_id: str) -> str:
    return {r.id: r.issuer for r in get_sources().regulations}.get(reg_id, "DEFAULT")


def score(gap_type: GapType, obl: Obligation | None, as_of: date,
          linked: int = 0, exception_rate: float = 0.0) -> RiskScore:
    w = get_risk_weights()
    topics = obl.topics if obl else []
    sev_tab, inh_tab = w["severity_by_issuer"], w["inherent_by_band"]
    sev = sev_tab.get(_issuer(obl.regulation_id) if obl else "DEFAULT", sev_tab["DEFAULT"])
    sev *= max((w["topic_severity_boost"].get(t, 1.0) for t in topics), default=1.0)
    inherent = max((inh_tab[w["inherent_by_topic"].get(t, "MEDIUM")] for t in topics), default=inh_tab["MEDIUM"])
    weakness = min(1.0, w["gap_type_weakness"][gap_type.value] + exception_rate)
    days = (obl.valid_from - as_of).days if obl and obl.valid_from else 0   # in force already -> max urgency
    urgency = next(u["factor"] for u in w["urgency"] if days <= u["max_days"])
    blast = min(w["blast_radius_cap"], 1 + 0.2 * linked)
    s = round(sev * inherent * weakness * urgency * blast, 3)
    band = next((RiskBand(b) for b, t in w["bands"].items() if s >= t), RiskBand.LOW)
    return RiskScore(
        factors=RiskFactors(severity=round(sev, 3), inherent=inherent, control_weakness=round(weakness, 3),
                            urgency=urgency, blast_radius=round(blast, 2)),
        score=s, band=band,
        explanation=f"severity {sev:.2f} x inherent {inherent:.2f} x weakness {weakness:.2f} "
                    f"x urgency {urgency:.2f} x blast {blast:.2f} = {s:.3f}")