"""Classify a subscription plan for onboarding without inspecting account state."""


_PLANS = {
    "pro": ("skills-only", "Pro is below the Max 5x equivalent needed for the loop."),
    "max 5x": ("loop", "Max 5x meets the loop's minimum capacity."),
    "max 20x": ("loop", "Max 20x exceeds the loop's minimum capacity."),
    "team premium": (
        "loop",
        "Team premium provides 6.25x Pro per session, above Max 5x's 5x.",
    ),
    "team standard": ("verify", "Team standard capacity needs verification before installation."),
    "enterprise": ("verify", "Enterprise capacity needs verification before installation."),
}


def classify(plan: str) -> tuple[str, str]:
    """Return an onboarding verdict and its reason for a plan name."""
    return _PLANS.get(
        plan.strip().casefold(),
        ("verify", "Unknown plan capacity needs verification before installation."),
    )
