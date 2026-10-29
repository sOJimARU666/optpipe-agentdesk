"""Fetchers: HTTP (live, GET only) and fixture files (offline). Both return FetchResult."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from .base import DataUnavailable, FetchResult

FIXTURE_NAMES = {
    "finviz": "fv_{key}.html",          # key = screen without 'ta_' prefix
    "nasdaq_info": "info_{key}.json",
    "nasdaq_chain": "oc_{key}.json",
}


class FixtureFetcher:
    """Serves Doodle's captured raw files instead of the network."""

    def __init__(self, directory: str | Path, fetched_at: datetime):
        self.dir = Path(directory)
        self.fetched_at = fetched_at

    def get(self, resource: str, key: str, url: str) -> FetchResult:
        pattern = FIXTURE_NAMES.get(resource)
        if pattern is None:
            raise DataUnavailable(f"no fixture mapping for {resource}")
        path = self.dir / pattern.format(key=key.removeprefix("ta_"))
        if not path.exists():
            raise DataUnavailable(f"fixture not found: {path.name}")
        return FetchResult(text=path.read_text(), url=url, fetched_at=self.fetched_at, kind="fixture")


class HttpFetcher:
    """Live read-only GET with a browser User-Agent (curl-like). Not used by tests or the offline CLI."""

    def __init__(self, user_agent: str, timeout_s: float = 20):
        self.headers = {"User-Agent": user_agent, "Accept": "text/html,application/json;q=0.9,*/*;q=0.8",
                        "Accept-Language": "en-US,en;q=0.9"}
        self.timeout = timeout_s

    def get(self, resource: str, key: str, url: str) -> FetchResult:
        import requests
        try:
            r = requests.get(url, headers=self.headers, timeout=self.timeout)
            r.raise_for_status()
        except Exception as e:  # network/HTTP errors become missing-data flags upstream
            raise DataUnavailable(f"{resource}:{key} GET failed: {e}") from e
        return FetchResult(text=r.text, url=url, fetched_at=datetime.now(timezone.utc), kind="live")
