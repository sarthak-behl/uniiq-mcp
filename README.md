# Uniiq — University Admissions Intelligence via MCP

Autonomous pipeline that scrapes, standardises, and serves university admission
data to Claude via the Model Context Protocol.

---

## System Architecture

```
┌──────────────────────────────────────────────────────────────────────────┐
│                          UNIIQ SYSTEM ARCHITECTURE                        │
│                                                                            │
│   Part 1 — Self-Healing Scraper                                           │
│   ─────────────────────────────                                           │
│                                                                            │
│   ┌─────────────────────┐     ┌──────────────────────────────────────┐   │
│   │  University Portals │     │          Scraper Pipeline             │   │
│   │  (React / dynamic)  │────▶│                                       │   │
│   │                     │     │  ┌─────────────┐  ┌────────────────┐ │   │
│   │  • MIT              │     │  │ BrowserPool │  │  RateLimiter   │ │   │
│   │  • Stanford         │     │  │ (Playwright)│  │ (token bucket) │ │   │
│   │  • Harvard          │     │  │  stealth UA │  │  + jitter      │ │   │
│   │  • UCLA             │     │  └──────┬──────┘  └───────┬────────┘ │   │
│   │  • UC Berkeley      │     │         │                  │          │   │
│   └─────────────────────┘     │         ▼                  │          │   │
│                                │  ┌─────────────────────────────────┐ │   │
│                                │  │   LLM Extractor (Claude Haiku)  │ │   │
│                                │  │                                 │ │   │
│                                │  │  Raw page text ──▶ structured   │ │   │
│                                │  │  JSON (no CSS selectors)        │ │   │
│                                │  │  Self-heals on layout changes   │ │   │
│                                │  └───────────────┬─────────────────┘ │   │
│                                └──────────────────┼────────────────────┘   │
│                                                   │                        │
│   Part 2 — MCP Server                            ▼                        │
│   ───────────────────                 ┌─────────────────────┐             │
│                                        │     SQLite DB        │             │
│                                        │                      │             │
│                                        │  universities table  │             │
│                                        │  requirements table  │             │
│                                        └──────────┬──────────┘             │
│                                                   │                        │
│                                                   ▼                        │
│                                        ┌─────────────────────┐             │
│                                        │    MCP Server        │             │
│                                        │  (FastMCP / stdio)   │             │
│                                        │                      │             │
│                                        │  ┌─────────────────┐ │             │
│                                        │  │evaluate_chances │ │             │
│                                        │  │  z-score model  │ │             │
│                                        │  └─────────────────┘ │             │
│                                        │  ┌─────────────────┐ │             │
│                                        │  │get_action_items │ │             │
│                                        │  │  gap analysis   │ │             │
│                                        │  └─────────────────┘ │             │
│                                        └──────────┬──────────┘             │
│                                                   │ MCP stdio protocol     │
│   Part 3 — Claude Agent                          ▼                        │
│   ─────────────────────          ┌─────────────────────────┐              │
│                                   │   Claude Sonnet Agent    │              │
│                                   │  (agentic tool-use loop) │              │
│                                   │                          │              │
│                                   │  System prompt           │              │
│                                   │  Student profile (JSON)  │              │
│                                   │  ── calls tools ──▶      │              │
│                                   │  ── synthesises ──▶      │              │
│                                   └──────────┬───────────────┘              │
│                                              │                              │
│                                              ▼                              │
│                                   ┌─────────────────────────┐              │
│                                   │  Strategic Report (text) │              │
│                                   │  • University table      │              │
│                                   │  • Priority action items │              │
│                                   │  • Safety/Target/Reach   │              │
│                                   │  • 30-60-90 day plan     │              │
│                                   └─────────────────────────┘              │
└──────────────────────────────────────────────────────────────────────────┘
```

---

## MCP Sequence Diagram

```
 Student Profile          Claude Agent          MCP Server           SQLite DB
      │                        │                     │                    │
      │  JSON profile          │                     │                    │
      │───────────────────────▶│                     │                    │
      │                        │                     │                    │
      │              system_prompt + tools           │                    │
      │                        │──── Anthropic API ──│                    │
      │                        │                     │                    │
      │                        │◀── tool_use ────────│                    │
      │                        │    evaluate_chances  │                    │
      │                        │    (MIT, profile)   │                    │
      │                        │                     │                    │
      │                        │── call_tool ────────▶│                    │
      │                        │                     │── SELECT * ───────▶│
      │                        │                     │◀── university row ─│
      │                        │                     │── z-score calc     │
      │                        │◀── tool_result ─────│                    │
      │                        │    {probability:     │                    │
      │                        │     0.08, breakdown} │                    │
      │                        │                     │                    │
      │                        │◀── tool_use ────────│                    │
      │                        │    get_action_items  │                    │
      │                        │    (MIT, profile)   │                    │
      │                        │                     │                    │
      │                        │── call_tool ────────▶│                    │
      │                        │                     │── SELECT reqs ────▶│
      │                        │                     │◀── requirements ───│
      │                        │                     │── gap analysis     │
      │                        │◀── tool_result ─────│                    │
      │                        │    [{metric: GPA,   │                    │
      │                        │      gap: 0.24},…]  │                    │
      │                        │                     │                    │
      │          (repeats for each target university) │                    │
      │                        │                     │                    │
      │                        │── end_turn ─────────│                    │
      │                        │  (all data gathered) │                   │
      │                        │                     │                    │
      │◀── Strategic Report ───│                     │                    │
      │    (synthesised text)  │                     │                    │
```

---

## Quick Start

### 1. Install dependencies

```bash
pip install -r requirements.txt
playwright install chromium
```

### 2. Configure environment

```bash
cp .env.example .env
# Edit .env — set ANTHROPIC_API_KEY
```

### 3. Seed the database

```bash
python seed_data.py
# Optional: also run the live scraper
python seed_data.py --scrape
```

### 4. Test the MCP server standalone

```bash
python -m mcp_server.server
# (Ctrl-C to stop; the server speaks MCP over stdio)
```

### 5. Run the full agent demo

```bash
python client/agent.py
```

---

## Project Structure

```
uniiq/
├── scraper/
│   ├── browser.py       # Playwright pool, rate limiter, stealth
│   ├── extractor.py     # LLM-guided extraction (Claude Haiku)
│   └── pipeline.py      # Orchestrates scrape → DB
├── database/
│   ├── schema.sql       # universities + requirements tables
│   └── db.py            # SQLite wrappers
├── mcp_server/
│   └── server.py        # FastMCP server: evaluate_chances, get_action_items
├── client/
│   └── agent.py         # Claude Sonnet agentic loop
├── seed_data.py         # Populate DB with curated stats
├── requirements.txt
└── SCALE_WRITEUP.md     # Scaling to 10 000 universities
```

---

## MCP Tools Reference

### `evaluate_chances`

| Parameter | Type | Description |
|---|---|---|
| `student_profile` | JSON string | `{gpa, sat_score, act_score, ap_classes, name}` |
| `university_name` | string | Partial match (e.g. `"MIT"`) |

Returns: `{university, acceptance_rate, student_probability, interpretation, metric_breakdown}`

### `get_action_items`

| Parameter | Type | Description |
|---|---|---|
| `student_profile` | JSON string | Same schema |
| `university_name` | string | Partial match |

Returns: `{university, student_name, application_deadline, action_items[{metric, severity, gap, message}]}`

### `list_universities_tool`

No parameters. Returns all universities currently in the database.

---

## Self-Healing Scraper Design

Traditional scrapers break when a site redeigns because they rely on brittle
CSS selectors.  This scraper instead:

1. Renders the page with Playwright (handles React/Next.js/Angular)
2. Extracts `document.body.innerText` — pure semantic content
3. Sends the text to Claude Haiku with a structured extraction prompt
4. Claude locates the statistics regardless of surrounding HTML structure

The extraction prompt specifies the **fields** to find, not **where** to find
them.  When MIT moves its acceptance-rate statistic from one widget to another,
the text "3.96% acceptance rate" still appears — the LLM finds it.
