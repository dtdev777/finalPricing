"""HTTP access shared by the store crawlers: robots.txt, a delay between requests, retries.

A site that refuses the crawler (robots.txt or a bot challenge) raises Blocked. The crawler
never tries to get around it.
"""
import logging
import random
import re
import time
from datetime import UTC, datetime
from typing import Any
from urllib import robotparser
from urllib.parse import urlsplit

import requests

logger = logging.getLogger(__name__)

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
)
MIN_DELAY_SECONDS = 1.5
RETRIES = 2
TIMEOUT_SECONDS = 30
CHALLENGE_MARKERS = ("Just a moment...", "cf-chl", "challenge-platform")


class Blocked(RuntimeError):
    """The site refused the crawler."""


def parse_price(text: Any) -> int | None:
    """'38.690.000d', '24,990,000 VND', 24990000 -> 24990000; text without digits -> None."""
    if text is None:
        return None
    if isinstance(text, int | float):
        return int(text)
    digits = re.sub(r"\D", "", str(text))
    return int(digits) if digits else None


def now_utc() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class Fetcher:
    def __init__(self, delay: float = MIN_DELAY_SECONDS):
        self.delay = delay
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": USER_AGENT, "Accept-Language": "vi-VN,vi;q=0.9"})
        self._robots: dict[str, robotparser.RobotFileParser] = {}
        self._last_request = 0.0

    def get(self, url: str) -> str:
        return self.request("GET", url).text

    def post(self, url: str, **kwargs: Any) -> requests.Response:
        return self.request("POST", url, **kwargs)

    def request(self, method: str, url: str, **kwargs: Any) -> requests.Response:
        if not self.allowed(url):
            raise Blocked(f"robots.txt disallows {url}")
        return self._send(method, url, **kwargs)

    def allowed(self, url: str) -> bool:
        parts = urlsplit(url)
        root = f"{parts.scheme}://{parts.netloc}"
        if root not in self._robots:
            parser = robotparser.RobotFileParser()
            response = self._send("GET", f"{root}/robots.txt", check=False)
            if response.status_code in (401, 403):
                raise Blocked(f"{root} refused robots.txt with HTTP {response.status_code}")
            parser.parse(response.text.splitlines() if response.ok else [])
            self._robots[root] = parser
        return self._robots[root].can_fetch(USER_AGENT, url)

    def _send(self, method: str, url: str, check: bool = True, **kwargs: Any) -> requests.Response:
        for attempt in range(RETRIES + 1):
            self._wait()
            try:
                response = self.session.request(method, url, timeout=TIMEOUT_SECONDS, **kwargs)
            except requests.RequestException:
                if attempt == RETRIES:
                    raise
                logger.warning("Retrying %s after a connection error", url)
                continue
            if is_challenge(response):
                raise Blocked(f"{urlsplit(url).netloc} answered with a bot challenge")
            if response.status_code >= 500 and attempt < RETRIES:
                logger.warning("Retrying %s after HTTP %s", url, response.status_code)
                continue
            if check:
                response.raise_for_status()
            return response
        raise AssertionError("unreachable")

    def _wait(self) -> None:
        pause = self.delay + random.uniform(0, 0.5) - (time.monotonic() - self._last_request)
        if pause > 0:
            time.sleep(pause)
        self._last_request = time.monotonic()


def is_challenge(response: requests.Response) -> bool:
    return response.status_code in (403, 503) and any(marker in response.text for marker in CHALLENGE_MARKERS)
