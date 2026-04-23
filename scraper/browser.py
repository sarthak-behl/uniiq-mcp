"""
Playwright-based browser pool with stealth configuration and token-bucket rate limiting.

Anti-bot measures applied:
  • Random user-agent rotation (Chrome/Firefox/Safari on Win/Mac/Linux)
  • navigator.webdriver spoofing via page init script
  • Randomised viewport dimensions and locale/timezone headers
  • Token-bucket rate limiter with jitter to avoid thundering-herd detection
  • Exponential-backoff retry on 429 / 503 responses
"""

import asyncio
import random
import time
from typing import Optional

from playwright.async_api import async_playwright, Browser, Page

USER_AGENTS: list[str] = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_4_1) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Safari/605.1.15",
]

# Injected into every new page to strip Playwright's webdriver fingerprint
_STEALTH_SCRIPT = """
Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });
Object.defineProperty(navigator, 'languages', { get: () => ['en-US', 'en'] });
window.chrome = { runtime: {} };
"""


class BrowserPool:
    """Thin pool of Chromium instances; hands out stealth-configured pages."""

    def __init__(self, pool_size: int = 2):
        self._pool_size = pool_size
        self._playwright = None
        self._browsers: list[Browser] = []

    async def start(self) -> None:
        self._playwright = await async_playwright().start()
        for _ in range(self._pool_size):
            browser = await self._playwright.chromium.launch(
                headless=True,
                args=[
                    "--no-sandbox",
                    "--disable-blink-features=AutomationControlled",
                    "--disable-dev-shm-usage",
                    "--disable-gpu",
                ],
            )
            self._browsers.append(browser)

    async def stop(self) -> None:
        for b in self._browsers:
            await b.close()
        if self._playwright:
            await self._playwright.stop()

    async def new_page(self) -> Page:
        browser = random.choice(self._browsers)
        context = await browser.new_context(
            user_agent=random.choice(USER_AGENTS),
            viewport={
                "width": random.randint(1280, 1920),
                "height": random.randint(720, 1080),
            },
            locale="en-US",
            timezone_id="America/New_York",
            java_script_enabled=True,
            # Mimic a real browser's accepted content types
            extra_http_headers={"Accept-Language": "en-US,en;q=0.9"},
        )
        page = await context.new_page()
        await page.add_init_script(_STEALTH_SCRIPT)
        return page


class RateLimiter:
    """
    Token-bucket limiter.  Callers `await limiter.acquire()` before each request.
    Adds random jitter so requests don't arrive in a perfectly metronomic pattern.
    """

    def __init__(self, requests_per_minute: int = 8):
        self._rate = requests_per_minute / 60.0  # tokens/second
        self._tokens: float = float(requests_per_minute)
        self._max: float = float(requests_per_minute)
        self._last_refill: float = time.monotonic()

    async def acquire(self) -> None:
        now = time.monotonic()
        elapsed = now - self._last_refill
        self._tokens = min(self._max, self._tokens + elapsed * self._rate)
        self._last_refill = now

        if self._tokens < 1.0:
            wait = (1.0 - self._tokens) / self._rate
            await asyncio.sleep(wait)
            self._tokens = 0.0
        else:
            self._tokens -= 1.0

        # Jitter prevents correlated request bursts that trigger Cloudflare/Akamai
        await asyncio.sleep(random.uniform(0.8, 2.5))


async def fetch_page_text(
    pool: BrowserPool,
    limiter: RateLimiter,
    url: str,
    wait_selector: Optional[str] = None,
    max_retries: int = 4,
) -> str:
    """
    Navigate to *url*, wait for the DOM to settle, and return all visible text.

    Retries on network errors or anti-bot responses (429/503) with exponential
    back-off.  Never relies on specific CSS selectors — the caller receives raw
    text that an LLM will parse.
    """
    for attempt in range(max_retries):
        page = await pool.new_page()
        try:
            await limiter.acquire()

            response = await page.goto(url, wait_until="networkidle", timeout=45_000)

            if response and response.status in (429, 503):
                backoff = 2 ** attempt + random.uniform(0, 1)
                print(f"[scraper] Rate-limited ({response.status}), sleeping {backoff:.1f}s")
                await asyncio.sleep(backoff)
                continue

            # If the caller knows a key element that signals the page is ready, wait for it
            if wait_selector:
                try:
                    await page.wait_for_selector(wait_selector, timeout=10_000)
                except Exception:
                    pass  # Best-effort; continue with whatever rendered

            # Extra settle time for React hydration / lazy-loaded stats widgets
            await asyncio.sleep(random.uniform(1.5, 3.0))

            # Extract ALL visible text — no DOM path dependencies
            text: str = await page.evaluate(
                "() => document.body.innerText"
            )
            return text

        except Exception as exc:
            if attempt == max_retries - 1:
                raise
            backoff = 2 ** attempt + random.uniform(0, 1)
            print(f"[scraper] Error on attempt {attempt + 1}: {exc}. Retrying in {backoff:.1f}s")
            await asyncio.sleep(backoff)
        finally:
            await page.context.close()

    raise RuntimeError(f"Failed to fetch {url} after {max_retries} attempts")
