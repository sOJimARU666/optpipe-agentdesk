"""REL-002 executable Hypothesis properties P01-P06 for the offline market-data loader (NEW work, new file).

This is NOT the on-hold REL-002 v2 patch (sha256 8a50f3577925...). It was written from the documented P01-P06
proposals in audit/REL-002-test-gap-matrix.md, against the committed offline spec fixtures only. No production code is
touched, nothing here reaches the network, and every pack is a tmp copy of spec/fixtures/market_data/valid.

Design rules (matrix guardrails 1-5):
* Every property has a VALID CONTROL (unmodified twin must pass) and a REJECTING case (a deliberately invalid input must
  be refused, and refused for the right reason), so a property cannot pass vacuously.
* Semantic mutations are re-hashed with `mdpack.rehash`, so a manifest-integrity error cannot mask the path under test.
* Expected acceptance is derived from the schema / field semantics (see `_expect_number`), not blanket rejection.
* Generation is bounded (OPTPIPE_HYP_EXAMPLES, default 25 per property) and deterministic (derandomize) by default.
  Exploration: OPTPIPE_HYP_EXPLORE=1 turns derandomize off, so run with `--hypothesis-seed=N` and record N.

Hypothesis is a dev-only dependency that is NOT declared in pyproject.toml (adding it needs separate review). Without it
this module is skipped (hosted CI installs only `.[dev]`); set OPTPIPE_REQUIRE_HYPOTHESIS=1 to make absence a failure.
"""
from __future__ import annotations

import json
import os
import tempfile
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

try:
    from hypothesis import HealthCheck, given, settings
    from hypothesis import strategies as st
except ImportError:  # pragma: no cover - exercised only where Hypothesis is not provisioned
    if os.environ.get("OPTPIPE_REQUIRE_HYPOTHESIS") == "1":
        raise
    pytest.skip("hypothesis is not installed (dev-only, undeclared dependency); REL-002 properties not run",
                allow_module_level=True)

from mdpack import BUILT, DATE, FIX, NOW, SPEC, assert_closed, copy_pack, ids, load, rehash
from optpipe import marketdata as md

PROP = settings(
    max_examples=int(os.environ.get("OPTPIPE_HYP_EXAMPLES", "25")),
    deadline=None,
    derandomize=os.environ.get("OPTPIPE_HYP_EXPLORE") != "1",
    database=None,
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.data_too_large],
    print_blob=True,
)

JSON_FILES = ["index.json", "JPM.json", "WDC.json", "XMPLA.json", "XMPLB.json", "catalysts.json"]
TICKERS = {"JPM", "WDC", "XMPLA", "XMPLB"}
RAW = "__REL002_RAW__"


@contextmanager
def fresh_pack():
    """a throw-away tmp copy of the committed valid pack (a plain context manager: Hypothesis re-runs the test body)"""
    with tempfile.TemporaryDirectory(prefix="rel002-") as t:
        yield copy_pack(Path(t))


def _rewrite(d: Path, name: str, fn, raw: str | None = None) -> None:
    """edit one JSON document with fn(doc); `raw` replaces the RAW placeholder verbatim (for NaN / 1e999 tokens)"""
    p = d / name
    doc = json.loads(p.read_text(encoding="utf-8"))
    fn(doc)
    text = json.dumps(doc, indent=2) + "\n"
    if raw is not None:
        assert f'"{RAW}"' in text
        text = text.replace(f'"{RAW}"', raw, 1)
    p.write_text(text, encoding="utf-8")
    rehash(d)


def _shuffle_keys(value, rnd):
    if isinstance(value, dict):
        items = list(value.items())
        rnd.shuffle(items)
        return {k: _shuffle_keys(v, rnd) for k, v in items}
    if isinstance(value, list):
        return [_shuffle_keys(v, rnd) for v in value]
    return value


def _same_semantics(a, b):
    assert b.errors == a.errors
    assert b.index == a.index and b.snapshots == a.snapshots and b.catalysts == a.catalysts
    assert b.summary_md == a.summary_md and b.exit_code() == a.exit_code()


def _stale_quote(reasons) -> bool:
    return any(r.startswith("MD-19-STALE-quote") and "JPM.last_price" in r for r in reasons)


def _future_quote(reasons) -> bool:
    return any(r.startswith("MD-19-FUTURE") and "JPM.last_price" in r for r in reasons)


# ---------------------------------------------------------------------------------------------------- P01 key order
def test_p01_control_valid_pack_loads_and_snapshots_exist():
    with fresh_pack() as d:
        r = load(d)
        assert r.ok and r.errors == [] and set(r.snapshots) == TICKERS


@PROP
@given(name=st.sampled_from(JSON_FILES), rnd=st.randoms(use_true_random=False))
def test_p01_any_object_key_order_gives_identical_semantics(name, rnd):
    """Valid pack + arbitrary key permutation of one JSON file + honest rehash: acceptance and every exposed value equal."""
    with fresh_pack() as d:
        base = load(d)
        assert base.ok, base.errors
        doc = json.loads((d / name).read_text(encoding="utf-8"))
        (d / name).write_text(json.dumps(_shuffle_keys(doc, rnd), indent=2) + "\n", encoding="utf-8")
        rehash(d)
        _same_semantics(base, load(d))


@PROP
@given(rnd=st.randoms(use_true_random=False), name=st.sampled_from(["JPM.json", "WDC.json", "XMPLA.json", "XMPLB.json"]))
def test_p01_rejecting_key_order_never_masks_an_invalid_value(rnd, name):
    """The same invalid value is refused with the same error ids whatever the key order (rejecting case)."""
    with fresh_pack() as d:
        _rewrite(d, name, lambda doc: doc["last_price"].__setitem__("value", -1))
        twin = load(d)
        assert_closed(twin, "MD-03-SCHEMA")
        doc = json.loads((d / name).read_text(encoding="utf-8"))
        (d / name).write_text(json.dumps(_shuffle_keys(doc, rnd), indent=2) + "\n", encoding="utf-8")
        rehash(d)
        r = load(d)
        assert_closed(r, "MD-03-SCHEMA")
        assert ids(r.errors) == ids(twin.errors)


# ------------------------------------------------------------------------------------------------ P02 numeric leaves
_NONFINITE_TOKENS = ["NaN", "Infinity", "-Infinity"]


@st.composite
def _numeric_leaf(draw):
    """(raw JSON text, expect_ok, expected error id or None) for JPM/WDC last_price.value. Oracle from the schema:
    accepted iff a finite, non-bool JSON number > 0 within +/-2**53 for ints; non-finite tokens / overflow are
    MD-16-NONFINITE; wrong types and <= 0 are MD-03-SCHEMA; null with status OK is MD-06-UNKNOWN."""
    kind = draw(st.sampled_from(["float", "int", "token", "overflow", "other"]))
    if kind == "float":
        v = draw(st.floats(allow_nan=False, allow_infinity=False, min_value=-1e300, max_value=1e300))
        return repr(v), v > 0, (None if v > 0 else "MD-03-SCHEMA")
    if kind == "int":
        i = draw(st.one_of(st.integers(-10**6, 10**6), st.integers(-2**60, 2**60),
                           st.sampled_from([2**53, 2**53 + 1, -(2**53), -(2**53) - 1])))
        if abs(i) > 2**53:                                  # hard-coded on purpose: never derive the oracle from src
            return str(i), False, "MD-16-NONFINITE"
        return str(i), i > 0, (None if i > 0 else "MD-03-SCHEMA")
    if kind == "token":
        return draw(st.sampled_from(_NONFINITE_TOKENS)), False, "MD-16-NONFINITE"
    if kind == "overflow":
        return f"1e{draw(st.integers(309, 5000))}", False, "MD-16-NONFINITE"
    raw, rid = draw(st.sampled_from([("true", "MD-03-SCHEMA"), ("false", "MD-03-SCHEMA"), ("null", "MD-06-UNKNOWN"),
                                     ('"101.25"', "MD-03-SCHEMA"), ("[1]", "MD-03-SCHEMA"), ("{}", "MD-03-SCHEMA")]))
    return raw, False, rid


def test_p02_control_and_rejecting_anchor_cases():
    for raw, ok, rid in [("101.25", True, None), ("0", False, "MD-03-SCHEMA"), ("NaN", False, "MD-16-NONFINITE"),
                         ("true", False, "MD-03-SCHEMA")]:
        with fresh_pack() as d:
            _rewrite(d, "JPM.json", lambda doc: doc["last_price"].__setitem__("value", RAW), raw=raw)
            r = load(d)
            if ok:
                assert r.ok and r.errors == []
            else:
                assert_closed(r, rid)


@PROP
@given(leaf=_numeric_leaf(), name=st.sampled_from(["JPM.json", "WDC.json"]))
def test_p02_numeric_leaf_acceptance_matches_schema_and_failure_is_closed(leaf, name):
    raw, expect_ok, rid = leaf
    with fresh_pack() as d:
        _rewrite(d, name, lambda doc: doc["last_price"].__setitem__("value", RAW), raw=raw)
        r = load(d)
        if expect_ok:
            assert r.ok and r.errors == [], (raw, r.errors)
            assert r.snapshots[name[:-5]]["last_price"]["value"] == json.loads(raw)
        else:
            assert_closed(r, rid)
            assert md.no_idea_reasons(r, name[:-5], now=NOW, allow_labels=("EXAMPLE",))[0].startswith("MD-20-NO-IDEA")


# --------------------------------------------------------------------------------------- P03 clock monotone staleness
_AS_OF = BUILT - timedelta(minutes=10)                                   # JPM.last_price.as_of 08:50 New York
_LOADED = md.load_pack(FIX / "valid" / DATE, root=FIX / "valid", spec=SPEC, now=NOW)
_TZ = st.integers(-12 * 60, 14 * 60).map(lambda m: timezone(timedelta(minutes=m)))


def _reasons(now):
    return md.no_idea_reasons(_LOADED, "JPM", now=now, allow_labels=("EXAMPLE",))


def test_p03_control_quote_is_fresh_just_after_as_of_and_future_before_it():
    assert _LOADED.ok and _LOADED.quote_limit == timedelta(minutes=20)
    fresh = _reasons(_AS_OF + timedelta(minutes=1))
    assert not _stale_quote(fresh) and not _future_quote(fresh), fresh
    assert _future_quote(_reasons(_AS_OF - timedelta(seconds=1)))          # rejecting: a quote from the future
    assert not _stale_quote(_reasons(_AS_OF + timedelta(minutes=20)))      # boundary: exactly the limit is not stale
    assert _stale_quote(_reasons(_AS_OF + timedelta(minutes=20, seconds=1)))


@PROP
@given(s1=st.integers(-3600, 6 * 3600), extra=st.integers(0, 6 * 3600), tz1=_TZ, tz2=_TZ)
def test_p03_stale_quote_never_revives_as_the_clock_advances(s1, extra, tz1, tz2):
    t1 = (_AS_OF + timedelta(seconds=s1)).astimezone(tz1)
    t2 = (_AS_OF + timedelta(seconds=s1 + extra)).astimezone(tz2)
    r1, r2 = _reasons(t1), _reasons(t2)
    limit = 20 * 60
    assert _stale_quote(r1) == (s1 > limit)                               # exact oracle: strictly older than the limit
    assert _future_quote(r1) == (s1 < 0)
    if _stale_quote(r1):
        assert _stale_quote(r2), (s1, extra, r1, r2)                      # monotone: stale stays stale
    # direction matters (negative control): an EARLIER clock may be fresh although a later one is stale
    assert not (s1 + extra > limit and s1 <= limit) or (_stale_quote(r2) and not _stale_quote(r1))


# ------------------------------------------------------------------------------- P04 rejected pack exposes nothing
def _m_tamper(d, name):
    p = d / name
    p.write_bytes(p.read_bytes().replace(b'"schema_version"', b'"schema_versioN"', 1))


def _m_drop(d, name):
    md_drop = d / name
    md_drop.unlink()
    m = d / "pack.sha256"
    m.write_text("".join(x + "\n" for x in m.read_text().splitlines() if not x.endswith("  " + name)))


def _m_truncate(d, name):
    p = d / name
    p.write_bytes(p.read_bytes()[: len(p.read_bytes()) // 2])
    rehash(d)


def _m_dupkey(d, name):
    p = d / name
    p.write_text(p.read_text().replace('"kind":', '"kind": "x", "kind":', 1))
    rehash(d)


def _m_bom(d, name):
    p = d / name
    p.write_bytes(b"\xef\xbb\xbf" + p.read_bytes())
    rehash(d)


def _m_status(d, name):
    _rewrite(d, name, lambda doc: doc["last_price"].__setitem__("status", "BOGUS"))


_MUTATIONS = {
    "tamper-without-rehash": (_m_tamper, "MD-02-HASH", JSON_FILES),
    "drop-file": (_m_drop, "MD-02-INDEX", ["JPM.json", "WDC.json", "XMPLA.json", "XMPLB.json"]),
    "truncate": (_m_truncate, "MD-03-JSON", JSON_FILES),
    "duplicate-key": (_m_dupkey, "MD-16-DUPKEY", JSON_FILES),
    "utf8-bom": (_m_bom, "MD-16-ENCODING", JSON_FILES),
    "bad-status": (_m_status, "MD-03-SCHEMA", ["JPM.json", "WDC.json", "XMPLA.json", "XMPLB.json"]),
}


@st.composite
def _mutation(draw):
    key = draw(st.sampled_from(sorted(_MUTATIONS)))
    return key, draw(st.sampled_from(_MUTATIONS[key][2]))


def test_p04_control_valid_pack_exposes_its_data():
    with fresh_pack() as d:
        r = load(d)
        assert r.ok and r.index is not None and r.catalysts is not None and r.summary_md is not None
        assert set(r.snapshots) == TICKERS and md.no_idea_reasons(r, "JPM", now=NOW, allow_labels=("EXAMPLE",)) is not None


@PROP
@given(m=_mutation())
def test_p04_every_single_invalid_mutation_is_refused_and_exposes_nothing(m):
    key, name = m
    fn, expected_id, _ = _MUTATIONS[key]
    with fresh_pack() as d:
        fn(d, name)
        r = load(d)
        assert_closed(r, expected_id, rc=r.exit_code())
        assert r.exit_code() in (1, 2)
        assert md.usable_contracts(r, "JPM", now=NOW) == []
        reasons = md.no_idea_reasons(r, "JPM", now=NOW, allow_labels=("EXAMPLE",))
        assert len(reasons) == 1 and reasons[0].startswith("MD-20-NO-IDEA")


# ------------------------------------------------------------------------- P05 equivalent instants / offsets
def test_p05_control_naive_clock_is_refused():
    assert _reasons(NOW.replace(tzinfo=None))[0].startswith("MD-19-TZ")
    assert md.usable_contracts(_LOADED, "JPM", now=NOW.replace(tzinfo=None)) == []


@PROP
@given(secs=st.integers(-2 * 3600, 3 * 3600), tz=_TZ)
def test_p05_same_instant_in_any_offset_gives_the_same_outcome(secs, tz):
    base = BUILT + timedelta(seconds=secs)
    shown = base.astimezone(tz)
    assert shown == base                                   # same instant, different wall-clock rendering
    # error TEXT embeds the clock as rendered ("... is after the clock 08:59:59-04:00"), so compare normalised ids
    assert [r.split(" ", 1)[0] for r in _reasons(shown)] == [r.split(" ", 1)[0] for r in _reasons(base)]
    assert md.usable_contracts(_LOADED, "JPM", now=shown) == md.usable_contracts(_LOADED, "JPM", now=base)
    a = md.load_pack(FIX / "valid" / DATE, root=FIX / "valid", spec=SPEC, now=base)
    b = md.load_pack(FIX / "valid" / DATE, root=FIX / "valid", spec=SPEC, now=shown)
    assert (a.ok, ids(a.errors), a.exit_code()) == (b.ok, ids(b.errors), b.exit_code())


@PROP
@given(tz=_TZ, z=st.booleans())
def test_p05_equivalent_file_timestamp_offsets_load_identically(tz, z):
    """Rewrite JPM.last_price.as_of as the same instant in another offset: same validation and eligibility outcome."""
    with fresh_pack() as d:
        base = load(d)
        assert base.ok, base.errors
        inst = datetime.fromisoformat(base.snapshots["JPM"]["last_price"]["as_of"])
        text = inst.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ") if z else inst.astimezone(tz).isoformat()
        assert datetime.fromisoformat(text.replace("Z", "+00:00")) == inst
        _rewrite(d, "JPM.json", lambda doc: doc["last_price"].__setitem__("as_of", text))
        r = load(d)
        assert r.ok and r.errors == [], (text, r.errors)
        assert md.no_idea_reasons(r, "JPM", now=NOW, allow_labels=("EXAMPLE",)) == \
            md.no_idea_reasons(base, "JPM", now=NOW, allow_labels=("EXAMPLE",))


def _decision_result(as_of: str):
    """a hand-built loaded result whose JPM.last_price.as_of is `as_of` (bypasses load_pack: defence in depth)"""
    doc = json.loads((FIX / "valid" / DATE / "JPM.json").read_text(encoding="utf-8"))
    doc["last_price"]["as_of"] = as_of
    return md.PackResult(ok=True, date=DATE, index=_LOADED.index, snapshots={"JPM": doc}, catalysts=_LOADED.catalysts,
                         quote_limit=timedelta(minutes=20))


@PROP
@given(naive=st.datetimes(min_value=datetime(2026, 10, 19), max_value=datetime(2026, 10, 21), timezones=st.none()))
def test_p05_decision_time_refuses_a_naive_as_of_and_accepts_the_same_text_with_an_offset(naive):
    text = naive.replace(microsecond=0).isoformat()
    bad = md.no_idea_reasons(_decision_result(text), "JPM", now=NOW, allow_labels=("EXAMPLE",))
    assert any(r.startswith("MD-19-TZ") and "JPM.last_price" in r for r in bad), bad          # rejecting case
    good = md.no_idea_reasons(_decision_result(text + "-04:00"), "JPM", now=NOW, allow_labels=("EXAMPLE",))
    assert not any(r.startswith("MD-19-TZ") for r in good), good                              # valid control


def test_p05_rejecting_naive_file_timestamp_is_refused():
    with fresh_pack() as d:
        _rewrite(d, "JPM.json", lambda doc: doc["last_price"].__setitem__("as_of", "2026-10-20T08:50:00"))
        assert_closed(load(d), "MD-04-TIME-NAIVE")


# ------------------------------------------------------------------------ P06 bounded byte mutations never crash
@st.composite
def _byte_mutation(draw):
    name = draw(st.sampled_from(JSON_FILES + ["summary.md"]))
    op = draw(st.sampled_from(["flip", "delete", "insert", "truncate", "dup-slice"]))
    return name, op, draw(st.floats(0, 1)), draw(st.integers(0, 255)), draw(st.integers(1, 16))


def _apply(data: bytes, op: str, frac: float, byte: int, n: int) -> bytes:
    i = min(int(frac * len(data)), max(len(data) - 1, 0))
    if op == "flip":
        return data[:i] + bytes([data[i] ^ (byte or 1)]) + data[i + 1:]
    if op == "delete":
        return data[:i] + data[i + n:]
    if op == "insert":
        return data[:i] + bytes([byte]) * n + data[i:]
    if op == "truncate":
        return data[:i]
    return data[:i] + data[i:i + n] + data[i:]


def test_p06_control_and_rejecting_anchor_cases():
    with fresh_pack() as d:
        assert load(d).ok
    with fresh_pack() as d:                               # rejecting: half a JSON document
        p = d / "WDC.json"
        p.write_bytes(_apply(p.read_bytes(), "truncate", 0.5, 0, 1))
        rehash(d)
        assert_closed(load(d), "MD-03-JSON", rc=2)


@PROP
@given(m=_byte_mutation())
def test_p06_byte_mutations_are_valid_or_fail_closed_never_crash_or_fall_back(m):
    name, op, frac, byte, n = m
    with fresh_pack() as d:
        p = d / name
        p.write_bytes(_apply(p.read_bytes(), op, frac, byte, n))
        rehash(d)                                          # integrity must not mask the parser / schema path
        r = load(d)                                        # must not raise
        assert "MD-00-CRASH" not in ids(r.errors), r.errors
        assert r.ok == (r.errors == [])
        if r.ok:
            assert set(r.snapshots) == TICKERS and r.index is not None and r.exit_code() == 0
        else:
            assert_closed(r, *sorted(ids(r.errors))[:1], rc=r.exit_code())
            assert r.exit_code() in (1, 2)
