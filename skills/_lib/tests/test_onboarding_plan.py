"""Each declared plan has one deterministic onboarding verdict and reason."""

import importlib.util
from pathlib import Path

import pytest


MODULE_PATH = Path(__file__).resolve().parents[1] / "onboarding_plan.py"
spec = importlib.util.spec_from_file_location("onboarding_plan_under_test", MODULE_PATH)
assert spec is not None and spec.loader is not None
onboarding_plan = importlib.util.module_from_spec(spec)
spec.loader.exec_module(onboarding_plan)


@pytest.mark.parametrize(
    ("plan", "verdict", "reason_fragment"),
    [
        ("Pro", "skills-only", "below the Max 5x equivalent"),
        ("Max 5x", "loop", "meets the loop's minimum"),
        ("Max 20x", "loop", "exceeds the loop's minimum"),
        ("Team premium", "loop", "6.25x Pro per session"),
        ("Team standard", "verify", "Team standard capacity needs verification"),
        ("Enterprise", "verify", "Enterprise capacity needs verification"),
        ("unlisted plan", "verify", "Unknown plan capacity needs verification"),
    ],
    ids=["pro", "max-5x", "max-20x", "team-premium", "team-standard", "enterprise", "unknown"],
)
def test_classify_plan(plan, verdict, reason_fragment):
    actual_verdict, reason = onboarding_plan.classify(plan)
    assert actual_verdict == verdict, f"{plan}: expected {verdict}, got {actual_verdict}"
    assert reason_fragment in reason, f"{plan}: expected {reason_fragment!r} in {reason!r}"
