"""EODHD provider - PLACEHOLDER (Doodle to confirm plan/endpoints; would add IV and greeks).

Implements the same read-only Protocols as NasdaqProvider once filled in.
"""
from __future__ import annotations

from typing import Optional

from ..models.common import Event, OptionChain, UnderlyingQuote


class EODHDProvider:
    def __init__(self, api_key_env: str = "EODHD_API_KEY"):
        self.api_key_env = api_key_env

    def get_quote(self, symbol: str) -> UnderlyingQuote:
        raise NotImplementedError("EODHD provider not implemented yet")

    def get_chain(self, symbol: str, quote: Optional[UnderlyingQuote] = None) -> OptionChain:
        raise NotImplementedError("EODHD provider not implemented yet")

    def get_events(self, symbol: str) -> list[Event]:
        raise NotImplementedError("EODHD provider not implemented yet")
