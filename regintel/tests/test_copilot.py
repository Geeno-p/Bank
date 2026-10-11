from regintel.agents import copilot as cp
from regintel.tools.readonly import TOOLS, describe


def test_react_loop_handles_bad_tool_then_answers(monkeypatch):
    monkeypatch.setattr(cp, "TOOLS", {"get_gaps": lambda band="": [{"gap_id": "GAP-1", "band": band}]})
    monkeypatch.setattr(cp, "audit", lambda *a, **k: None)
    seq = iter([{"thought": "t", "action": "delete_everything"},
                {"thought": "t", "action": "get_gaps", "args": {"band": "HIGH"}},
                {"thought": "t", "action": "final", "answer": "GAP-1 is HIGH"}])
    prompts = []

    def fake(task, model, system, user):
        prompts.append(user)
        return model(**next(seq))

    res = cp.ask("Which high gaps?", llm=fake)
    assert res["answer"] == "GAP-1 is HIGH"
    assert [s["action"] for s in res["steps"]] == ["delete_everything", "get_gaps"]
    assert "unknown tool" in res["steps"][0]["observation"]
    assert "GAP-1" in prompts[-1]                         # tool result fed back to the LLM


def test_step_limit_forces_answer(monkeypatch):
    monkeypatch.setattr(cp, "TOOLS", {"overview": lambda: {"ok": 1}})
    monkeypatch.setattr(cp, "audit", lambda *a, **k: None)
    res = cp.ask("loop", llm=lambda t, m, s, u: m(thought="t", action="overview", answer="done"), max_steps=2)
    assert res["answer"] == "done" and len(res["steps"]) == 2


def test_describe_lists_all_tools():
    d = describe(TOOLS)
    assert all(name in d for name in TOOLS) and len(TOOLS) == 16