"""Read-only guard for LLM- or user-written Cypher.
Layer 1: static checks. Layer 2: EXPLAIN query type must be read-only. Layer 3: READ session + timeout + row cap."""
from __future__ import annotations

import re
from itertools import islice
from typing import Any

from neo4j import READ_ACCESS, Driver, Query

MAX_ROWS, TIMEOUT_S = 50, 10.0
_STRINGS = re.compile(r"'(?:[^'\\]|\\.)*'|\"(?:[^\"\\]|\\.)*\"|`[^`]*`")
_COMMENTS = re.compile(r"//[^\n]*|/\*.*?\*/", re.S)
_DENY = re.compile(r"\b(CREATE|MERGE|DELETE|DETACH|SET|REMOVE|DROP|LOAD|FOREACH|GRANT|REVOKE|DENY|ALTER|"
                   r"RENAME|START|STOP|TERMINATE|SHOW|USE|INSERT|PROFILE|EXPLAIN)\b", re.I)
_PROC = re.compile(r"\bCALL\s+([A-Za-z_][\w.]*)\s*\(", re.I)
_ALLOWED_PROCS = {"db.labels", "db.relationshiptypes", "db.propertykeys", "db.schema.visualization",
                  "db.schema.nodetypeproperties", "db.schema.reltypeproperties"}


class GuardError(ValueError):
    """Query rejected by the read-only guard."""


def _skeleton(q: str) -> str:
    """Query with string literals and comments blanked, so keywords inside them don't count."""
    return _COMMENTS.sub(" ", _STRINGS.sub("''", q))


def validate(cypher: str) -> str:
    q = (cypher or "").strip().rstrip(";").strip()
    if not q:
        raise GuardError("empty query")
    sk = _skeleton(q)
    if ";" in sk:
        raise GuardError("only one statement is allowed")
    if m := _DENY.search(sk):
        raise GuardError(f"'{m.group(1).upper()}' is not allowed (read-only)")
    for proc in _PROC.findall(sk):
        if proc.lower() not in _ALLOWED_PROCS:
            raise GuardError(f"procedure '{proc}' is not allowed")
    if (re.search(r"\bRETURN\b", sk, re.I) and not re.search(r"\bUNION\b", sk, re.I)
            and not re.search(r"\bLIMIT\s+\d+\s*$", sk.strip(), re.I)):
        q = f"{q}\nLIMIT {MAX_ROWS}"
    return q


def run_readonly(cypher: str, drv: Driver, max_rows: int = MAX_ROWS, timeout: float = TIMEOUT_S) -> dict[str, Any]:
    q = validate(cypher)
    with drv.session(default_access_mode=READ_ACCESS) as s:
        qtype = s.run("EXPLAIN " + q).consume().query_type
        if qtype != "r":
            raise GuardError(f"query type '{qtype}' is not read-only")
        res = s.run(Query(q, timeout=timeout))
        cols = list(res.keys())
        rows = [r.data() for r in islice(res, max_rows + 1)]
        res.consume()
    return {"cypher": q, "columns": cols, "rows": rows[:max_rows], "truncated": len(rows) > max_rows}