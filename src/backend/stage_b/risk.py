"""Deterministic reviewer-priority scoring for Stage B findings."""

from __future__ import annotations

SEVERITY_PRIORITY = {
    "critical": (90, "P1"),
    "high": (70, "P2"),
    "medium": (40, "P3"),
    "low": (15, "P4"),
}

RISK_BASIS = "Severity-based reviewer priority; not a probability or validated harm estimate."


def prioritize_findings(findings: list[dict]) -> list[dict]:
    """Add a transparent priority score and sort findings highest-first."""
    prioritized = []
    for index, finding in enumerate(findings):
        item = dict(finding)
        severity = str(item.get("severity", "")).lower()
        score, rank = SEVERITY_PRIORITY.get(severity, (0, "Unranked"))
        item["risk_score"] = score
        item["risk_rank"] = rank
        item["risk_basis"] = RISK_BASIS
        prioritized.append((index, item))

    prioritized.sort(
        key=lambda entry: (
            -entry[1]["risk_score"],
            entry[1].get("category", ""),
            entry[1].get("phrase", ""),
            entry[0],
        )
    )
    return [item for _, item in prioritized]


def reference_failure_severity(entity_type: str | None) -> str:
    """A missing medication reference merits high review priority, not a critical claim."""
    return "High" if entity_type == "medication" else "Medium"
