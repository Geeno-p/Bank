from regintel.evals.scorecard import Metric, ratio, to_markdown


def test_ratio_and_markdown():
    m = ratio("Gaps", "Planted gaps detected", 9, 10, 0.8)
    assert m.ok and m.value == "9/10 (90%)"
    assert ratio("X", "empty", 0, 0, 0.8).ok is None
    md = to_markdown([m, Metric("Info", "note", "n/a")])
    assert "| Gaps | Planted gaps detected | 9/10 (90%) | ≥ 80% | ✓ |" in md and "| – |" in md