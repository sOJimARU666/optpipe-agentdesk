"""REL-002 P03: offline decision-time quote staleness is monotone.

Uses the pinned valid EXAMPLE fixture; never loads external market data.
The test is intentionally narrower than monotonicity of every NO IDEA reason.
"""
from __future__ import annotations

from datetime import timedelta

import pytest

from mdpack import BUILT, DATE, FIX, SPEC, ids
from optpipe import marketdata as md


@pytest.mark.parametrize("minutes_after_build", [11, 12, 16, 21, 45, 180])
def test_stale_last_price_never_revives_as_decision_clock_advances(minutes_after_build):
    """JPM's 08:50 quote is stale after 09:10 under the pinned 20-min limit."""
    pack = FIX / "valid" / DATE
    result = md.load_pack(pack, root=pack.parent, spec=SPEC, now=BUILT + timedelta(minutes=5))
    assert result.ok, result.errors
    assert result.quote_limit == timedelta(minutes=20)

    early = BUILT + timedelta(minutes=11)
    later = BUILT + timedelta(minutes=minutes_after_build)
    assert later >= early
    early_reasons = md.no_idea_reasons(result, "JPM", now=early, allow_labels=("EXAMPLE",))
    later_reasons = md.no_idea_reasons(result, "JPM", now=later, allow_labels=("EXAMPLE",))

    assert "MD-19-STALE-quote" in ids(early_reasons), early_reasons
    assert "MD-19-STALE-quote" in ids(later_reasons), later_reasons
    assert any("JPM.last_price" in reason for reason in early_reasons), early_reasons
    assert any("JPM.last_price" in reason for reason in later_reasons), later_reasons
