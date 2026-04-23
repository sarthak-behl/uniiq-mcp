"""
Scraping pipeline — ties together BrowserPool, RateLimiter, and LLM extractor.

Target universities and their admission-stats URLs are declared in TARGETS.
Add any new school by appending to that list; zero code changes required.
"""

import asyncio
import os
from dataclasses import dataclass

from scraper.browser import BrowserPool, RateLimiter, fetch_page_text
from scraper.extractor import extract_admission_data
from database.db import connect, upsert_university, upsert_requirements


@dataclass
class ScrapeTarget:
    name: str
    url: str
    # Optional CSS selector that signals the page is fully hydrated.
    # None = fall back to networkidle + sleep heuristic.
    ready_selector: str | None = None


TARGETS: list[ScrapeTarget] = [
    ScrapeTarget(
        name="MIT",
        url="https://mitadmissions.org/apply/process/stats/",
        ready_selector=None,
    ),
    ScrapeTarget(
        name="Stanford",
        url="https://admission.stanford.edu/apply/freshmen/profile.html",
        ready_selector=None,
    ),
    ScrapeTarget(
        name="Harvard",
        url="https://college.harvard.edu/admissions/admissions-statistics",
        ready_selector=None,
    ),
    ScrapeTarget(
        name="UCLA",
        url="https://admission.ucla.edu/apply/freshman/freshman-profile",
        ready_selector=None,
    ),
    ScrapeTarget(
        name="UC Berkeley",
        url="https://admissions.berkeley.edu/freshman-profile",
        ready_selector=None,
    ),
]


async def scrape_target(
    target: ScrapeTarget,
    pool: BrowserPool,
    limiter: RateLimiter,
    db_path: str,
) -> dict:
    print(f"[pipeline] Scraping {target.name} → {target.url}")
    try:
        page_text = await fetch_page_text(
            pool, limiter, target.url, wait_selector=target.ready_selector
        )
        data = extract_admission_data(page_text, target.name)
        data["name"] = target.name
        data["url"] = target.url

        conn = connect(db_path)
        uni_id = upsert_university(conn, data)

        # Build granular requirements rows from the extracted values
        reqs = _build_requirements(data)
        if reqs:
            upsert_requirements(conn, uni_id, reqs)

        print(f"[pipeline] ✓ {target.name} saved (acceptance_rate={data.get('acceptance_rate')})")
        return data

    except Exception as exc:
        print(f"[pipeline] ✗ {target.name} failed: {exc}")
        return {"name": target.name, "error": str(exc)}


def _build_requirements(data: dict) -> list[dict]:
    """Convert flat extraction dict into normalised requirement rows."""
    reqs = []
    if data.get("avg_gpa"):
        reqs.append(
            dict(
                category="academic",
                label="GPA",
                min_value=data["avg_gpa"] - 0.3,
                preferred_value=data["avg_gpa"],
                unit="gpa_points",
                is_required=1,
            )
        )
    if data.get("avg_sat"):
        reqs.append(
            dict(
                category="test",
                label="SAT",
                min_value=data["avg_sat"] - 150,
                preferred_value=data["avg_sat"],
                unit="sat_points",
                is_required=0,
            )
        )
    if data.get("avg_act"):
        reqs.append(
            dict(
                category="test",
                label="ACT",
                min_value=data["avg_act"] - 3,
                preferred_value=data["avg_act"],
                unit="act_points",
                is_required=0,
            )
        )
    if data.get("required_ap_classes"):
        reqs.append(
            dict(
                category="academic",
                label="AP/IB Courses",
                min_value=data["required_ap_classes"],
                preferred_value=data["required_ap_classes"] + 2,
                unit="courses",
                is_required=1,
            )
        )
    return reqs


async def run_pipeline(
    targets: list[ScrapeTarget] | None = None,
    db_path: str | None = None,
    pool_size: int = 2,
    rpm: int = 8,
) -> list[dict]:
    targets = targets or TARGETS
    db_path = db_path or os.getenv("DB_PATH", "./uniiq.db")

    pool = BrowserPool(pool_size=pool_size)
    limiter = RateLimiter(requests_per_minute=rpm)

    await pool.start()
    try:
        tasks = [scrape_target(t, pool, limiter, db_path) for t in targets]
        results = await asyncio.gather(*tasks, return_exceptions=False)
    finally:
        await pool.stop()

    return results


if __name__ == "__main__":
    asyncio.run(run_pipeline())
