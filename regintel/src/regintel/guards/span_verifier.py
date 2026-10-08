"""Ground LLM quotes in source text. Returns EXACT source offsets, or None (= reject)."""
from __future__ import annotations

from regintel.schemas.common import SourceSpan
from regintel.schemas.regulation import Clause

_MAP = {"\u2018": "'", "\u2019": "'", "\u201c": '"', "\u201d": '"', "\u2013": "-", "\u2014": "-"}
_TRIM = " .;:,'\"\u2026"
MIN_LEN = 12  # very short quotes are too ambiguous to ground


def _normalise(s: str) -> tuple[str, list[int]]:
    """Lower-case, unify quotes/dashes, collapse whitespace. idx[k] = original index of out[k]."""
    out: list[str] = []
    idx: list[int] = []
    for i, ch in enumerate(s):
        ch = _MAP.get(ch, ch)
        if ch.isspace():
            if not out or out[-1] == " ":
                continue
            ch = " "
        else:
            low = ch.lower()
            ch = low if len(low) == 1 else ch
        out.append(ch)
        idx.append(i)
    if out and out[-1] == " ":
        out.pop()
        idx.pop()
    return "".join(out), idx


def find_span(quote: str, text: str, lo: int = 0, hi: int | None = None) -> tuple[int, int] | None:
    q = _normalise(quote)[0].strip(_TRIM)
    if len(q) < MIN_LEN:
        return None
    t, idx = _normalise(text[lo:len(text) if hi is None else hi])
    pos = t.find(q)
    if pos < 0:
        return None
    return lo + idx[pos], lo + idx[pos + len(q) - 1] + 1


def make_span(quote: str, clause: Clause, full_text: str, version_label: str) -> SourceSpan | None:
    """Quote must lie inside the clause. The stored quote is the SOURCE text, never the LLM's wording."""
    hit = find_span(quote, full_text, clause.char_start or 0, clause.char_end)
    if hit is None:
        return None
    s, e = hit
    return SourceSpan(doc_id=clause.doc_id, version=version_label, clause_ref=clause.clause_ref,
                      quote=full_text[s:e], char_start=s, char_end=e, page=clause.page_start)