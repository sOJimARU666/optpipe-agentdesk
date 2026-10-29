"""Finviz screener provider (HTML, browser User-Agent).

Ticker cells contain a logo <span> with the first letter followed by the ticker link, so a naive
get_text() yields 'WWDC' for WDC. We read data-boxover-ticker / the tab-link instead, and
`dedouble_ticker` repairs text-only extractions.
"""
from __future__ import annotations

from typing import Optional

from bs4 import BeautifulSoup

from ..models.common import ScreenResult, ScreenRow
from .base import DataUnavailable, Fetcher

SCREENER_URL = "https://finviz.com/screener?v=111&s={screen}&f={filters}"


def _num(s: str) -> Optional[float]:
    s = (s or "").replace(",", "").replace("%", "").strip()
    try:
        return float(s)
    except ValueError:
        return None


def dedouble_ticker(text: str, known: Optional[str] = None) -> str:
    """'WWDC' -> 'WDC' when it is the logo letter + ticker. Uses the link text when available."""
    text = text.strip()
    if known:
        return known.strip()
    if len(text) >= 2 and text[0] == text[1]:
        return text[1:]
    return text


def parse_screener(html: str, screen: str, source: Optional[str] = None) -> ScreenResult:
    soup = BeautifulSoup(html, "lxml")
    table = soup.select_one("table.screener_table")
    if table is None:
        return ScreenResult(screen=screen, status="empty", source=source,
                            note="no result table in page (no rows matched or layout changed)")
    rows = table.find_all("tr")
    header = [c.get_text(strip=True) for c in rows[0].find_all(["th", "td"])] if rows else []
    idx = {h: i for i, h in enumerate(header)}
    out: list[ScreenRow] = []
    for tr in rows[1:]:
        cells = tr.find_all("td")
        if len(cells) < len(header):
            continue
        tcell = cells[idx.get("Ticker", 1)]
        link = tcell.select_one("a.tab-link")
        ticker = tcell.get("data-boxover-ticker") or dedouble_ticker(
            tcell.get_text("", strip=True), link.get_text(strip=True) if link else None)

        def col(name: str) -> str:
            i = idx.get(name)
            return cells[i].get_text(strip=True) if i is not None else ""

        out.append(ScreenRow(ticker=ticker, company=col("Company") or None, sector=col("Sector") or None,
                             industry=col("Industry") or None, country=col("Country") or None,
                             market_cap=col("Market Cap") or None, price=_num(col("Price")),
                             change_pct=_num(col("Change %") or col("Change")),
                             volume=int(_num(col("Volume")) or 0) or None))
    return ScreenResult(screen=screen, status="ok" if out else "empty", rows=out, source=source)


class FinvizScreener:
    def __init__(self, fetcher: Fetcher, filters: str):
        self.fetcher = fetcher
        self.filters = filters

    def get_screen(self, screen: str) -> ScreenResult:
        url = SCREENER_URL.format(screen=screen, filters=self.filters)
        try:
            res = self.fetcher.get("finviz", screen, url)
        except DataUnavailable as e:
            return ScreenResult(screen=screen, status="error", source=url, note=str(e))
        r = parse_screener(res.text, screen, source=url)
        r.fetched_at = res.fetched_at
        return r
