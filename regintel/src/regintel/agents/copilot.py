"""Compliance Copilot: ReAct-style agent. The LLM chooses which read-only tool to call next."""
from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from pydantic import BaseModel, Field

from regintel.db import audit
from regintel.llm.client import call_llm
from regintel.schemas.common import stable_id, utcnow
from regintel.tools import readonly

TOOLS = readonly.TOOLS
MAX_STEPS, OBS_CHARS = 6, 3000


class AgentStep(BaseModel):
    thought: str = Field(description="Brief reasoning about what to do next")
    action: str = Field(description="A tool name from the list, or 'final' to answer")
    args: dict[str, Any] = Field(default_factory=dict, description="Tool arguments (empty for 'final')")
    answer: str | None = Field(None, description="Markdown answer, only when action='final'")


SYSTEM = """You are the Compliance Copilot for a bank's regulatory compliance system.
Answer questions using ONLY the tools below. You decide which tool to call next, one at a time.
Tools (read-only):
{tools}
Rules:
- Use tools before answering. Never invent obligations, controls, gaps, numbers or IDs.
- Cite evidence: gap IDs, control IDs, regulation + clause refs, quotes.
- Typical paths: overview -> get_gaps -> explain_risk; search_obligations -> get_traceability.
- Graph tools (multi-hop): impact_of_clause for "what is affected if clause X changes";
  coverage_if_control_removed; shared_controls; obligation_history for before/after an amendment.
- Use ask_graph ONLY when no other tool fits. Its results are exploratory: show the user the Cypher it ran.
- You cannot change data. If the user wants an action (approve, start a run), tell them to use the
  'Human review' or 'Agent runs' page.
- If tools return nothing relevant, say so honestly.
- When you have enough, set action='final' and write a concise markdown answer (bullets welcome).
- get_impact gives the full impact report of a regulatory change (prefer it for "what is the impact" questions).
- get_remediation answers "how do we fix / close gap X" (show owners, due days and draft wording).
Respond with one step: thought, action, args, answer."""


def ask(question: str, history: list[tuple[str, str]] | None = None,
        llm: Callable = call_llm, max_steps: int = MAX_STEPS) -> dict[str, Any]:
    system = SYSTEM.format(tools=readonly.describe(TOOLS))
    convo = "".join(f"USER: {q}\nCOPILOT: {a[:500]}\n" for q, a in (history or [])[-2:])
    steps, scratch, seen, answer = [], "", set(), None
    for i in range(max_steps + 1):
        force = i == max_steps
        user = (f"{('PREVIOUS CONVERSATION:' + chr(10) + convo) if convo else ''}QUESTION: {question}\n\n"
                f"SCRATCHPAD:\n{scratch or '(empty)'}\n\n"
                + ("Step limit reached: action MUST be 'final' now." if force else "Decide the next step."))
        step = llm("copilot", AgentStep, system, user)
        if step.action == "final" or force:
            answer = step.answer or "I could not complete the analysis within the step limit."
            break
        key = (step.action, json.dumps(step.args, sort_keys=True))
        if step.action not in TOOLS:
            obs: Any = {"error": f"unknown tool '{step.action}'. Valid: {sorted(TOOLS)}"}
        elif key in seen:
            obs = {"error": "already called with these arguments; use the earlier result or answer"}
        else:
            seen.add(key)
            try:
                obs = TOOLS[step.action](**step.args)
            except (TypeError, ValueError) as e:
                obs = {"error": f"bad arguments: {e}"}
        text = json.dumps(obs, default=str)[:OBS_CHARS]
        steps.append({"thought": step.thought, "action": step.action, "args": step.args, "observation": text})
        scratch += f"[{i + 1}] thought: {step.thought}\naction: {step.action}({step.args})\nobservation: {text}\n"
    audit("agent:copilot", "ASK", "chat", stable_id("CHAT", question, utcnow().isoformat()),
          {"question": question[:300], "tools": [s["action"] for s in steps], "answer": (answer or "")[:500]})
    return {"answer": answer, "steps": steps}