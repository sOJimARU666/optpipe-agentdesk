"""Provider interfaces (read-only market data only - there is deliberately no trading interface)."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Optional, Protocol, runtime_checkable

from ..models.common import Event, OptionChain, ScreenResult, UnderlyingQuote


@dataclass
class FetchResult:
    text: str
    url: str
    fetched_at: datetime
    kind: str            # "live" | "fixture" | "cache"


class DataUnavailable(Exception):
    """Raised by a fetcher/provider when a resource cannot be obtained (becomes a missing-data flag)."""


@runtime_checkable
class Fetcher(Protocol):
    def get(self, resource: str, key: str, url: str) -> FetchResult: ...


@runtime_checkable
class QuoteProvider(Protocol):
    def get_quote(self, symbol: str) -> UnderlyingQuote: ...


@runtime_checkable
class OptionChainProvider(Protocol):
    def get_chain(self, symbol: str, quote: Optional[UnderlyingQuote] = None) -> OptionChain: ...


@runtime_checkable
class EventProvider(Protocol):
    def get_events(self, symbol: str) -> list[Event]: ...


@runtime_checkable
class ScreenerProvider(Protocol):
    def get_screen(self, screen: str) -> ScreenResult: ...


@runtime_checkable
class GlobalSnapshotProvider(Protocol):
    def get_snapshot(self, run_id: str) -> tuple[dict, list[dict]]:
        """-> (schema global_snapshot section, the schema sources it references)."""
        ...
