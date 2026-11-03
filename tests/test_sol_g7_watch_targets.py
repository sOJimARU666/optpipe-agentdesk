"""SOL-ACCEPT-001 G7: the watcher library itself fails closed, not only the `optpipe watch` CLI gate.

- `targets_from_session` never watches an `open` paper position the INV-06 guards reject (or whose approval does
  not resolve, INV-04): such a position is not a watched `open` target.
- wrong-typed or naive approval times never crash it and are never honoured as a real approval.
- `optpipe watch` parses the session file ONCE: the object it validates is the object it watches, and the watch
  document's session sha256 is of those same bytes.

Behaviour-only: records come from Doodle's spec/session.example.json via tests/sec001_records.py; the library is
called through `targets_from_session` (signature adapter: 1 arg on older trees, `(rec, now)` on newer ones) and
the CLI through `optpipe.cli.main`. A failure on a tree without the fix is a watched / honoured / refused
assertion, never an import or signature error.
"""
import copy
import hashlib
import inspect
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from optpipe.watcher.runtime import targets_from_session
from sec001_records import approved, ts, with_position

NOW = datetime(2026, 10, 13, 16, 31, tzinfo=timezone.utc)      # 12:31 New York: inside apr-1's window, after the fill
TAPE = Path(__file__).parent / "fixtures" / "2026-10-13" / "watch_tape.yaml"
CLOCK = "2026-10-13T12:31:00-04:00"


def _targets(rec):
    if "now" in inspect.signature(targets_from_session).parameters:
        return targets_from_session(rec, NOW)
    return targets_from_session(rec)


def _positions(rec):
    return [t for t in _targets(rec) if t.kind == "position"]


def _real_tickets(rec):
    """Ticket targets watched as really approved (approved_pending and not a hypothetical what-if)."""
    return [t for t in _targets(rec) if t.kind == "ticket" and t.status == "approved_pending" and not t.hypothetical]


@pytest.fixture
def positioned(example):
    return with_position(approved(example))


def test_valid_position_is_watched_open(positioned):
    """Control: an approved, in-window position is watched as open."""
    ps = _positions(positioned)
    assert [(t.target_id, t.status) for t in ps] == [("pos-1", "open")]


def test_valid_approval_is_really_approved(positioned):
    """Control for the approval cases below: the untouched approval IS honoured, so 'not honoured' there is the
    field under test, not a record that could never be approved."""
    assert len(_real_tickets(positioned)) == 1


def _revoked_before_fill(ex):
    return with_position(approved(ex, status="revoked", status_changed_at=ts("12:25")))


def _fill_before_approval(ex):
    return with_position(approved(ex), fill_at="12:10")


def _unknown_approval(ex):
    r = with_position(approved(ex))
    r["paper_positions"][0]["approval_id"] = "apr-missing"
    return r


def _fill_after_expiry(ex):
    return with_position(approved(ex), fill_at="16:20")


@pytest.mark.parametrize("build", [_revoked_before_fill, _fill_before_approval, _unknown_approval, _fill_after_expiry],
                         ids=["revoked_before_fill", "fill_before_approved_at", "unknown_approval_id", "fill_after_expires_at"])
def test_position_with_inv06_errors_not_watched_open(example, build):
    rec = build(copy.deepcopy(example))
    ps = _positions(rec)
    assert ps, "the position is still listed (as not watched), not silently dropped"
    for t in ps:
        assert t.status != "open" and not t.watched, f"{t.target_id} watched as {t.status!r} despite INV-04/06: {t.note}"


def _set(path, value):
    def f(r):
        a = r["approvals"][0]
        if value is _DROP:
            a.pop(path)
        else:
            a[path] = value
        return r
    return f


_DROP = object()


@pytest.mark.parametrize("field,value", [
    ("approved_at", "2026-10-13T12:20:00-04:00"),       # a bare string, not a TsPair object
    ("approved_at", {"ny": "2026-10-13T12:20:00", "berlin": "2026-10-13T18:20:00"}),   # naive
    ("expires_at", 1760385600),
    ("expires_at", _DROP),
    ("status", "ACTIVE"),
    ("status", _DROP),
    ("status", 1),
], ids=["approved_at=str", "approved_at=naive", "expires_at=int", "expires_at=missing",
        "status=ACTIVE", "status=missing", "status=1"])
def test_targets_from_session_schema_invalid_approval_not_honoured(example, field, value):
    rec = _set(field, value)(approved(copy.deepcopy(example)))
    try:
        real = _real_tickets(rec)                          # must not raise (AttributeError / TypeError)
    except Exception as e:                                 # noqa: BLE001
        pytest.fail(f"targets_from_session crashed on {field}={value!r}: {type(e).__name__}: {e}")
    assert not real, f"approval with {field}={value!r} honoured as a real approval: {[t.note for t in real]}"


# -- the watch CLI parses the file once
def _serve(monkeypatch, path: Path, first: bytes, later: bytes):
    """Make every read of `path` return `first` on the first read and `later` on any read after it."""
    n = {"reads": 0}
    real_bytes, real_text = Path.read_bytes, Path.read_text

    def pick(self):
        n["reads"] += 1
        return first if n["reads"] == 1 else later

    def rb(self):
        return pick(self) if Path(self).resolve() == path.resolve() else real_bytes(self)

    def rt(self, *a, **k):
        return pick(self).decode() if Path(self).resolve() == path.resolve() else real_text(self, *a, **k)
    monkeypatch.setattr(Path, "read_bytes", rb)
    monkeypatch.setattr(Path, "read_text", rt)
    return n


def _watch(sess: Path, out: Path):
    from optpipe.cli import main
    return main(["watch", "--session", str(sess), "--replay", str(TAPE), "--once", "--clock", CLOCK, "--out", str(out)])


def _enc(rec):
    return (json.dumps(rec, indent=2) + "\n").encode()


def test_watch_validates_the_bytes_it_watches(tmp_path, example, monkeypatch, capsys):
    """The first read is a record that must be refused (a position opened on an approval revoked before the fill);
    later reads are a clean record. Watching the first object while validating a re-read must not happen."""
    good, bad = _enc(with_position(approved(copy.deepcopy(example)))), _enc(_revoked_before_fill(copy.deepcopy(example)))
    sess = tmp_path / "session-2026-10-13.json"
    sess.write_bytes(good)
    _serve(monkeypatch, sess, bad, good)
    rc = _watch(sess, tmp_path / "out")
    cap = capsys.readouterr()
    assert rc == 2 and "refusing to watch" in cap.err, \
        f"watched a record whose validated bytes were a different read (exit {rc}):\n{cap.out[-600:]}{cap.err[-600:]}"


def test_watch_document_hash_is_of_the_watched_bytes(tmp_path, example, monkeypatch, capsys):
    """The first read is clean; any re-read differs. The watch runs on the first read and records ITS sha256."""
    good, bad = _enc(with_position(approved(copy.deepcopy(example)))), _enc(_revoked_before_fill(copy.deepcopy(example)))
    sess = tmp_path / "session-2026-10-13.json"
    sess.write_bytes(good)
    _serve(monkeypatch, sess, good, bad)
    out = tmp_path / "out"
    rc = _watch(sess, out)
    cap = capsys.readouterr()
    assert rc == 0, f"the clean first read was not what got validated (exit {rc}):\n{cap.err[-800:]}"
    doc = json.loads(next(out.glob("*.watch.json")).read_bytes())
    assert doc["session"]["sha256"] == hashlib.sha256(good).hexdigest(), "watch document hashes a re-read, not the watched bytes"
