"""RegIntel Streamlit UI.   streamlit run streamlit_app.py"""
from __future__ import annotations

import json
import subprocess
import sys
from datetime import date

import pandas as pd
import streamlit as st
import os
from regintel.config import get_bank_profile, get_sources
from regintel.db import audit
from regintel.graph import service
from regintel.settings import get_settings
from regintel.ui import data as D

BANDS = ["CRITICAL", "HIGH", "MEDIUM", "LOW"]
ICON = {"CRITICAL": "🟥", "HIGH": "🟧", "MEDIUM": "🟨", "LOW": "🟩"}
COV = {"FULL": "#b6d7a8", "PARTIAL": "#ffe599", "NONE": "#ea9999"}
WIDE = {"hide_index": True, "use_container_width": True}


def flash(msg: str, kind: str = "success") -> None:
    st.session_state.setdefault("flash", []).append((kind, msg))


def esc(s: str) -> str:
    return s.replace('"', "'")


def band(g: dict) -> str:
    return g["risk"]["band"] if g.get("risk") else "LOW"


def gap_table(gaps: list[dict]) -> pd.DataFrame:
    return pd.DataFrame([{"band": f"{ICON[band(g)]} {band(g)}", "score": g["risk"]["score"], "type": g["gap_type"],
                          "control": g.get("control_id") or "-", "title": g["title"]} for g in gaps])


# ---------------- pages ----------------
def dashboard():
    st.title("Compliance dashboard")
    app, cov, gaps = D.by_id("applicability", "obligation_id"), D.by_id("coverage", "obligation_id"), D.load("gaps")
    applicable = [o for o in D.active_obligations() if app.get(o["id"], {}).get("applicable")]
    if not applicable:
        st.info("No assessed obligations yet. Run extraction → applicability → mapping → gaps.")
        return
    full = sum(cov.get(o["id"], {}).get("coverage") == "FULL" for o in applicable)
    c = st.columns(5)
    c[0].metric("Applicable obligations", len(applicable))
    c[1].metric("Fully covered", f"{full / len(applicable):.0%}")
    c[2].metric("Open gaps", len(gaps))
    c[3].metric("High / critical", sum(band(g) in ("CRITICAL", "HIGH") for g in gaps))
    c[4].metric("Runs awaiting review", len(service.paused_runs()))
    left, right = st.columns(2)
    if gaps:
        g = pd.DataFrame([{"band": band(x), "type": x["gap_type"]} for x in gaps])
        left.subheader("Gaps by type and band")
        left.bar_chart(pd.crosstab(g["type"], g["band"]).reindex(columns=[b for b in BANDS if b in set(g["band"])]),
                       horizontal=True)
    cv = pd.DataFrame([{"regulation": o["regulation_id"], "coverage": cov.get(o["id"], {}).get("coverage", "NOT ASSESSED")}
                       for o in applicable])
    right.subheader("Coverage by regulation")
    right.dataframe(pd.crosstab(cv["regulation"], cv["coverage"]), use_container_width=True)
    if gaps:
        st.subheader("Top risks")
        st.dataframe(gap_table(gaps[:10]), **WIDE)


def changes():
    st.title("Regulatory change intelligence")
    cl, events = D.clauses(), D.load("changes")
    if not events:
        st.info("No change events yet. Go to Agent runs and start a NEW_VERSION run.")
        return
    for ev in events:
        st.subheader(f"{ev['doc_id']}: {ev['from_version']} → {ev['to_version']}")
        st.caption(f"detected {ev['detected_at'][:19]}")
        for ch in ev["changes"]:
            ref = cl.get(ch.get("new_clause_id") or ch.get("old_clause_id"), {}).get("ref", "?")
            material = ch["change_type"] in ("NEW", "DELETED", "TIGHTENED", "RELAXED")
            params = "; ".join(f"{p['name']}: {p['old']} → {p['new']}" for p in ch.get("changed_parameters", []))
            with st.expander(f"{'🔴' if material else '⚪'} {ref} · {ch['change_type']} · {params or ch['summary'][:80]}",
                             expanded=material):
                st.write(ch["summary"])
                a, b = st.columns(2)
                a.markdown("**Before**")
                a.info(cl.get(ch.get("old_clause_id"), {}).get("text", "—"))
                b.markdown("**After**")
                b.success(cl.get(ch.get("new_clause_id"), {}).get("text", "—"))


def trace_dot(o, maps, ctl, ev, tests, gaps) -> str:
    L = ['digraph{rankdir=LR; node[shape=box,style="rounded,filled",fontname=Helvetica,fontsize=10]']
    node = lambda i, label, col: L.append(f'"{i}"[label="{esc(label)}",fillcolor="{col}"]')  # noqa: E731
    edge = lambda a, b: L.append(f'"{a}"->"{b}"')  # noqa: E731
    reg = f"{o['regulation_id']} {o['source']['clause_ref']}"
    node(reg, reg, "#cfe2f3")
    node(o["id"], "Obligation\\n" + o["action"][:40], "#d9d2e9")
    edge(reg, o["id"])
    for m in maps:
        c = m["control_id"]
        node(c, f"{c}\\n{m['coverage']}", COV[m["coverage"]])
        edge(o["id"], c)
        for x in ev:
            if c in x["control_ids"]:
                node(x["id"], x["title"][:30], "#eeeeee")
                edge(c, x["id"])
        for t in tests:
            if t["control_id"] == c:
                node(t["id"], f"Test {'PASS' if t['passed'] else 'FAIL'}\\n{t['exceptions']}/{t['population']}",
                     "#b6d7a8" if t["passed"] else "#ea9999")
                edge(t["evidence_id"], t["id"])
    for g in gaps:
        node(g["id"], f"{g['gap_type']}\\n{band(g)}", "#f4cccc")
        edge(g.get("control_id") or o["id"], g["id"])
    return "\n".join(L + ["}"])


def traceability():
    st.title("Traceability")
    st.caption("Regulation → obligation → control → evidence → test → gap")
    obls = {o["id"]: o for o in D.active_obligations()}
    if not obls:
        st.info("No obligations extracted yet.")
        return
    reg = st.selectbox("Regulation", sorted({o["regulation_id"] for o in obls.values()}))
    ids = sorted([i for i, o in obls.items() if o["regulation_id"] == reg], key=lambda i: obls[i]["source"]["clause_ref"])
    o = obls[st.selectbox("Obligation", ids, format_func=lambda i: f"{obls[i]['source']['clause_ref']} · {obls[i]['action'][:90]}")]
    app = D.by_id("applicability", "obligation_id").get(o["id"], {})
    cov = D.by_id("coverage", "obligation_id").get(o["id"], {})
    maps = [m for m in D.load("mappings") if m["obligation_id"] == o["id"]]
    ctl, cids = D.by_id("controls"), {m["control_id"] for m in maps}
    gaps = [g for g in D.load("gaps") if g["obligation_id"] == o["id"] or g.get("control_id") in cids]
    a, b = st.columns([3, 2])
    with a:
        st.markdown(f"**{o['actor']} {o['modality']}** {o['action']}")
        st.info(f"“{o['source']['quote']}” · {o['regulation_id']} {o['source']['clause_ref']}")
        if o.get("supersedes_id"):
            st.caption(f"Supersedes {o['supersedes_id']} (regulatory change)")
        st.write(f"Applicability: {'✅' if app.get('applicable') else '❌'} {app.get('reason', 'not assessed')}")
        st.write(f"Coverage: **{cov.get('coverage', 'NOT ASSESSED')}**")
        for m in maps:
            with st.expander(f"{m['control_id']} · {m['coverage']} · {ctl.get(m['control_id'], {}).get('name', '')}"):
                st.write(m["rationale"])
                if m["missing_elements"]:
                    st.warning("Missing: " + "; ".join(m["missing_elements"]))
                st.caption(f"Control text: “{m['control_span']['quote']}”")
        for g in gaps:
            st.error(f"{ICON[band(g)]} **{g['gap_type']}** · {g['title']}\n\n{g['description']}")
    with b:
        st.graphviz_chart(trace_dot(o, maps, ctl, D.load("evidence"), D.load("test_results"), gaps))


def gaps_page():
    st.title("Gaps (risk-prioritised)")
    gaps = D.load("gaps")
    if not gaps:
        st.info("No gaps yet. Run: python -m regintel.agents.gaps")
        return
    a, b = st.columns(2)
    bands = a.multiselect("Band", BANDS, default=[x for x in BANDS if any(band(g) == x for g in gaps)])
    types = b.multiselect("Type", sorted({g["gap_type"] for g in gaps}))
    sel = [g for g in gaps if band(g) in bands and (not types or g["gap_type"] in types)]
    if not sel:
        return
    st.dataframe(gap_table(sel), **WIDE)
    by = {g["id"]: g for g in sel}
    g = by[st.selectbox("Details", list(by), format_func=lambda i: f"{band(by[i])} · {by[i]['title']}")]
    f = g["risk"]["factors"]
    cols = st.columns(6)
    for col, (k, v) in zip(cols, [("score", g["risk"]["score"]), *f.items()]):
        col.metric(k.replace("_", " "), f"{v:.2f}")
    st.write(g["description"])
    st.caption(g["risk"]["explanation"])
    o = D.by_id("obligations").get(g["obligation_id"])
    if o:
        st.info(f"Obligation · {o['regulation_id']} {o['source']['clause_ref']}: “{o['source']['quote']}”")


def review():
    st.title("Human-in-the-loop review")
    st.session_state.setdefault("reviewer", "compliance.officer")
    reviewer = st.text_input("Reviewer", key="reviewer")
    paused = [(r, s) for r in service.paused_runs() if (s := service.snapshot(r["run_id"]))["status"] == "PAUSED"]
    st.subheader(f"Runs paused for approval ({len(paused)})")
    if not paused:
        st.success("Nothing waiting.")
    for r, snap in paused:
        with st.container(border=True):
            st.markdown(f"**{r['run_id']}** · {r['event_type']} · started {r['started_at'][:19]}")
            for it in snap["review"].get("review", []):
                st.warning(f"{ICON.get(it['band'], '')} **{it['band']}** {it['title']}\n\n{it['detail']}")
            for i in snap["review"].get("issues", []):
                st.error(i)
            notes = st.text_input("Notes", key=f"n-{r['run_id']}")
            c1, c2 = st.columns(2)
            for col, action in ((c1, "approve"), (c2, "reject")):
                if col.button(action.title(), key=f"{action}-{r['run_id']}", use_container_width=True,
                              type="primary" if action == "approve" else "secondary"):
                    with st.spinner("Resuming workflow..."):
                        res = service.resume(r["run_id"], action, reviewer, notes)
                    D.refresh()
                    flash(f"{r['run_id']} → {res['status']}")
                    st.rerun()
    items = D.sql("SELECT id, item_type, item_id, reason, confidence, created_at FROM review_queue "
                  "WHERE status='PENDING' AND item_type != 'gap' ORDER BY created_at DESC")
    st.subheader(f"Low-confidence items ({len(items)})")
    if not items:
        return
    st.dataframe(pd.DataFrame(items), **WIDE)
    by = {i["id"]: i for i in items}
    pick = st.selectbox("Item", list(by), format_func=lambda i: f"{by[i]['item_type']} · {by[i]['item_id']}")
    it = by[pick]
    st.json(D.by_id("obligations").get(it["item_id"]) or D.by_id("mappings").get(it["item_id"])
            or D.by_id("applicability", "obligation_id").get(it["item_id"]) or {}, expanded=False)
    c1, c2 = st.columns(2)
    for col, status in ((c1, "APPROVED"), (c2, "REJECTED")):
        if col.button(status.title(), key=f"item-{status}", use_container_width=True):
            D.sql("UPDATE review_queue SET status=?, reviewer=? WHERE id=?", status, reviewer, pick)
            audit(f"human:{reviewer}", status, "review_item", pick, {"item_id": it["item_id"]})
            flash(f"{it['item_type']} {it['item_id']} → {status}")
            st.rerun()


@st.cache_resource
def _edges():
    from regintel.graph.flow import build
    return [(e.source, e.target, e.conditional) for e in build().get_graph().edges]


def flow_dot(visited: set[str], status: str | None) -> str:
    L = ['digraph{node[shape=box,style="rounded,filled",fontname=Helvetica,fontsize=10]']
    for a, b, cond in _edges():
        L.append(f'"{a}"->"{b}"' + ("[style=dashed]" if cond else ""))
    for n in {x for e in _edges() for x in e[:2]}:
        col = "#f6b26b" if n == "hitl" and status == "PAUSED" else "#93c47d" if n in visited else "#eeeeee"
        L.append(f'"{n}"[label="{n.strip("_")}",fillcolor="{col}"]')
    return "\n".join(L + ["}"])


def runs_page():
    st.title("Agent runs")
    targets = [(r.id, v.label) for r in get_sources().regulations
               for v in sorted(r.versions, key=lambda v: v.effective_date or date.min)[1:]]
    with st.form("start"):
        kind = st.radio("Event", ["NEW_VERSION", "EVIDENCE_UPLOADED", "SCHEDULED_TEST"], horizontal=True)
        target = st.selectbox("Regulation version (NEW_VERSION only)", targets,
                              format_func=lambda x: f"{x[0]} / {x[1]}") if targets else None
        auto = st.checkbox("Auto-approve (skip human review)")
        if st.form_submit_button("Start run", type="primary"):
            if kind == "NEW_VERSION" and not target:
                st.error("No amended regulation version in sources.yaml")
            else:
                payload = {"doc_id": target[0], "version": target[1]} if kind == "NEW_VERSION" else {}
                with st.spinner("Agents working... (free-tier LLM calls are throttled)"):
                    res = service.start(kind, payload, auto, no_kg=not st.session_state.get("use_kg", True))
                D.refresh()
                st.session_state["run"] = res["run_id"]
                flash(f"{res['run_id']} → {res['status']}")
                st.rerun()
    runs = service.recent_runs(30)
    if not runs:
        return
    st.dataframe(pd.DataFrame(runs), **WIDE)
    ids = [r["run_id"] for r in runs]
    cur = st.session_state.get("run")
    rid = st.selectbox("Inspect run", ids, index=ids.index(cur) if cur in ids else 0)
    snap = service.snapshot(rid)
    trace = snap["trace"]
    if not trace:                                 # runs without a checkpoint: use the stored summary
        row = D.sql("SELECT summary FROM runs WHERE run_id=?", rid)
        trace = json.loads(row[0]["summary"] or "{}").get("trace", []) if row else []
    a, b = st.columns([2, 3])
    a.graphviz_chart(flow_dot({t["node"] for t in trace}, snap["status"]))
    b.caption(f"Status: **{snap['status']}**")
    b.dataframe(pd.DataFrame(trace), **WIDE)


def monitoring():
    from regintel.monitoring import scheduler as sch
    st.title("Continuous monitoring")
    st.caption("Background mode: python -m regintel.monitoring.scheduler")
    for col, name in zip(st.columns(len(sch.JOBS)), sch.JOBS):
        if col.button(name.replace("_", " "), use_container_width=True):
            with st.spinner(f"Running {name}..."):
                lines = sch.JOBS[name]()
            D.refresh()
            flash("\n\n".join(D.plain(x) for x in lines) or f"{name}: nothing to do", "info")
            st.rerun()
    st.subheader("Simulate evidence changes (demo)")
    for col, act in zip(st.columns(3), ["fix-vendor-access", "late-str", "reset"]):
        if col.button(act, use_container_width=True):
            out = subprocess.run([sys.executable, "scripts/simulate_evidence.py", act], cwd=get_settings().root,
                                 capture_output=True, text=True,
                                 env={**os.environ, "PYTHONPATH": str(get_settings().root / "src")})
            flash(out.stdout.strip() or out.stderr.strip()[-300:], "info" if out.returncode == 0 else "error")
            st.rerun()
    k = D.sql("SELECT ts, kri_id, value, threshold, status FROM kri_snapshots ORDER BY id")
    if k:
        df = pd.DataFrame(k)
        latest = df.groupby("kri_id").tail(1).copy()
        latest["name"] = latest["kri_id"].map({x["id"]: x["name"] for x in sch.cfg()["kris"]})
        st.subheader("Key risk indicators")
        st.dataframe(latest[["kri_id", "name", "value", "threshold", "status"]], **WIDE)
        st.line_chart(df.pivot_table(index="ts", columns="kri_id", values="value"))
    log = D.sql("SELECT timestamp, actor, action, entity_id FROM audit_log WHERE actor LIKE 'monitor:%' "
                "ORDER BY id DESC LIMIT 25")
    if log:
        st.subheader("Monitoring log")
        st.dataframe(pd.DataFrame(log), **WIDE)


def phase6():
    st.title("Remediation · Impact · Cross-regulation")
    st.info("Coming in Phase 6: remediation recommendations per gap, autonomous impact analysis, "
            "cross-regulation overlaps and contradiction detection (KYC retention vs DPDP erasure).")

def copilot_page():
    from regintel.agents.copilot import ask
    st.title("Compliance Copilot")
    st.caption("An LLM agent that chooses its own read-only tools. It cannot change data.")
    chat = st.session_state.setdefault("chat", [])
    examples = ["What are my highest-risk gaps and why?",
                "Trace the high-risk periodic KYC updation obligation",
                "What changed in the latest KYC regulation version and what is the impact?"]
    for col, q in zip(st.columns(3), examples):
        if col.button(q, use_container_width=True):
            st.session_state["pending_q"] = q
    if chat and st.button("Clear chat"):
        st.session_state["chat"] = []
        st.rerun()
    for m in chat:
        with st.chat_message(m["role"]):
            st.markdown(m["content"])
            if m.get("steps"):
                with st.expander(f"🔧 {len(m['steps'])} tool call(s): agent reasoning"):
                    for n, s in enumerate(m["steps"], 1):
                        st.markdown(f"**{n}. `{s['action']}`** `{json.dumps(s['args'])}`  \n_{s['thought']}_")
                        st.code(s["observation"][:800], language="json")
    q = st.chat_input("Ask about obligations, controls, gaps, changes, runs or KRIs...") \
        or st.session_state.pop("pending_q", None)
    if q:
        hist = [(chat[i]["content"], chat[i + 1]["content"]) for i in range(0, len(chat) - 1, 2)]
        chat.append({"role": "user", "content": q})
        with st.spinner("Copilot is choosing tools... (free-tier calls are throttled)"):
            try:
                res = ask(q, history=hist)
                chat.append({"role": "assistant", "content": res["answer"], "steps": res["steps"]})
            except Exception as e:  # noqa: BLE001
                chat.append({"role": "assistant", "content": f"⚠️ Copilot error: {e}"})
        st.rerun()


def main() -> None:
    st.set_page_config(page_title="RegIntel", page_icon="⚖️", layout="wide")
    with st.sidebar:
        st.markdown(f"### ⚖️ RegIntel\n{get_bank_profile().name}")
        st.toggle("Write results to Neo4j", value=get_settings().kg_write_default, key="use_kg")
        if st.button("Refresh data", use_container_width=True):
            D.refresh()
            st.rerun()
        st.caption("Synthetic bank data and paraphrased regulation excerpts. Demo only.")
    for kind, msg in st.session_state.pop("flash", []):
        getattr(st, kind)(msg)
    st.navigation([
        st.Page(dashboard, title="Dashboard", icon="📊", default=True),
        st.Page(changes, title="Regulatory changes", icon="🔄"),
        st.Page(traceability, title="Traceability", icon="🧭"),
        st.Page(gaps_page, title="Gaps", icon="🚨"),
        st.Page(review, title="Human review", icon="✅"),
        st.Page(runs_page, title="Agent runs", icon="🤖"),
        st.Page(monitoring, title="Monitoring", icon="📡"),
        st.Page(phase6, title="Remediation & impact", icon="🧩"),
        st.Page(copilot_page, title="Copilot", icon="💬"),
    ]).run()