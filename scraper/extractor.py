"""
Structured extraction with two modes:

  LLM mode (default when OPENAI_API_KEY is set):
    Sends raw page text to GPT-4o-mini — self-healing, layout-agnostic.

  Heuristic mode (fallback when no API key):
    Regex patterns against the raw text.  Good enough to prove the
    scraper pipeline runs end-to-end without any API key.
"""

import json
import os
import re
from typing import Any


# ── Heuristic parser (no API key required) ───────────────────────────────────

def _heuristic_extract(text: str) -> dict[str, Any]:
    """
    Best-effort regex extraction from raw page text.
    Handles common phrasings found on university admissions pages.
    """
    data: dict[str, Any] = {}

    # Acceptance rate — "4.6%", "Percentage admitted  4.6%", "admit rate: 3.9%"
    for pattern in [
        r"(?:acceptance|admit(?:tance)?)\s*rate[:\s]*(\d+\.?\d*)\s*%",
        r"percentage\s+admitted[:\s\t]*(\d+\.?\d*)\s*%",
        r"(\d+\.?\d*)\s*%\s*(?:acceptance|admit(?:ted)?)",
    ]:
        m = re.search(pattern, text, re.I)
        if m:
            data["acceptance_rate"] = round(float(m.group(1)) / 100, 4)
            break

    # Average GPA
    for pattern in [
        r"(?:average\s+)?gpa[:\s\t]*(\d\.\d+)",
        r"(\d\.\d+)\s*(?:average\s+)?gpa",
    ]:
        m = re.search(pattern, text, re.I)
        if m:
            val = float(m.group(1))
            if 2.0 <= val <= 4.0:
                data["avg_gpa"] = val
                break

    # SAT combined — "SAT: 1545", range "[780, 800]" + "[740, 780]" (Math + ERW)
    # Try explicit combined score first
    m = re.search(r"sat\s+(?:total|combined|composite|score)[:\s\t]*(\d{4})", text, re.I)
    if m:
        val = int(m.group(1))
        if 800 <= val <= 1600:
            data["avg_sat"] = val
    else:
        # Try range like "1490-1580" or "1490–1580"
        m = re.search(r"sat.*?(\d{4})\s*[-–]\s*(\d{4})", text, re.I)
        if m:
            data["avg_sat"] = (int(m.group(1)) + int(m.group(2))) // 2
        else:
            # MIT format: separate Math [780,800] and ERW [740,780] — sum midpoints
            math_m = re.search(r"sat\s+math.*?\[(\d+),\s*(\d+)\]", text, re.I)
            erw_m  = re.search(r"sat\s+(?:erw|reading|verbal).*?\[(\d+),\s*(\d+)\]", text, re.I)
            if math_m and erw_m:
                math_mid = (int(math_m.group(1)) + int(math_m.group(2))) // 2
                erw_mid  = (int(erw_m.group(1))  + int(erw_m.group(2)))  // 2
                data["avg_sat"] = math_mid + erw_mid

    # ACT composite — "ACT: 35", "33-35", "[34, 36]"
    m = re.search(r"act\s+composite.*?\[(\d+),\s*(\d+)\]", text, re.I)
    if m:
        data["avg_act"] = (int(m.group(1)) + int(m.group(2))) // 2
    else:
        m = re.search(r"act.*?(\d{2})\s*[-–]\s*(\d{2})", text, re.I)
        if m:
            data["avg_act"] = (int(m.group(1)) + int(m.group(2))) // 2
        else:
            m = re.search(r"act[:\s\t]*(\d{2})", text, re.I)
            if m:
                val = int(m.group(1))
                if 1 <= val <= 36:
                    data["avg_act"] = val

    # Application deadline
    m = re.search(
        r"(?:application\s+)?deadline[:\s]+"
        r"((?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|"
        r"Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)"
        r"\s+\d{1,2}(?:,?\s*\d{4})?)",
        text, re.I,
    )
    if m:
        data["application_deadline"] = m.group(1).strip()

    data.setdefault("requires_interview", bool(re.search(r"interview", text, re.I)))

    return data


# ── LLM parser (requires OPENAI_API_KEY) ─────────────────────────────────────

_EXTRACTION_PROMPT = """\
You are a data extraction assistant.  Below is the raw text content scraped
from a university admissions webpage.  Extract the following fields and return
ONLY a valid JSON object — no explanation, no markdown fences.

Fields to extract (use null if the value is not found):
  - acceptance_rate        : float between 0 and 1  (e.g. 4% → 0.04)
  - avg_gpa                : float (e.g. 3.96)
  - avg_sat                : integer (combined score, e.g. 1545)
  - avg_act                : integer (composite, e.g. 35)
  - required_ap_classes    : integer (minimum AP/IB courses mentioned)
  - application_deadline   : string (ISO date or human-readable)
  - scholarship_deadline   : string (ISO date or human-readable)
  - required_essays        : integer
  - requires_interview     : boolean
  - notes                  : string (≤120 chars, any important caveats)

Raw page text:
---
{page_text}
---

Return ONLY the JSON object.
"""


def _llm_extract(page_text: str, university_name: str) -> dict[str, Any]:
    from openai import OpenAI
    client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])

    trimmed = page_text[:12_000]
    response = client.chat.completions.create(
        model="gpt-4o-mini",
        max_tokens=512,
        messages=[
            {
                "role": "system",
                "content": (
                    "You extract structured data from university admissions pages. "
                    "Reply only with a valid JSON object."
                ),
            },
            {"role": "user", "content": _EXTRACTION_PROMPT.format(page_text=trimmed)},
        ],
    )

    raw = response.choices[0].message.content.strip()
    raw = re.sub(r"^```(?:json)?\s*", "", raw)
    raw = re.sub(r"\s*```$", "", raw)

    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        print(f"[extractor] JSON parse error for {university_name}: {exc}\nRaw: {raw[:300]}")
        return {}


# ── Public interface ──────────────────────────────────────────────────────────

def extract_admission_data(page_text: str, university_name: str) -> dict[str, Any]:
    """
    Extract structured admission data from raw page text.
    Uses LLM if OPENAI_API_KEY is set, otherwise falls back to heuristics.
    """
    if os.getenv("OPENAI_API_KEY"):
        mode = "llm"
        raw = _llm_extract(page_text, university_name)
    else:
        mode = "heuristic"
        raw = _heuristic_extract(page_text)

    print(f"[extractor] {university_name} — using {mode} extraction")

    defaults: dict[str, Any] = {
        "acceptance_rate": None,
        "avg_gpa": None,
        "avg_sat": None,
        "avg_act": None,
        "required_ap_classes": None,
        "application_deadline": None,
        "scholarship_deadline": None,
        "required_essays": None,
        "requires_interview": False,
        "notes": None,
    }
    defaults.update({k: v for k, v in raw.items() if k in defaults})
    return defaults
