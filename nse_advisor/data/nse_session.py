"""
NSE Session Manager — handles all 6 anti-bot layers.

NSE blocks bots using 6 distinct layers:
- Layer 1: 403 on first request (no homepage cookie before API call)
- Layer 2: Works 30min then silently fails (session cookies expire ~25min)
- Layer 3: Returns 200 but content is HTML (expired session, no JSON validation)
- Layer 4: Immediate 403 all day (IP banned from too many rapid requests)
- Layer 5: Empty JSON or wrong data (missing/wrong headers)
- Layer 6: JS challenge blocks requests (Cloudflare or NSE JS fingerprinting)

Usage:
    session = NseSession()
    await session.init()
    data = await session.fetch("https://www.nseindia.com/api/option-chain-indices?symbol=NIFTY")
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
import time
from datetime import datetime
from typing import Any, Optional

import requests
from zoneinfo import ZoneInfo

from nse_advisor.config import get_settings

logger = logging.getLogger(__name__)
IST = ZoneInfo("Asia/Kolkata")


# ── Layer 5 fix: exact headers NSE expects ──────────────────────────────────
# Do NOT change these. NSE validates: User-Agent, Referer, sec-fetch headers.
# Never rotate User-Agent mid-session — it's tied to the session cookie.

BASE_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9,hi;q=0.8",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
    "sec-ch-ua": '"Not_A Brand";v="8", "Chromium";v="120", "Google Chrome";v="120"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"Windows"',
    "Sec-Fetch-Dest": "empty",
    "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Site": "same-origin",
}

API_HEADERS = {
    **BASE_HEADERS,
    "Referer": "https://www.nseindia.com/option-chain",
    "X-Requested-With": "XMLHttpRequest",
}

HOMEPAGE_HEADERS = {
    **BASE_HEADERS,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
}


class NseSessionError(Exception):
    """Base exception for NSE session errors."""
    pass


class NseIpBannedError(NseSessionError):
    """Raised when IP is banned by NSE/Cloudflare."""
    pass


class NseSessionStaleError(NseSessionError):
    """Raised when session is stale and needs refresh."""
    pass


class NseSession:
    """
    Thread-safe NSE session with automatic cookie refresh.

    Fixes applied:
    - Layer 1: 3-step init (homepage → option-chain page → API)
    - Layer 2: auto-refresh every EXPIRY_MINUTES before cookies expire
    - Layer 3: HTML response detection — re-inits on silent expiry
    - Layer 4: jitter on all intervals, consecutive failure detection
    - Layer 5: full browser-like header set, Referer correct per endpoint
    - Layer 6: Playwright fallback if requests is JS-challenged
    """

    EXPIRY_MINUTES: int = 25       # Refresh before NSE's ~30min expiry
    MAX_RETRIES: int = 3
    RATE_LIMIT_BACKOFF: int = 60   # seconds to wait on 429
    IP_BAN_BACKOFF: int = 3600     # seconds to wait if IP banned

    def __init__(self) -> None:
        """Initialize session manager."""
        self._session: Optional[requests.Session] = None
        self._last_init: Optional[datetime] = None
        self._consecutive_failures: int = 0
        self._lock = asyncio.Lock()
        self._playwright_mode: bool = False   # Layer 6 fallback flag
        self._settings = get_settings()

    # ── Properties ──────────────────────────────────────────────────────────

    @property
    def last_refresh(self) -> Optional[datetime]:
        """Get the last refresh timestamp."""
        return self._last_init

    @property
    def session_age_minutes(self) -> float:
        """Get session age in minutes."""
        if not self._last_init:
            return float("inf")
        return (datetime.now(IST) - self._last_init).total_seconds() / 60

    @property
    def is_initialized(self) -> bool:
        """Check if session is initialized and not stale."""
        return self._session is not None and not self._is_stale()

    # ── Layer 1 + 2: 3-step initialization ─────────────────────────────────

    def _init_sync(self) -> None:
        """
        Synchronous 3-step NSE session bootstrap.
        Must be run via asyncio.to_thread() — blocks for ~3-4 seconds.

        Step 1: Visit homepage → NSE sets bm_sz, nsit cookies
        Step 2: Visit option-chain page → NSE sets nseappid cookie
        Step 3: Session is now trusted for API calls
        """
        s = requests.Session()

        # Step 1: Homepage
        logger.debug("NSE init step 1: visiting homepage")
        s.get("https://www.nseindia.com", headers=HOMEPAGE_HEADERS, timeout=15)
        time.sleep(random.uniform(1.8, 2.5))  # Human-like pause

        # Step 2: Option chain page (sets nseappid cookie)
        logger.debug("NSE init step 2: visiting option-chain page")
        s.get(
            "https://www.nseindia.com/option-chain",
            headers={**BASE_HEADERS, "Referer": "https://www.nseindia.com"},
            timeout=15,
        )
        time.sleep(random.uniform(0.8, 1.4))

        self._session = s
        self._last_init = datetime.now(IST)
        self._consecutive_failures = 0
        logger.info("NSE session initialized", extra={
            "cookies": len(s.cookies),
            "timestamp": self._last_init.isoformat()
        })

    async def init(self) -> None:
        """Initialize or re-initialize the NSE session (async wrapper)."""
        async with self._lock:
            await asyncio.to_thread(self._init_sync)

    async def init_session(self) -> None:
        """Alias for init() for backward compatibility."""
        await self.init()

    async def refresh_session(self) -> None:
        """
        Refresh session cookies.
        
        Called by APScheduler every NSE_SESSION_REFRESH_MINUTES.
        Logs success/failure to event log and sends Telegram alert on failure.
        """
        from nse_advisor.storage.event_log import get_event_log, EventType
        
        logger.info("Refreshing NSE session cookies")
        try:
            await self.init()
            # Log success
            get_event_log().log(
                EventType.NSE_SESSION_REFRESHED,
                {"success": True, "refresh_time": self._last_init.isoformat() if self._last_init else None}
            )
        except Exception as e:
            # Log failure
            get_event_log().log(
                EventType.NSE_SESSION_ERROR,
                {"error": str(e)}
            )
            logger.error(f"NSE session refresh failed: {e}")
            # Try to send Telegram alert
            try:
                from nse_advisor.alerts.telegram import get_telegram_dispatcher
                telegram = get_telegram_dispatcher()
                await telegram.send_risk_alert(
                    "NSE_SESSION_ERROR",
                    "NSE session refresh failed — data may be stale",
                    str(e)
                )
            except Exception as telegram_error:
                logger.warning(f"Failed to send Telegram alert: {telegram_error}")

    # ── Layer 2: staleness check ────────────────────────────────────────────

    def _is_stale(self) -> bool:
        """Check if session is stale and needs refresh."""
        if not self._last_init or not self._session:
            return True
        age_minutes = (datetime.now(IST) - self._last_init).total_seconds() / 60
        return age_minutes >= self.EXPIRY_MINUTES

    # ── Layer 3: HTML response guard ────────────────────────────────────────

    @staticmethod
    def _is_html(text: str) -> bool:
        """
        NSE returns 200 OK with an HTML login/redirect page when session expires.
        Always check before calling .json() — no exception is raised otherwise.
        """
        stripped = text.strip()[:200].lower()
        return stripped.startswith("<!doctype") or "<html" in stripped

    # ── Layer 4: error classification ───────────────────────────────────────

    @staticmethod
    def _classify_error(status_code: int, text: str) -> str:
        """Classify HTTP error for appropriate handling."""
        if status_code == 429:
            return "RATE_LIMITED"
        if status_code == 403:
            if "cloudflare" in text.lower() or "cf-ray" in text.lower():
                return "IP_BANNED_CLOUDFLARE"
            return "SESSION_EXPIRED_403"
        if status_code == 503:
            return "NSE_DOWN"
        return "OTHER"

    # ── Main fetch method ───────────────────────────────────────────────────

    async def fetch(self, url: str) -> dict[str, Any]:
        """
        Fetch a JSON endpoint from NSE with full anti-bot handling.

        Args:
            url: Full URL to fetch
            
        Returns:
            Parsed JSON response
            
        Raises:
            NseIpBannedError: If IP is banned by Cloudflare (requires manual intervention)
            NseSessionError: If all retries exhausted
        """
        if self._is_stale():
            await self.init()

        for attempt in range(self.MAX_RETRIES):
            try:
                response = await asyncio.to_thread(
                    self._session.get,
                    url,
                    headers=API_HEADERS,
                    timeout=10,
                )

                # Layer 4: handle HTTP error codes
                if response.status_code == 429:
                    logger.warning("NSE rate limited", extra={"url": url, "attempt": attempt})
                    await asyncio.sleep(self.RATE_LIMIT_BACKOFF)
                    continue

                if response.status_code == 403:
                    error_type = self._classify_error(403, response.text)
                    if error_type == "IP_BANNED_CLOUDFLARE":
                        logger.error("NSE IP banned by Cloudflare", extra={"url": url})
                        raise NseIpBannedError(
                            "IP banned by NSE/Cloudflare. "
                            "Wait 1-6 hours or change IP. "
                            "Reduce fetch frequency to avoid future bans."
                        )
                    # Session expired with 403 → re-init
                    logger.warning("NSE 403 - reinitializing session", extra={"url": url, "attempt": attempt})
                    await self.init()
                    await asyncio.sleep(2 ** attempt)
                    continue

                if response.status_code == 503:
                    logger.warning("NSE service unavailable", extra={"url": url})
                    await asyncio.sleep(30)
                    continue

                # Layer 3: detect HTML masquerading as 200 OK
                if self._is_html(response.text):
                    logger.warning(
                        "NSE returned HTML instead of JSON",
                        extra={"url": url, "attempt": attempt, "hint": "Session expired — re-initializing"}
                    )
                    await self.init()
                    continue

                data = response.json()

                # Validate response has actual content (not empty dict)
                if not data:
                    logger.warning("NSE returned empty response", extra={"url": url})
                    await asyncio.sleep(2)
                    continue

                self._consecutive_failures = 0
                return data

            except NseIpBannedError:
                raise  # Don't retry IP bans

            except Exception as e:
                self._consecutive_failures += 1
                logger.error(
                    "NSE fetch error",
                    extra={
                        "url": url,
                        "attempt": attempt,
                        "error": str(e),
                        "consecutive_failures": self._consecutive_failures,
                    }
                )

                if self._consecutive_failures >= 5:
                    # Persistent failure — likely IP issue or NSE outage
                    raise NseSessionError(
                        f"NSE fetch failing persistently ({self._consecutive_failures} times). "
                        f"Last error: {e}"
                    )

                backoff = min(2 ** attempt + random.uniform(0, 1), 30)
                await asyncio.sleep(backoff)

        raise NseSessionError(f"All {self.MAX_RETRIES} retries failed for {url}")

    # ── Layer 6: Playwright fallback ────────────────────────────────────────

    async def fetch_with_browser(self, url: str) -> dict[str, Any]:
        """
        Playwright fallback for when requests is blocked by JS challenges.

        Install: pip install playwright && playwright install chromium
        Use only as last resort — ~3-4s per fetch, not suitable for 5s loops.
        Switch to this if fetch() raises NseIpBannedError and IP cannot be changed.
        """
        try:
            from playwright.async_api import async_playwright
        except ImportError:
            raise NseSessionError(
                "Playwright not installed. Run: pip install playwright && playwright install chromium"
            )

        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            context = await browser.new_context(
                user_agent=BASE_HEADERS["User-Agent"],
                locale="en-US",
            )
            page = await context.new_page()

            # Warm up: visit homepage first (same as 3-step init)
            await page.goto("https://www.nseindia.com", wait_until="networkidle")
            await page.wait_for_timeout(random.randint(1500, 2500))
            await page.goto("https://www.nseindia.com/option-chain", wait_until="networkidle")
            await page.wait_for_timeout(random.randint(800, 1200))

            # Fetch API URL with cookies already set in browser context
            response = await page.goto(url)
            if not response:
                raise NseSessionError(f"Playwright got no response for {url}")

            text = await response.text()
            await browser.close()

            if self._is_html(text):
                raise NseSessionError("Playwright fetch returned HTML — NSE blocking")

            return json.loads(text)

    # ── Utility ─────────────────────────────────────────────────────────────

    def status(self) -> dict[str, Any]:
        """Get session status for monitoring."""
        return {
            "initialized": self._session is not None,
            "age_minutes": round(self.session_age_minutes, 1),
            "is_stale": self._is_stale(),
            "consecutive_failures": self._consecutive_failures,
            "playwright_mode": self._playwright_mode,
            "last_init": self._last_init.isoformat() if self._last_init else None,
        }

    def close(self) -> None:
        """Close the session."""
        if self._session:
            self._session.close()
            self._session = None
            self._last_init = None
            logger.info("NSE session closed")


# Global session instance
_nse_session: NseSession | None = None


def get_nse_session() -> NseSession:
    """Get or create global NSE session."""
    global _nse_session
    if _nse_session is None:
        _nse_session = NseSession()
    return _nse_session


async def close_nse_session() -> None:
    """Close global NSE session."""
    global _nse_session
    if _nse_session is not None:
        _nse_session.close()
        _nse_session = None
