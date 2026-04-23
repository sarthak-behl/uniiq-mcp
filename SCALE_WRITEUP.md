# Scaling to 10,000 Universities — Architecture & Cost Analysis

## The Core Challenge

At 10,000 universities the naive "scrape everything daily" approach fails on
three axes simultaneously: **latency** (can't finish in one cycle), **cost**
(Playwright + LLM calls per page are expensive), and **freshness**
(admission deadlines change on irregular schedules, not daily).

The solution is a **tiered, event-driven refresh system** that matches scrape
frequency to the volatility and value of each data source.

---

## Tier 1 — Static Reference Data (Cold)

**What:** Program names, contact details, accreditation status.  Changes once
per year at most.

**Strategy:** Scrape once on ingestion; refresh on a 90-day cron.
Use `httpx` (not Playwright) for pages that serve this data as plain HTML —
roughly 60 % of university sites at the departmental level.

**Cost driver:** LLM extraction tokens.  At ~2 000 tokens per page and
$0.25 / M tokens (Haiku), 10 000 pages = **$5 per full refresh cycle**.

---

## Tier 2 — Admissions Stats (Warm)

**What:** Acceptance rates, GPA/test medians, class profiles.  Published
annually after each admissions cycle (typically March–May).

**Strategy:** Change-detection before extraction.
1. Store the `ETag` / `Last-Modified` header from the prior scrape.
2. Issue a `HEAD` request (no LLM call, no Playwright spin-up).
3. Only invoke the full pipeline when the header changes.

Expected hit rate: < 5 % of pages change on any given week outside March–May.
This cuts LLM calls by **~95 %** in steady state.

**Cost driver:** Playwright instances.  Use an auto-scaling container pool
(AWS ECS / GCP Cloud Run).  Spin up on demand, scale to zero overnight.
Peak-season (March) budget: ~$120 / day for concurrent scraping at 50 RPS.

---

## Tier 3 — Deadline & Scholarship Data (Hot)

**What:** Application deadlines, scholarship deadlines, financial aid dates.
These can change with 2–3 weeks notice and have high business value.

**Strategy:** Combination of:
- **RSS / structured data feeds** where available (many universities publish
  iCal feeds for academic deadlines).
- **Webhook triggers** from partner data providers (e.g. Common App publishes
  deadline changes via an API).
- **Weekly scrape** of the 500 highest-traffic universities regardless of
  change detection, because missed deadlines are the highest-severity failure.

---

## Distributed Architecture

```
                   ┌─────────────────────────────────────┐
                   │         Scheduler Service            │
                   │  (AWS EventBridge / Celery Beat)     │
                   │                                      │
                   │  Tier 1: 90-day cron                 │
                   │  Tier 2: Weekly HEAD-check + diff    │
                   │  Tier 3: Daily + event triggers      │
                   └──────────────┬──────────────────────┘
                                  │  Job queue (SQS / Redis)
                                  ▼
                   ┌─────────────────────────────────────┐
                   │         Scraper Workers              │
                   │  (auto-scaling containers)           │
                   │                                      │
                   │  • Playwright for dynamic pages      │
                   │  • httpx for static HTML             │
                   │  • Rate-limiter per domain           │
                   │  • Retry + dead-letter queue         │
                   └──────────────┬──────────────────────┘
                                  │  Raw page text
                                  ▼
                   ┌─────────────────────────────────────┐
                   │     LLM Extraction Service           │
                   │  (Anthropic Batch API)               │
                   │                                      │
                   │  • Batches up to 10 000 prompts      │
                   │  • 50 % cost discount vs real-time   │
                   │  • Async result webhook              │
                   └──────────────┬──────────────────────┘
                                  │  Structured JSON
                                  ▼
                   ┌─────────────────────────────────────┐
                   │         Data Store                   │
                   │                                      │
                   │  PostgreSQL (write)  ◀──────────────▶│
                   │  + pgvector for semantic search      │
                   │                                      │
                   │  Redis (cache)                       │
                   │  • MCP tool responses: 1-hour TTL    │
                   │  • evaluate_chances results          │
                   └──────────────┬──────────────────────┘
                                  │
                                  ▼
                   ┌─────────────────────────────────────┐
                   │         MCP Server Fleet             │
                   │  (horizontally scaled)               │
                   │                                      │
                   │  • evaluate_chances                  │
                   │  • get_action_items                  │
                   │  • search_universities (semantic)    │
                   └─────────────────────────────────────┘
```

---

## Cost Model at Scale

| Component | Unit Cost | At 10K universities | At 1M queries/month |
|---|---|---|---|
| Playwright scrape | $0.003 / page | $30 / full cycle | — |
| Haiku extraction | $0.0005 / page | $5 / full cycle | — |
| HEAD change-check | $0.00001 / check | $0.70 / week | — |
| evaluate_chances | ~0 (pure SQL) | — | $0 |
| MCP server (Cloud Run) | $0.00002 / request | — | $20 |
| PostgreSQL (db.r6g.large) | $0.26 / hr | $190 / month | $190 / month |
| Redis cache | $0.017 / hr | $12 / month | $12 / month |

**Total steady-state: ~$240/month** for the full 10 K university dataset
with weekly refreshes and 1 M agent queries.

---

## Freshness Without Budget Destruction: Key Decisions

### 1. Batch API over real-time LLM calls
Anthropic's Batch API processes asynchronously at 50 % of the standard price.
For non-urgent enrichment jobs (Tier 1/2), batching 10 000 extractions
costs the same as 5 000 real-time calls.

### 2. Change-detection as the primary cost gate
The HEAD-check pattern means we invoke Playwright + LLM only when content
actually changed.  For 10 000 universities with weekly checks and ~5 % weekly
change rate, we scrape 500 pages/week instead of 10 000 — a **20× cost
reduction**.

### 3. Probabilistic freshness instead of exact freshness
For the evaluate_chances tool, exact staleness doesn't matter: a GPA median
of 3.96 vs 3.97 doesn't change any recommendation.  The cache TTL for MCP
responses can be 24 hours for stats data, 1 hour for deadline data.  This
dramatically reduces DB read pressure at query time.

### 4. Domain-level rate limiting, not global
Each university domain gets its own token bucket.  This maximises throughput
(all 10 000 domains run concurrently) while respecting each site's individual
rate limits — a necessary courtesy to avoid IP bans at scale.

### 5. Graceful degradation on scrape failure
When a university's site blocks us or changes structure too dramatically for
the LLM to parse, the pipeline writes a `scrape_failed` flag and the MCP
server falls back to the last known good data with a staleness warning,
rather than returning an error to the agent.  Data quality degrades gracefully.

---

## Data Quality & Validation

At 10 K universities, 1–3 % of scrapes will produce malformed or hallucinated
extractions.  Mitigations:

- **Range guards**: acceptance rates outside [0.01, 0.99], GPAs outside
  [2.0, 4.0], SAT scores outside [800, 1600] trigger a re-scrape.
- **Cross-source validation**: for the top 500 universities, validate scraped
  stats against Common Data Set XML feeds where available.
- **Confidence scores**: the LLM extraction prompt is extended to also return
  a `confidence` field (0–1).  Extractions below 0.7 are queued for human
  review before being committed.
- **Audit log**: every write to the universities table records the source URL,
  extraction timestamp, and confidence score for traceability.
