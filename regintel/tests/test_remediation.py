from datetime import date

from regintel.agents import remediation as rem
from regintel.schemas import Gap, Parameter
from regintel.schemas.gap import RemediationDraft, RiskFactors, RiskScore
from test_phase3b import P, ctl, obl

CFG = {"sla_days": {"CRITICAL": 30, "HIGH": 60, "MEDIUM": 90, "LOW": 180},
       "patterns": {"DESIGN_GAP": {"required": ["POLICY_UPDATE"],
                                   "allowed": ["POLICY_UPDATE", "PROCESS_CHANGE"], "guidance": "g"}}}
RISK = RiskScore(factors=RiskFactors(severity=1, inherent=1, control_weakness=0.8, urgency=1, blast_radius=1),
                 score=0.6, band="HIGH")


def _setup():
    o = obl("O1", "update high risk KYC every year", [Parameter(name="periodicity", value="1", unit="year")])
    o = o.model_copy(update={"valid_from": date(2025, 7, 1)})
    g = Gap(id="GAP-1", gap_type="DESIGN_GAP", obligation_id="O1", control_id="C1", title="t", description="d",
            risk=RISK, provenance=P)
    return g, rem.Ctx({"O1": o}, {"C1": ctl("C1", "re-KYC high risk")}, ["Head of KYC Operations"],
                      date(2025, 3, 31), CFG)


def _plan(**kw):
    a = {"action_type": "POLICY_UPDATE", "description": "amend policy", "owner_role": "Head of KYC Operations",
         "effort": "S", "due_in_days": 30,
         "draft_text": "Periodic updation shall be carried out at least once every year for High risk customers."}
    return {"actions": [a | kw], "rationale": "r", "expected_residual_band": "LOW"}


def test_checks_pass_and_fail():
    g, ctx = _setup()
    o = ctx.obligations["O1"]
    assert rem.check(RemediationDraft(**_plan()), g, o, ctx) == []
    bad = RemediationDraft(**_plan(owner_role="Wizard", due_in_days=200, draft_text="once every two years"))
    issues = " | ".join(rem.check(bad, g, o, ctx))
    assert "not a known role" in issues and "limit of 92" in issues and "regulatory period" in issues
    wrong = RemediationDraft(**_plan(action_type="TRAINING", draft_text=None))
    assert any("must include" in i for i in rem.check(wrong, g, o, ctx))


def test_draft_one_revises_once():
    g, ctx = _setup()
    drafts = iter([_plan(draft_text="once every two years"), _plan()])
    prompts = []

    def fake(task, model, system, user):
        prompts.append(user)
        return model(**next(drafts))

    r = rem.draft_one(g, ctx, "RUN-T", llm=fake)
    assert r.revised and not r.issues and r.status == "PROPOSED" and len(prompts) == 2
    assert "failed these checks" in prompts[1]