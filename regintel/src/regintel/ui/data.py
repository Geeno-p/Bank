"""Cached data access for the UI (files + SQLite). Neo4j is not needed to view anything."""
from __future__ import annotations

import json
import re

import streamlit as st

from regintel.agents.extraction import load_extracted, parsed_docs
from regintel.db import get_conn, init_db
from regintel.engines.evidence_tests import load_manifest
from regintel.schemas.common import DocType
from regintel.schemas.control import Control
from regintel.schemas.evidence import ControlTestResult
from regintel.schemas.gap import Gap
from regintel.schemas.mapping import ControlMapping, ObligationCoverage
from regintel.schemas.obligation import ApplicabilityDecision, Obligation
from regintel.settings import get_settings

MODELS = {"obligations": Obligation, "controls": Control, "mappings": ControlMapping,
          "coverage": ObligationCoverage, "applicability": ApplicabilityDecision, "gaps": Gap,
          "test_results": ControlTestResult}
MARKUP = re.compile(r"\[/?[a-z ]*\]")


@st.cache_data(ttl=60, show_spinner=False)
def load(name: str) -> list[dict]:
    if name == "evidence":
        try:
            return [e.model_dump(mode="json") for e in load_manifest()]
        except FileNotFoundError:
            return []
    if name == "changes":
        d = get_settings().data_dir / "extracted" / "changes"
        return [json.loads(p.read_text(encoding="utf-8")) for p in sorted(d.glob("*.json"))] if d.exists() else []
    return [m.model_dump(mode="json") for m in load_extracted(name, MODELS[name])]


def by_id(name: str, key: str = "id") -> dict[str, dict]:
    return {x[key]: x for x in load(name)}


def active_obligations() -> list[dict]:
    return [o for o in load("obligations") if not o.get("valid_to")]


@st.cache_data(ttl=300, show_spinner=False)
def clauses() -> dict[str, dict]:
    out = {}
    for dt in (DocType.REGULATION, DocType.POLICY):
        for ver, cls, _ in parsed_docs(dt):
            for c in cls:
                out[c.id] = {"ref": c.clause_ref, "text": c.text, "doc": c.doc_id, "version": ver.version_label}
    return out


def sql(query: str, *args) -> list[dict]:
    init_db()
    with get_conn() as c:
        return [dict(r) for r in c.execute(query, args).fetchall()]


def refresh() -> None:
    st.cache_data.clear()


def plain(s: str) -> str:
    return MARKUP.sub("", s)