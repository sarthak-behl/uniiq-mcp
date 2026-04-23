"""
Seed the database with curated admission data from publicly known statistics.

Run this before the client when you don't want to (or can't) run the live
scraper (e.g., in CI or when Playwright isn't installed).

Usage:
    python seed_data.py              # seed only
    python seed_data.py --scrape     # seed then overwrite with live scrape
"""

import argparse
import asyncio
import os
import sys

from database.db import connect, upsert_university, upsert_requirements

# ── Curated ground-truth data (sources: Common Data Sets, official sites) ────

UNIVERSITIES = [
    {
        "name": "MIT",
        "url": "https://mitadmissions.org/apply/process/stats/",
        "acceptance_rate": 0.04,
        "avg_gpa": 3.96,
        "avg_sat": 1545,
        "avg_act": 35,
        "required_ap_classes": 5,
        "application_deadline": "2024-01-01",
        "scholarship_deadline": "2024-02-15",
        "required_essays": 5,
        "requires_interview": 0,
        "notes": "No legacy preference; research experience highly valued.",
    },
    {
        "name": "Stanford",
        "url": "https://admission.stanford.edu/apply/freshmen/profile.html",
        "acceptance_rate": 0.04,
        "avg_gpa": 3.96,
        "avg_sat": 1510,
        "avg_act": 35,
        "required_ap_classes": 5,
        "application_deadline": "2024-01-05",
        "scholarship_deadline": "2024-02-01",
        "required_essays": 4,
        "requires_interview": 0,
        "notes": "Holistic review; demonstrated intellectual vitality matters.",
    },
    {
        "name": "Harvard",
        "url": "https://college.harvard.edu/admissions/admissions-statistics",
        "acceptance_rate": 0.04,
        "avg_gpa": 3.94,
        "avg_sat": 1510,
        "avg_act": 34,
        "required_ap_classes": 5,
        "application_deadline": "2024-01-01",
        "scholarship_deadline": "2024-02-01",
        "required_essays": 6,
        "requires_interview": 1,
        "notes": "Alumni interviews offered; extracurriculars weighed heavily.",
    },
    {
        "name": "UCLA",
        "url": "https://admission.ucla.edu/apply/freshman/freshman-profile",
        "acceptance_rate": 0.09,
        "avg_gpa": 4.15,
        "avg_sat": 1415,
        "avg_act": 32,
        "required_ap_classes": 4,
        "application_deadline": "2023-11-30",
        "scholarship_deadline": "2024-03-01",
        "required_essays": 8,
        "requires_interview": 0,
        "notes": "UC GPA (weighted); Personal Insight Questions critical.",
    },
    {
        "name": "UC Berkeley",
        "url": "https://admissions.berkeley.edu/freshman-profile",
        "acceptance_rate": 0.11,
        "avg_gpa": 4.15,
        "avg_sat": 1415,
        "avg_act": 33,
        "required_ap_classes": 4,
        "application_deadline": "2023-11-30",
        "scholarship_deadline": "2024-03-01",
        "required_essays": 8,
        "requires_interview": 0,
        "notes": "STEM focus strong; research/internship experience valued.",
    },
    {
        "name": "Carnegie Mellon",
        "url": "https://admission.cmu.edu/apply/freshman/profile",
        "acceptance_rate": 0.11,
        "avg_gpa": 3.89,
        "avg_sat": 1530,
        "avg_act": 35,
        "required_ap_classes": 4,
        "application_deadline": "2024-01-01",
        "scholarship_deadline": "2024-01-01",
        "required_essays": 3,
        "requires_interview": 0,
        "notes": "Top CS program; portfolio/projects for SCS applicants.",
    },
    {
        "name": "University of Michigan",
        "url": "https://admissions.umich.edu/apply/freshman/profile",
        "acceptance_rate": 0.18,
        "avg_gpa": 3.90,
        "avg_sat": 1450,
        "avg_act": 33,
        "required_ap_classes": 3,
        "application_deadline": "2024-02-01",
        "scholarship_deadline": "2023-12-01",
        "required_essays": 2,
        "requires_interview": 0,
        "notes": "In-state vs out-of-state acceptance rates differ significantly.",
    },
]

REQUIREMENTS_MAP = {
    "MIT": [
        dict(category="academic", label="GPA", min_value=3.7, preferred_value=3.96, unit="gpa_points", is_required=1),
        dict(category="test", label="SAT", min_value=1400, preferred_value=1545, unit="sat_points", is_required=0),
        dict(category="test", label="ACT", min_value=32, preferred_value=35, unit="act_points", is_required=0),
        dict(category="academic", label="AP/IB Courses", min_value=5, preferred_value=7, unit="courses", is_required=1),
        dict(category="essay", label="Essays", min_value=5, preferred_value=5, unit="essays", is_required=1),
    ],
    "Stanford": [
        dict(category="academic", label="GPA", min_value=3.7, preferred_value=3.96, unit="gpa_points", is_required=1),
        dict(category="test", label="SAT", min_value=1400, preferred_value=1510, unit="sat_points", is_required=0),
        dict(category="test", label="ACT", min_value=32, preferred_value=35, unit="act_points", is_required=0),
        dict(category="academic", label="AP/IB Courses", min_value=5, preferred_value=7, unit="courses", is_required=1),
        dict(category="essay", label="Essays", min_value=4, preferred_value=4, unit="essays", is_required=1),
    ],
    "Harvard": [
        dict(category="academic", label="GPA", min_value=3.7, preferred_value=3.94, unit="gpa_points", is_required=1),
        dict(category="test", label="SAT", min_value=1400, preferred_value=1510, unit="sat_points", is_required=0),
        dict(category="test", label="ACT", min_value=32, preferred_value=34, unit="act_points", is_required=0),
        dict(category="academic", label="AP/IB Courses", min_value=5, preferred_value=7, unit="courses", is_required=1),
        dict(category="essay", label="Essays", min_value=6, preferred_value=6, unit="essays", is_required=1),
    ],
    "UCLA": [
        dict(category="academic", label="GPA", min_value=3.7, preferred_value=4.15, unit="gpa_points", is_required=1),
        dict(category="test", label="SAT", min_value=1300, preferred_value=1415, unit="sat_points", is_required=0),
        dict(category="test", label="ACT", min_value=29, preferred_value=32, unit="act_points", is_required=0),
        dict(category="academic", label="AP/IB Courses", min_value=4, preferred_value=6, unit="courses", is_required=1),
    ],
    "UC Berkeley": [
        dict(category="academic", label="GPA", min_value=3.7, preferred_value=4.15, unit="gpa_points", is_required=1),
        dict(category="test", label="SAT", min_value=1310, preferred_value=1415, unit="sat_points", is_required=0),
        dict(category="test", label="ACT", min_value=29, preferred_value=33, unit="act_points", is_required=0),
        dict(category="academic", label="AP/IB Courses", min_value=4, preferred_value=6, unit="courses", is_required=1),
    ],
    "Carnegie Mellon": [
        dict(category="academic", label="GPA", min_value=3.7, preferred_value=3.89, unit="gpa_points", is_required=1),
        dict(category="test", label="SAT", min_value=1430, preferred_value=1530, unit="sat_points", is_required=0),
        dict(category="test", label="ACT", min_value=33, preferred_value=35, unit="act_points", is_required=0),
        dict(category="academic", label="AP/IB Courses", min_value=4, preferred_value=6, unit="courses", is_required=1),
    ],
    "University of Michigan": [
        dict(category="academic", label="GPA", min_value=3.6, preferred_value=3.90, unit="gpa_points", is_required=1),
        dict(category="test", label="SAT", min_value=1320, preferred_value=1450, unit="sat_points", is_required=0),
        dict(category="test", label="ACT", min_value=30, preferred_value=33, unit="act_points", is_required=0),
        dict(category="academic", label="AP/IB Courses", min_value=3, preferred_value=5, unit="courses", is_required=1),
    ],
}


def seed(db_path: str):
    conn = connect(db_path)
    for uni in UNIVERSITIES:
        uni_id = upsert_university(conn, uni)
        reqs = REQUIREMENTS_MAP.get(uni["name"], [])
        if reqs:
            upsert_requirements(conn, uni_id, reqs)
        print(f"[seed] ✓ {uni['name']} (id={uni_id})")
    print(f"\n[seed] Database ready at {db_path}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--scrape", action="store_true", help="Run live scraper after seeding")
    parser.add_argument("--db", default=os.getenv("DB_PATH", "./uniiq.db"), help="Database path")
    args = parser.parse_args()

    seed(args.db)

    if args.scrape:
        print("\n[seed] Launching live scraper (requires Playwright + ANTHROPIC_API_KEY)...")
        sys.path.insert(0, os.path.dirname(__file__))
        from scraper.pipeline import run_pipeline
        asyncio.run(run_pipeline(db_path=args.db))
