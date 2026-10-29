"""Tiny on-disk cache for read-only GETs (macro calendars, Treasury rates). Owner: Moodle / Poodle.

Entries are JSON: {url, fetched_at, text}. A cached copy keeps its ORIGINAL fetched_at, so the
point-in-time rule still applies (a copy fetched after a run's as_of is invisible to that run).
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from .base import DataUnavailable, Fetcher, FetchResult


class CachedFetcher:
    def __init__(self, inner: Fetcher, directory: Path, ttl_hours: float, now: Optional[datetime] = None):
        self.inner, self.dir, self.ttl = inner, Path(directory), timedelta(hours=ttl_hours)
        self._now = now
        self.last_status: dict[str, str] = {}      # key -> "hit" | "miss" | "stale-fallback"

    def now(self) -> datetime:
        return self._now or datetime.now(timezone.utc)

    def _path(self, resource: str, key: str, url: str) -> Path:
        h = hashlib.sha256(url.encode()).hexdigest()[:12]
        return self.dir / resource / f"{key}-{h}.json"

    def _read(self, p: Path) -> Optional[FetchResult]:
        try:
            d = json.loads(p.read_text())
            return FetchResult(text=d["text"], url=d["url"], fetched_at=datetime.fromisoformat(d["fetched_at"]),
                               kind="cache")
        except (OSError, ValueError, KeyError):
            return None

    def get(self, resource: str, key: str, url: str) -> FetchResult:
        p = self._path(resource, key, url)
        cached = self._read(p) if p.exists() else None
        if cached and self.now() - cached.fetched_at <= self.ttl:
            self.last_status[key] = "hit"
            return cached
        try:
            r = self.inner.get(resource, key, url)
        except DataUnavailable:
            if cached:                      # expired copy beats nothing; caller sees its true fetched_at
                self.last_status[key] = "stale-fallback"
                return cached
            raise
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({"url": r.url, "fetched_at": r.fetched_at.isoformat(), "text": r.text}))
        self.last_status[key] = "miss"
        return r
