"""
University Admissions MCP Server
=================================
Exposes two tools to any MCP-compatible LLM client:

  evaluate_chances  — returns a 0–1 probability score for a given student
  get_action_items  — returns a prioritised list of gaps/improvements

The scoring model is intentionally transparent so results are explainable.

Run standalone:
    python -m mcp_server.server          (uses DB_PATH env var or ./uniiq.db)
"""

import json
import math
import os
from typing import Any

from mcp.server.fastmcp import FastMCP

from database.db import connect, get_university, get_requirements, list_universities

DB_PATH = os.getenv("DB_PATH", "./uniiq.db")

mcp = FastMCP(
    "university-admissions",
    instructions=(
        "You have access to a university admissions database. "
        "Use evaluate_chances to compute admission probability and "
        "get_action_items to retrieve a student's personalised gap analysis."
    ),
)


# ─────────────────────────────────────────────
# Internal helpers
# ─────────────────────────────────────────────

def _db():
    return connect(DB_PATH)


def _parse_profile(student_json: str) -> dict[str, Any]:
    try:
        return json.loads(student_json)
    except json.JSONDecodeError as exc:
        raise ValueError(f"student_profile must be valid JSON: {exc}")


def _admission_probability(profile: dict, uni: dict) -> tuple[float, list[dict]]:
    """
    Returns (probability, breakdown_list).

    Model: each metric contributes a z-score relative to the university's
    average.  The composite z-score shifts the university's baseline
    acceptance rate via an exponential link function.

    At z=0 the student is exactly at the average → probability ≈ acceptance_rate.
    Each standard-deviation advantage roughly doubles the odds.
    """
    # Approximate population std-devs for each metric
    STD = {"gpa": 0.25, "sat": 90, "act": 3}

    breakdown = []
    z_scores = []

    if uni.get("avg_gpa") and profile.get("gpa"):
        z = (profile["gpa"] - uni["avg_gpa"]) / STD["gpa"]
        z_scores.append(z)
        breakdown.append(
            {
                "metric": "GPA",
                "student": profile["gpa"],
                "university_avg": uni["avg_gpa"],
                "z_score": round(z, 2),
            }
        )

    if uni.get("avg_sat") and profile.get("sat_score"):
        z = (profile["sat_score"] - uni["avg_sat"]) / STD["sat"]
        z_scores.append(z)
        breakdown.append(
            {
                "metric": "SAT",
                "student": profile["sat_score"],
                "university_avg": uni["avg_sat"],
                "z_score": round(z, 2),
            }
        )

    if uni.get("avg_act") and profile.get("act_score"):
        z = (profile["act_score"] - uni["avg_act"]) / STD["act"]
        z_scores.append(z)
        breakdown.append(
            {
                "metric": "ACT",
                "student": profile["act_score"],
                "university_avg": uni["avg_act"],
                "z_score": round(z, 2),
            }
        )

    if not z_scores:
        base = uni.get("acceptance_rate") or 0.10
        return round(base, 4), breakdown

    avg_z = sum(z_scores) / len(z_scores)
    base = uni.get("acceptance_rate") or 0.10

    # Exponential link: p = base × e^(k·z), clipped to [0.01, 0.99]
    # k=0.7 means a +1 SD advantage raises odds by ~2× at low base rates
    k = 0.7
    prob = base * math.exp(k * avg_z)
    prob = max(0.01, min(0.99, prob))

    return round(prob, 4), breakdown


def _gap_items(profile: dict, uni: dict, reqs: list[dict]) -> list[dict]:
    """Return a list of gap items sorted by severity (largest gap first)."""
    items = []

    for req in reqs:
        label = req["label"]
        preferred = req.get("preferred_value")
        minimum = req.get("min_value")
        unit = req.get("unit", "")
        is_required = bool(req.get("is_required", 1))

        student_val: float | None = None
        if label == "GPA":
            student_val = profile.get("gpa")
        elif label == "SAT":
            student_val = profile.get("sat_score")
        elif label == "ACT":
            student_val = profile.get("act_score")
        elif label in ("AP/IB Courses", "AP Classes"):
            student_val = profile.get("ap_classes")

        if student_val is None:
            items.append(
                {
                    "metric": label,
                    "severity": "unknown",
                    "priority": 2 if is_required else 3,
                    "message": f"{label} not provided in student profile.",
                }
            )
            continue

        gap = (preferred or minimum or 0) - student_val
        if gap <= 0:
            items.append(
                {
                    "metric": label,
                    "severity": "met",
                    "priority": 4,
                    "message": f"{label} of {student_val} meets or exceeds target ({preferred or minimum} {unit}).",
                }
            )
        else:
            severity = "critical" if is_required and gap > 0 else "recommended"
            items.append(
                {
                    "metric": label,
                    "severity": severity,
                    "priority": 1 if severity == "critical" else 2,
                    "gap": round(gap, 2),
                    "message": (
                        f"{label}: student has {student_val} {unit}, "
                        f"university prefers {preferred or minimum} {unit} "
                        f"(gap: {round(gap, 2)} {unit})."
                    ),
                }
            )

    items.sort(key=lambda x: x["priority"])
    return items


# ─────────────────────────────────────────────
# MCP Tools
# ─────────────────────────────────────────────

@mcp.tool()
def evaluate_chances(student_profile: str, university_name: str) -> str:
    """
    Evaluate a student's admission probability at a university.

    Args:
        student_profile: JSON string with keys: gpa (float), sat_score (int),
                         act_score (int), ap_classes (int), name (str).
        university_name: University name (partial match supported, e.g. "MIT").

    Returns:
        JSON with probability (0–1) and per-metric breakdown.
    """
    conn = _db()
    uni = get_university(conn, university_name)
    if not uni:
        available = list_universities(conn)
        return json.dumps(
            {
                "error": f"University '{university_name}' not found.",
                "available": available,
            }
        )

    profile = _parse_profile(student_profile)
    prob, breakdown = _admission_probability(profile, uni)

    return json.dumps(
        {
            "university": uni["name"],
            "acceptance_rate": uni.get("acceptance_rate"),
            "student_probability": prob,
            "interpretation": _interpret(prob, uni.get("acceptance_rate") or 0.10),
            "metric_breakdown": breakdown,
        },
        indent=2,
    )


@mcp.tool()
def get_action_items(student_profile: str, university_name: str) -> str:
    """
    Return prioritised action items for a student to close gaps vs. a university.

    Args:
        student_profile: JSON string (same schema as evaluate_chances).
        university_name: University name (partial match supported).

    Returns:
        JSON with ordered list of action items and their severity.
    """
    conn = _db()
    uni = get_university(conn, university_name)
    if not uni:
        available = list_universities(conn)
        return json.dumps(
            {
                "error": f"University '{university_name}' not found.",
                "available": available,
            }
        )

    profile = _parse_profile(student_profile)
    reqs = get_requirements(conn, uni["id"])

    if not reqs:
        return json.dumps(
            {
                "university": uni["name"],
                "message": "No detailed requirements in database; run scraper to populate.",
                "action_items": [],
            }
        )

    gaps = _gap_items(profile, uni, reqs)

    return json.dumps(
        {
            "university": uni["name"],
            "student_name": profile.get("name", "Student"),
            "application_deadline": uni.get("application_deadline"),
            "scholarship_deadline": uni.get("scholarship_deadline"),
            "action_items": gaps,
        },
        indent=2,
    )


@mcp.tool()
def list_universities_tool() -> str:
    """List all universities available in the database."""
    conn = _db()
    names = list_universities(conn)
    return json.dumps({"universities": names})


# ─────────────────────────────────────────────
# Utility
# ─────────────────────────────────────────────

def _interpret(prob: float, base: float) -> str:
    ratio = prob / base if base > 0 else 1.0
    if ratio >= 2.0:
        return "Strong candidate — significantly above-average profile."
    if ratio >= 1.2:
        return "Competitive candidate — slightly above average."
    if ratio >= 0.8:
        return "On-the-bubble candidate — near the statistical average."
    if ratio >= 0.5:
        return "Below-average candidate — meaningful gap to close."
    return "Reach school — profile significantly below median admitted student."


if __name__ == "__main__":
    mcp.run()
