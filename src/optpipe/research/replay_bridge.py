"""Replay Bridge 001: export the frozen Lesson 001 v3 decision tape to offline research CSVs.

PAPER ONLY / DECISION SUPPORT ONLY. SYNTHETIC fixture data. No broker, fill model, market-data access, optimizer,
entries, exits or P&L. Optpipe owns the decisions; this module copies them and never raises an action.

Origin: Sol's standalone prototype (Optpipe-Replay-Bridge-001.zip, sha256 dd6a0815...fb220, replay_bridge.py
sha256 8bb3ed3c...056f), with the fixes from Zoodle's test report and Noodle's review folded in. See
docs/replay_bridge_001.md for the fix list and the inner-tamper disposition table.

Decision-time price freshness follows Doodle's PROPOSED spec 1.1.9 policy (trading/proposals/
ReplayBridge-001-price-freshness.md, sha256 fc50b0e1...7619). The numbers are read from a policy file
(config/replay_bridge_price_policy.json by default), never hard-coded here.
TODO(Doodle): the policy values are PROPOSED and pending Eugene's answers. Doodle owns them. When the policy is
missing or any value is unset, the bridge refuses to export (fail closed, exit 2).

Outputs (one new folder, written atomically: all five files land together or none do):
  decision_tape.csv           Sol's 10-column projection (byte-identical for the frozen tape)
  decision_tape_labeled.csv   the same rows plus paper-only / decision-support labels, fixture set + hash, price status
  price_block_log.csv         one record per blocked decision-time price (header only when none), plus labels
  bridge_receipt.json         hashes, counts, policy, code hash, command, timestamp
  SHA256SUMS                  sha256 of the four files above
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import os
import platform
import shlex
import shutil
import sys
import tempfile
import zipfile
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path

from optpipe import nyse_calendar as cal

ARCHIVE_SHA = "159de19c0fa74be03a46da954af92f8f1f164a62e0c439f0a57aa8bb1c3947a3"   # frozen Lesson 001 v3 bundle
FROZEN_DECISIONS_SHA = "659175c6eb8a31ea3bebb53d71a27d462557ec0b62aa2494efab2918e71804f6"  # its decisions.json
FIXTURE_SET = "lesson001-portability-v3"
BASE = "out/lesson001/"
MODULE_CMD = "python -m optpipe.research.replay_bridge"
POLICY_FILE = Path(__file__).resolve().parents[3] / "config" / "replay_bridge_price_policy.json"

PAPER_ONLY = "PAPER_ONLY"
DECISION_SUPPORT = "DECISION_SUPPORT_ONLY_NOT_AN_ENTRY_OR_FILL"
ACTIONS = ("ABSTAIN", "BLOCKED", "SIGNAL_BEAR", "SIGNAL_BULL")
SIGNALS = {"SIGNAL_BULL": "bull", "SIGNAL_BEAR": "bear"}
INDICATORS = frozenset({"buffer", "noise_lower", "noise_upper", "price", "range_high", "range_low",
                        "session_open", "vwap"})
INDICATOR_KEYS = frozenset({"as_of", "source_id", "status", "value"})
COMMON_GATES = ("calendar", "daily_signal_available", "entry_definitions", "fresh_input", "opening_range_complete",
                "scan")
DIRECTION_GATES = ("noise", "range", "vwap")
EXIT = {"action": "BLOCKED", "status": "Unknown"}
CADENCE = timedelta(seconds=60)
PRICE_KINDS = ("bar_close_1m", "quote_last")

FIELDS = ("decision_at_ny", "decision_at_berlin", "symbol", "observed_price_at_ny",
          "observed_price", "decision", "bull_signal", "bear_signal", "exit_state", "source_id")
LABEL_FIELDS = ("paper_only", "decision_support", "fixture_set", "fixture_set_sha256")
LABELED_FIELDS = FIELDS + ("upstream_decision", "price_kind", "price_status", "price_reason_code",
                           "price_age_seconds", "price_max_age_seconds") + LABEL_FIELDS
LOG_FIELDS = ("row_index", "decision_at_ny", "symbol", "price_kind", "status", "as_of_raw", "age_seconds",
              "max_age_seconds", "reason_code", "upstream_action", "final_action", "source_id", "kind") + LABEL_FIELDS
REASONS = ("PF-KIND", "PF-STATUS", "PF-TIME-MISSING", "PF-TIME-NAIVE", "PF-TIME-INVALID", "PF-CALENDAR",
           "PF-CLOSED", "PF-FUTURE", "PF-SESSION", "PF-STALE", "PF-VALUE", "PF-BIDASK")
OUTPUT_FILES = ("decision_tape.csv", "decision_tape_labeled.csv", "price_block_log.csv", "bridge_receipt.json")


class TapeError(ValueError):
    """A tape-integrity failure: the whole export is refused (exit 2)."""


class PolicyError(ValueError):
    """The price policy is missing or incomplete: fail closed (exit 2)."""


def digest(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


# ------------------------------------------------------------------------------------------- price policy
def _seconds(v, name: str) -> Decimal:
    if v is None or isinstance(v, bool) or not isinstance(v, (str, int)):
        raise PolicyError(f"price policy {name} is not set (fail closed; Doodle owns the value)")
    try:
        d = Decimal(str(v))
    except InvalidOperation:
        raise PolicyError(f"price policy {name} is not a number: {v!r}") from None
    if not d.is_finite() or d < 0:
        raise PolicyError(f"price policy {name} must be a finite number >= 0: {v!r}")
    return d


def load_policy(raw) -> dict:
    """Validate a policy mapping. Missing keys or null values raise PolicyError (fail closed)."""
    if not isinstance(raw, dict):
        raise PolicyError("price policy is not configured (fail closed; Doodle owns the values)")
    pid = raw.get("policy_id")
    if not isinstance(pid, str) or not pid:
        raise PolicyError("price policy policy_id is not set")
    return {
        "policy_id": pid,
        "status": str(raw.get("status", "UNKNOWN")),
        "max_age_seconds": {k: _seconds((raw.get(k) or {}).get("max_age_seconds"), f"{k}.max_age_seconds")
                            for k in PRICE_KINDS},
        "future_tolerance_seconds": _seconds(raw.get("future_tolerance_seconds"), "future_tolerance_seconds"),
    }


def read_policy(path: Path) -> tuple[dict, str]:
    try:
        data = Path(path).read_bytes()
    except OSError as e:
        raise PolicyError(f"price policy is not configured: {e.strerror}: {path} (fail closed)") from None
    try:
        raw = json.loads(data)
    except ValueError:
        raise PolicyError(f"price policy is not valid JSON: {path}") from None
    return load_policy(raw), digest(data)


def _fmt(d: Decimal | None) -> str:
    if d is None:
        return ""
    s = format(d, "f")
    return s.rstrip("0").rstrip(".") if "." in s else s


def _age(decision: datetime, as_of: datetime) -> Decimal:
    """D_utc - as_of_utc in exact decimal seconds. Both converted to UTC first (never same-tzinfo wall subtraction)."""
    td = decision.astimezone(UTC) - as_of.astimezone(UTC)
    us = (td.days * 86400 + td.seconds) * 1_000_000 + td.microseconds
    return Decimal(us) / Decimal(1_000_000)


def _num(v) -> Decimal | None:
    if v is None or isinstance(v, bool) or not isinstance(v, (str, int)):
        return None
    try:
        d = Decimal(str(v))
    except InvalidOperation:
        return None
    return d if d.is_finite() else None


def evaluate_price(price: dict, decision: datetime, price_kind: str | None, policy: dict) -> dict:
    """Doodle's PROPOSED checks 1-12, fixed order, first failure wins. Never raises for a bad price.

    Returns {"ok", "reason", "age", "max_age", "value"}; age is recorded whenever both instants parse."""
    out = {"ok": False, "reason": None, "age": None, "max_age": None, "value": None}

    def block(code):
        out["reason"] = code
        return out

    raw_as_of = price.get("as_of")
    as_of = None
    if isinstance(raw_as_of, str) and raw_as_of:
        try:
            as_of = datetime.fromisoformat(raw_as_of)
        except ValueError:
            as_of = None
    if as_of is not None and as_of.utcoffset() is not None:
        out["age"] = _age(decision, as_of)
    if price_kind not in PRICE_KINDS:                                             # 1
        return block("PF-KIND")
    out["max_age"] = policy["max_age_seconds"][price_kind]
    if price.get("status") != "ok":                                               # 2 (case-sensitive)
        return block("PF-STATUS")
    if not isinstance(raw_as_of, str) or not raw_as_of:                           # 3
        return block("PF-TIME-MISSING")
    if as_of is not None and as_of.utcoffset() is None:                           # 4
        return block("PF-TIME-NAIVE")
    if as_of is None:                                                             # 5
        return block("PF-TIME-INVALID")
    d_ny = decision.astimezone(cal.NY)
    if not cal.covers(d_ny.date()):                                               # 6
        return block("PF-CALENDAR")
    close = cal.close_at(d_ny.date())
    if close is None or not (cal.ny_at(d_ny.date(), cal.OPEN) <= d_ny <= close):  # 7 (both ends inclusive)
        return block("PF-CLOSED")
    if -out["age"] > policy["future_tolerance_seconds"]:                          # 8
        return block("PF-FUTURE")
    a_ny = as_of.astimezone(cal.NY)
    if a_ny.date() != d_ny.date() or a_ny < cal.ny_at(d_ny.date(), cal.OPEN):     # 9
        return block("PF-SESSION")
    if out["age"] > out["max_age"]:                                               # 10 (inclusive limit)
        return block("PF-STALE")
    value = _num(price.get("value"))
    if value is None or value <= 0:                                               # 11
        return block("PF-VALUE")
    if "bid" in price or "ask" in price:                                          # 12
        bid, ask = _num(price.get("bid")), _num(price.get("ask"))
        if bid is None or ask is None or bid <= 0 or ask <= 0 or ask < bid:
            return block("PF-BIDASK")
    out["ok"], out["value"] = True, value
    return out


# ------------------------------------------------------------------------------------------- tape integrity
def _instant(s, what: str) -> datetime:
    try:
        t = datetime.fromisoformat(s)
    except (TypeError, ValueError):
        raise TapeError(f"invalid decision clock {what}") from None
    if t.utcoffset() is None:
        raise TapeError("invalid decision clock order or offset (naive)")
    return t


def _check_row(row: dict, receipt: dict, source_id: str) -> None:
    provenance = row["provenance"]
    if (row["mode"] != "paper_signal_only" or provenance["kind"] != "SYNTHETIC"
            or {k: row["exit"].get(k) for k in EXIT} != EXIT or row["schema_version"] != receipt["schema_version"]):
        raise TapeError("unsupported provenance, mode, schema or exit state")
    if row["configuration"].get("fixture_assumptions_enabled") is not True:
        raise TapeError("fixture assumptions not enabled on row")
    if row["action"] not in ACTIONS:
        raise TapeError("unsupported decision")
    if provenance["source_id"] != source_id:
        raise TapeError("provenance source_id differs between rows")
    ind = row["indicators"]
    if set(ind) != INDICATORS:
        raise TapeError(f"unexpected indicator set: {sorted(set(ind) ^ INDICATORS)}")
    for name, v in ind.items():
        if v.get("source_id") != source_id:
            raise TapeError(f"indicator {name} source_id mismatch")
        if name != "price" and set(v) != INDICATOR_KEYS:
            raise TapeError(f"indicator {name} has unexpected keys")
    if not str(row.get("explanation", "")).startswith(f"{row['action']} at {row['clock']['ny']}."):
        raise TapeError("explanation does not name the row's action and clock")
    direction = SIGNALS.get(row["action"])
    if direction:
        conds = row["conditions"]
        gates = list(COMMON_GATES) + [f"{direction}_{g}" for g in DIRECTION_GATES]
        failed = [g for g in gates if conds.get(g) != "Pass"]
        if failed:
            raise TapeError(f"signal without passing gates: {failed}")
        missing = sorted(n for n, v in ind.items() if n != "price" and v.get("status") != "ok")
        if missing:
            raise TapeError(f"signal with non-ok inputs: {missing}")


def project(raw: bytes, receipt: dict, policy: dict, *, fixture_sha: str, price_kind: str | None = None):
    """Validate the tape and build the three CSVs. Returns (files: dict[name, bytes], summary: dict)."""
    if receipt.get("fixture_assumptions") is not True or receipt.get("kind") != "SYNTHETIC":
        raise TapeError("expected explicitly enabled synthetic fixture")
    if digest(raw) != receipt.get("decisions_sha256"):
        raise TapeError("decisions do not match frozen receipt")
    try:
        tape = json.loads(raw)
    except ValueError:
        raise TapeError("decisions.json is not valid JSON") from None
    if not isinstance(tape, list) or not tape or len(tape) != receipt.get("count"):
        raise TapeError("decisions do not match frozen receipt (count)")
    if price_kind is None and digest(raw) == FROZEN_DECISIONS_SHA:
        price_kind = "bar_close_1m"     # set by the bridge for the frozen archive only (policy 3.1)

    labels = (PAPER_ONLY, DECISION_SUPPORT, FIXTURE_SET, fixture_sha)
    bufs = {n: io.StringIO(newline="") for n in OUTPUT_FILES[:3]}
    w = {n: csv.writer(b, lineterminator="\n") for n, b in bufs.items()}
    w["decision_tape.csv"].writerow(FIELDS)
    w["decision_tape_labeled.csv"].writerow(LABELED_FIELDS)
    w["price_block_log.csv"].writerow(LOG_FIELDS)

    source_id, symbol = tape[0]["provenance"]["source_id"], tape[0]["symbol"]
    first = _instant(tape[0]["clock"]["ny"], "ny")
    day = first.astimezone(cal.NY).date()
    if not cal.is_session(day):
        raise TapeError("tape date is not a covered NYSE session")
    if first != cal.ny_at(day, cal.OPEN):
        raise TapeError("tape does not start at the session open")
    upstream = dict.fromkeys(ACTIONS, 0)
    final = dict.fromkeys(ACTIONS, 0)
    blocks = dict.fromkeys(REASONS, 0)
    last, signal_seen = None, False
    for i, row in enumerate(tape):
        try:
            clock = row["clock"]
            ny, berlin = _instant(clock["ny"], "ny"), _instant(clock["berlin"], "berlin")
            if ny != berlin or (last is not None and ny <= last):
                raise TapeError("invalid decision clock order or offset")
            if last is not None and ny - last != CADENCE:
                raise TapeError(f"decision clock gap at row {i} (expected one row per minute)")
            if row["symbol"] != symbol:
                raise TapeError("symbol changes within the tape")
            _check_row(row, receipt, source_id)
            if row["signal_used_before"] is not signal_seen:
                raise TapeError(f"signal_used_before inconsistent at row {i}")
            action = row["action"]
            if action in SIGNALS:
                if signal_seen:
                    raise TapeError("second signal in one session")
                signal_seen = True
            price = row["indicators"]["price"]
            kind = price.get("price_kind", price_kind)
        except (KeyError, TypeError, AttributeError) as e:
            raise TapeError(f"malformed tape row {i}: {type(e).__name__} {e}") from None
        last = ny
        res = evaluate_price(price, ny, kind, policy)
        final_action = action if res["ok"] or action not in SIGNALS else "BLOCKED"   # never promote; downgrade only
        upstream[action] += 1
        final[final_action] += 1
        seen = price["as_of"] if res["ok"] else ""
        value = format(res["value"], "f") if res["ok"] else ""
        base = (clock["ny"], clock["berlin"], symbol, seen, value, final_action, int(final_action == "SIGNAL_BULL"),
                int(final_action == "SIGNAL_BEAR"), "BLOCKED", source_id)
        w["decision_tape.csv"].writerow(base)
        w["decision_tape_labeled.csv"].writerow(base + (
            action, kind or "", "OK" if res["ok"] else "BLOCKED", res["reason"] or "", _fmt(res["age"]),
            _fmt(res["max_age"]), *labels))
        if not res["ok"]:
            blocks[res["reason"]] += 1
            as_of_raw = price.get("as_of")
            w["price_block_log.csv"].writerow((
                i, clock["ny"], symbol, kind or "", "" if price.get("status") is None else str(price.get("status")),
                "" if as_of_raw is None else str(as_of_raw), _fmt(res["age"]), _fmt(res["max_age"]), res["reason"],
                action, final_action, source_id, row["provenance"]["kind"], *labels))
    close = cal.close_at(day)
    if last != close:
        raise TapeError("tape does not end at the session close")
    files = {n: b.getvalue().encode("utf-8") for n, b in bufs.items()}
    total = sum(blocks.values())
    summary = {
        "decisions_sha256": digest(raw),
        "rows": len(tape),
        "counts": final,
        "upstream_counts": upstream,
        "price_blocks": {"total": total, "by_reason": {k: v for k, v in blocks.items() if v}},
        "price_kind_default": price_kind,
        "kind": "SYNTHETIC",
        "labels": dict(zip(LABEL_FIELDS, labels)),
        "scope": "decision projection only; no entries, fills, exits or P&L simulated",
    }
    return files, summary


# ------------------------------------------------------------------------------------------- archive + output
def convert(bundle: Path, policy: dict, *, archive_sha: str = ARCHIVE_SHA):
    raw = Path(bundle).read_bytes()
    if digest(raw) != archive_sha:
        raise TapeError("wrong frozen Lesson 001 v3 archive")
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        try:
            decisions = archive.read(BASE + "decisions.json")
            receipt = json.loads(archive.read(BASE + "receipt.json"))
        except KeyError as e:
            raise TapeError(f"archive member missing: {e}") from None
    files, summary = project(decisions, receipt, policy, fixture_sha=archive_sha)
    return files, {**summary, "archive_sha256": archive_sha, "fixture_set": FIXTURE_SET}


def _write(path: Path, data: bytes) -> None:
    with open(path, "xb") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())


def sha256sums(files: dict) -> bytes:
    return "".join(f"{digest(files[n])}  {n}\n" for n in sorted(files)).encode()


def write_output(out: Path, files: dict) -> None:
    """All files land together or none do: build in a temp dir beside `out`, then one rename.
    An existing `out` (even empty) is refused before anything is written."""
    out = Path(out)
    if out.exists() or out.is_symlink():
        raise FileExistsError(17, "output folder already exists", str(out))
    tmp = Path(tempfile.mkdtemp(prefix=f".{out.name}.partial-", dir=out.parent))
    try:
        os.chmod(tmp, 0o755)                         # mkdtemp makes 0700; the output is an ordinary folder
        for name, data in files.items():
            _write(tmp / name, data)
        _write(tmp / "SHA256SUMS", sha256sums(files))
        if out.exists() or out.is_symlink():          # re-check just before the rename
            raise FileExistsError(17, "output folder already exists", str(out))
        os.rename(tmp, out)
    except BaseException:
        shutil.rmtree(tmp, ignore_errors=True)
        raise


def export(bundle: Path, out: Path, policy_path: Path, *, command: str, now=None,
           archive_sha: str = ARCHIVE_SHA) -> dict:
    policy, policy_sha = read_policy(policy_path)
    files, summary = convert(bundle, policy, archive_sha=archive_sha)
    stamp = (now or datetime.now(UTC)).astimezone(UTC).isoformat(timespec="seconds")
    receipt = {
        **summary,
        "csv_sha256": digest(files["decision_tape.csv"]),
        "labeled_csv_sha256": digest(files["decision_tape_labeled.csv"]),
        "price_block_log_sha256": digest(files["price_block_log.csv"]),
        "price_policy": {"policy_id": policy["policy_id"], "status": policy["status"], "file_sha256": policy_sha,
                         "max_age_seconds": {k: _fmt(v) for k, v in policy["max_age_seconds"].items()},
                         "future_tolerance_seconds": _fmt(policy["future_tolerance_seconds"]),
                         "age": "decision_utc - as_of_utc, inclusive limits, first failure wins"},
        "code_sha256": digest(Path(__file__).read_bytes()),
        "command": command,
        "python": platform.python_version(),
        "generated_at_utc": stamp,
    }
    files = {**files, "bridge_receipt.json": (json.dumps(receipt, sort_keys=True, indent=2) + "\n").encode()}
    write_output(out, files)
    return receipt


def verify(out: Path) -> dict:
    """Check an output folder: every file present, nothing extra, SHA256SUMS and the receipt agree with the bytes."""
    out = Path(out)
    expected = set(OUTPUT_FILES) | {"SHA256SUMS"}
    present = {p.name for p in out.iterdir()} if out.is_dir() else set()
    if present != expected:
        raise TapeError(f"output incomplete or unexpected files: missing {sorted(expected - present)}, "
                        f"extra {sorted(present - expected)}")
    files = {n: (out / n).read_bytes() for n in OUTPUT_FILES}
    if (out / "SHA256SUMS").read_bytes() != sha256sums(files):
        raise TapeError("SHA256SUMS does not match the files")
    try:
        receipt = json.loads(files["bridge_receipt.json"])
    except ValueError:
        raise TapeError("receipt is not valid JSON") from None
    for key, name in (("csv_sha256", "decision_tape.csv"), ("labeled_csv_sha256", "decision_tape_labeled.csv"),
                      ("price_block_log_sha256", "price_block_log.csv")):
        if receipt.get(key) != digest(files[name]):
            raise TapeError(f"receipt {key} does not match {name}")
    rows = list(csv.reader(io.StringIO(files["decision_tape_labeled.csv"].decode())))
    log = list(csv.reader(io.StringIO(files["price_block_log.csv"].decode())))
    if len(rows) - 1 != receipt.get("rows") or len(log) - 1 != receipt.get("price_blocks", {}).get("total"):
        raise TapeError("receipt row or block counts do not match the CSVs")
    got = receipt.get("labels") if isinstance(receipt.get("labels"), dict) else {}
    labels = [got.get(k) for k in LABEL_FIELDS]
    for r in rows[1:] + log[1:]:
        if r[-len(LABEL_FIELDS):] != labels or labels[:2] != [PAPER_ONLY, DECISION_SUPPORT]:
            raise TapeError("a CSV row lacks the paper-only / decision-support labels")
    return receipt


def main(argv=None, *, _archive_sha: str = ARCHIVE_SHA) -> int:
    """CLI. `_archive_sha` is a test hook only; the command line has no way to change the pin."""
    p = argparse.ArgumentParser(prog=MODULE_CMD, description=__doc__.split("\n\n")[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    e = sub.add_parser("export", help="verify the frozen bundle and write a NEW output folder")
    e.add_argument("bundle", type=Path)
    e.add_argument("out", type=Path, help="new output directory (must not exist)")
    e.add_argument("--price-policy", type=Path, default=POLICY_FILE,
                   help="price freshness policy JSON (default: config/replay_bridge_price_policy.json)")
    v = sub.add_parser("verify", help="check an output folder against its receipt and SHA256SUMS")
    v.add_argument("out", type=Path)
    args_in = list(argv) if argv is not None else None
    a = p.parse_args(args_in)
    raw_args = args_in if args_in is not None else sys.argv[1:]
    try:
        if a.cmd == "verify":
            r = verify(a.out)
            print(f"Verified output: {r['rows']} rows, {r['price_blocks']['total']} price blocks. Paper only.")
            return 0
        r = export(a.bundle, a.out, a.price_policy, command=f"{MODULE_CMD} {shlex.join(raw_args)}",
                   archive_sha=_archive_sha)
    except (OSError, KeyError, ValueError, InvalidOperation, zipfile.BadZipFile) as err:
        print(f"bridge: {err}", file=sys.stderr)
        return 2
    print(f"Verified and exported {r['rows']} synthetic decision rows; {r['price_blocks']['total']} decision-time "
          f"price(s) blocked. Paper only, decision support only. No trades simulated.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
