# AGENTS.md: RegIntel

Agentic regulatory-compliance system (ET AI Hackathon). Traceable chain:
regulation → obligation → applicability → control → evidence → test → gap → remediation → monitoring.
All bank data is SYNTHETIC; regulation texts are paraphrased demo excerpts.

## Architecture
- Ingestion (`ingestion/`): DOCX/PDF → clause tree with exact char offsets (`data/parsed/`).
- Agents (`agents/`): extraction, applicability (rules), mapping (LLM judge + param guard), gaps (rules),
  change (diff + LLM classification), impact, remediation, copilot (ReAct over read-only tools).
- Orchestration (`graph/flow.py`): LangGraph with deterministic routing; nodes
  router → ingest → change → applicability → map_new → assess → impact → remediate → critic → hitl → commit.
  `graph/service.py` is the single entry point (CLI, scheduler, UI).
- Engines (`engines/`): pandas evidence tests, deterministic risk score.
- Knowledge graph (`kg/`): Neo4j repository, read-only Cypher guard, graph tools (fixed + text-to-Cypher).
- Tools (`tools/readonly.py`): shared by the Copilot and the MCP server (`mcp_server/`).
- UI (`ui/`, `streamlit_app.py`), monitoring (`monitoring/scheduler.py`), evals (`evals/`, `scripts/check_*.py`).

## Rules (do not break)
1. `data/golden/` is ground truth for evals only. Never read it from agents, prompts or tools.
2. Every LLM call goes through `llm/client.call_llm` (routing, cache, throttle). Models live in `config/models.yaml`.
3. LLMs never create IDs, timestamps or risk scores. Drafts are enriched by code; risk is a formula (`engines/risk.py`).
4. Every quote from an LLM is verified against the source (`guards/span_verifier.make_span`); unverified → reject or cap confidence → review.
5. Routing between agents is deterministic code. The LLM chooses tools only inside the Copilot, and those tools are read-only.
6. Neo4j is written only at `commit` (after human approval when required). Files in `data/extracted/` hold the proposed state.
7. LLM- or user-written Cypher must go through `kg/guard.run_readonly`.
8. The audit log is append-only. HIGH/CRITICAL gaps require human approval.
9. Prefer deterministic code over LLM calls; batch and cache when an LLM is needed (free-tier budget).

## Commands
- Install: `uv pip install -e ".[ml,mcp,dev]"`, then `python -c "from regintel.db import init_db; init_db()"`
- Tests (offline): `pytest -q`
- Demo flow: `python -m regintel.graph.run new-version --doc RBI-KYC-MD --version v-amended`
- UI: `streamlit run streamlit_app.py`; MCP: `python -m regintel.mcp_server.server`
- Scorecard: `python -m regintel.evals.scorecard`; text-to-Cypher eval: `python scripts/eval_text2cypher.py`
- Cloud sync: `python -m regintel.deploy export-cache` and `python -m regintel.deploy sync-kg`

## Adding things
- New read-only tool: add a typed, docstring'd function to `tools/readonly.py` and to `TOOLS`
  (the Copilot and MCP pick it up automatically).
- New flow node: wrap it with `@step(name)`, add it to `NODES` and wire the edges in `build()`; update `tests/test_flow.py`.
- New LLM task: add a route in `config/models.yaml`; use a Pydantic response model; add an offline test with a fake `llm`.