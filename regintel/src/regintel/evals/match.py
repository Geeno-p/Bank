"""Match answer-key hints to extracted obligations by keyword overlap (evaluation only)."""
from __future__ import annotations

import re

from regintel.schemas.obligation import Obligation

STOP = {"the", "and", "for", "with", "any", "its", "their", "when", "not", "than", "after", "before",
        "from", "under", "into", "are", "least", "each", "who", "all", "shall", "such"}


def words(s: str) -> set[str]:
    return {w for w in re.findall(r"[a-z]+", s.lower()) if len(w) > 2 and w not in STOP}


def best_match(hint: str, obls: list[Obligation]) -> tuple[Obligation | None, float]:
    hw = words(hint)
    best, score = None, 0.0
    for o in obls:
        s = len(hw & words(f"{o.action} {o.source.quote}")) / max(len(hw), 1)
        if s > score:
            best, score = o, s
    return best, score