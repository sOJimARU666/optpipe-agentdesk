"""Replay Bridge 001: export the frozen Lesson 001 v3 decision tape to offline research CSVs.

PAPER ONLY / DECISION SUPPORT ONLY. SYNTHETIC fixture data. No broker, fill model, market-data access, optimizer,
entries, exits or P&L. Optpipe owns the decisions; this module copies them and never raises an action.

Origin: Sol's standalone prototype (Optpipe-Replay-Bridge-001.zip, sha256 dd6a0815...fb220, replay_bridge.py
sha256 8bb3ed3c...056f), with the fixes from Zoodle's test report and Noodle's review folded in. See
docs/replay_bridge_001.md for the fix list and the inner-tamper disposition table.

Decision-time price freshness follows Doodle's PROPOSED spec 1.1.9 policy (trading/proposals/
ReplayBridge-001-price-freshness.md, sha256 805f2681...e246, which replaces fc50b0e1...7619). The numbers are
read from a policy file (config/replay_bridge_price_policy.json by default), never hard-coded here.
TODO(Doodle): the policy values are PROPOSED for spec 1.1.9 (Eugene's answers relayed by Soodle). Doodle owns them.
When the policy is missing or any value is unset, the bridge refuses to export (fail closed, exit 2).

Outputs (one new folder, written atomically: all six files land together or none do):
  decision_tape.csv           Sol's 10-column projection (byte-identical for the frozen tape)
  decision_tape_labeled.csv   the same rows plus paper-only / decision-support labels, fixture set + hash, price status,
                              price label (BAR / REALTIME / DELAYED), delay and adjusted age
  price_block_log.csv         one record per blocked decision-time price (header only when none), plus labels
  price_accept_log.csv        one record per accepted decision-time price (header only when none), plus labels
  bridge_receipt.json         hashes, counts, policy, code hash, command, timestamp
  SHA256SUMS                  sha256 of the five files above
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import math
import os
import platform
import shlex
import shutil
import signal
import sys
import tempfile
import zipfile
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path

from optpipe import nyse_calendar as cal

ARCHIVE_SHA = "159de19c0fa74be03a46da954af92f8f1f164a62e0c439f0a57aa8bb1c3947a3"   # frozen Lesson 001 v3 bundle
FROZEN_DECISIONS_SHA = "659175c6eb8a31ea3bebb53d71a27d462557ec0b62aa2494efab2918e71804f6"  # its decisions.json
FROZEN_CSV_SHA = "039ee5883a49fbd82e74a921bf18d6c2a551b52f7adfdda5a01d35ff0b58de24"  # Sol's evidence tape
FIXTURE_SET = "lesson001-portability-v3"
UNOFFICIAL_FIXTURE_SET = "test-fixture"  # used when export is not the pinned archive
BASE = "out/lesson001/"
MODULE_CMD = "python -m optpipe.research.replay_bridge"
POLICY_FILE = Path(__file__).resolve().parents[3] / "config" / "replay_bridge_price_policy.json"
REPO_ROOT = POLICY_FILE.parent.parent
OFFICIAL_FIXTURE_DIR = REPO_ROOT / "tests" / "fixtures" / "lesson001_v3"
# Copied into the receipt and printed on export/verify.
# Not an approval switch: there is no way to mark a policy approved.
POLICY_STATUS_NOTE = ("A policy with status PROPOSED is not owner-approved. This receipt records the status "
                      "declared by the policy file; it has no approval mechanism.")
VERIFY_SELF_ONLY = "self-consistency only"
VERIFY_WITH_PINS = "pinned"
IDENTITY_PINS = ("archive_sha256", "decisions_sha256", "csv_sha256", "code_sha256", "policy_file_sha256")
# Sidecar columns that cannot be derived from decision_tape.csv alone (plain-tape projection).
SIDECAR_DIAGNOSTIC_FIELDS = (
    "upstream_decision", "price_kind", "price_label", "price_reason_code",
    "price_age_seconds", "price_delay_seconds", "price_adjusted_age_seconds", "price_max_age_seconds",
    "as_of_raw", "status", "kind", "label",
)
PARTIAL_MARK = ".partial-"  # hidden temp folder prefix used by write_output

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
DELAY_FIELDS = ("delayed", "delay_minutes", "delay_seconds")
MIN_DELAY_SECONDS = 60                      # MD-08: delayed:true needs delay_minutes >= 1

FIELDS = ("decision_at_ny", "decision_at_berlin", "symbol", "observed_price_at_ny",
          "observed_price", "decision", "bull_signal", "bear_signal", "exit_state", "source_id")
LABEL_FIELDS = ("paper_only", "decision_support", "fixture_set", "fixture_set_sha256")
LABELED_FIELDS = FIELDS + ("upstream_decision", "price_kind", "price_label", "price_status", "price_reason_code",
                           "price_age_seconds", "price_delay_seconds", "price_adjusted_age_seconds",
                           "price_max_age_seconds") + LABEL_FIELDS
LOG_FIELDS = ("row_index", "decision_at_ny", "symbol", "price_kind", "label", "status", "as_of_raw", "age_seconds",
              "delay_seconds", "adjusted_age_seconds", "max_age_seconds", "reason_code", "upstream_action",
              "final_action", "source_id", "kind") + LABEL_FIELDS
ACCEPT_FIELDS = ("row_index", "decision_at_ny", "symbol", "price_kind", "label", "status", "as_of", "age_seconds",
                 "delay_seconds", "adjusted_age_seconds", "max_age_seconds", "source_id", "kind",
                 "action") + LABEL_FIELDS
# Doodle's check order (first failure wins). PF-DELAY-DECL sits between PF-SESSION and PF-STALE.
REASONS = ("PF-KIND", "PF-STATUS", "PF-TIME-MISSING", "PF-TIME-NAIVE", "PF-TIME-INVALID", "PF-CALENDAR",
           "PF-CLOSED", "PF-FUTURE", "PF-SESSION", "PF-DELAY-DECL", "PF-STALE", "PF-VALUE", "PF-BIDASK")
OUTPUT_FILES = ("decision_tape.csv", "decision_tape_labeled.csv", "price_block_log.csv", "price_accept_log.csv",
                "bridge_receipt.json")
CSV_FILES = OUTPUT_FILES[:4]


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


def _mapping(v, name: str) -> dict:
    """A policy section must be a JSON object. A string, list or other type is PolicyError (exit 2), not a crash."""
    if not isinstance(v, dict):
        raise PolicyError(f"price policy {name} must be an object (fail closed)")
    return v


def load_policy(raw) -> dict:
    """Validate a policy mapping. Missing keys, wrong types or null values raise PolicyError (fail closed)."""
    if not isinstance(raw, dict):
        raise PolicyError("price policy is not configured (fail closed; Doodle owns the values)")
    pid = raw.get("policy_id")
    if not isinstance(pid, str) or not pid:
        raise PolicyError("price policy policy_id is not set")
    delayed = {k: _seconds(_mapping(raw.get("delayed_quote"), "delayed_quote").get(k), f"delayed_quote.{k}")
               for k in ("max_declared_delay_seconds", "absolute_ceiling_seconds", "adjusted_max_seconds")}
    if delayed["max_declared_delay_seconds"] < MIN_DELAY_SECONDS:
        raise PolicyError("price policy delayed_quote.max_declared_delay_seconds must be >= 60 (MD-08)")
    return {
        "policy_id": pid,
        "status": str(raw.get("status", "UNKNOWN")),
        "max_age_seconds": {k: _seconds(_mapping(raw.get(k), k).get("max_age_seconds"), f"{k}.max_age_seconds")
                            for k in PRICE_KINDS},
        "future_tolerance_seconds": _seconds(raw.get("future_tolerance_seconds"), "future_tolerance_seconds"),
        "delayed_quote": delayed,
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


def _delay_declaration(price: dict, policy: dict):
    """Validate a quote's delay declaration (MD-08 / MD-14 shape). Returns (declared, delay_seconds or None).

    declared is False only for `delayed: false`/absent with no delay field (REALTIME). Anything else is a declaration:
    a valid one gives delay_seconds, an invalid one gives None (PF-DELAY-DECL)."""
    present = [k for k in DELAY_FIELDS if k in price]
    if not present or (present == ["delayed"] and price["delayed"] is False):
        return False, None
    if price.get("delayed") is not True:                       # delayed:false with a delay field, or not a real bool
        return True, None
    cap = policy["delayed_quote"]["max_declared_delay_seconds"]
    secs = None
    if "delay_minutes" in price:
        m = price["delay_minutes"]
        if isinstance(m, bool) or not isinstance(m, int) or not (1 <= m <= cap // 60):
            return True, None
        secs = Decimal(m * 60)
    if "delay_seconds" in price:
        d = price["delay_seconds"]
        if isinstance(d, bool) or not isinstance(d, (int, float)) or not math.isfinite(d):
            return True, None
        if d != int(d) or int(d) % 60 or not (MIN_DELAY_SECONDS <= int(d) <= cap):
            return True, None
        if secs is not None and secs != Decimal(int(d)):       # both present and disagreeing
            return True, None
        secs = Decimal(int(d))
    return True, secs


def evaluate_price(price: dict, decision: datetime, price_kind: str | None, policy: dict) -> dict:
    """Doodle's PROPOSED checks 1-15 (policy 805f2681...e246), fixed order, first failure wins. Never raises for a
    bad price. Returns {"ok", "reason", "label", "age", "delay", "adjusted", "max_age", "value"}.

    label is BAR, REALTIME or DELAYED ("" for an unknown price_kind). A DELAYED price keeps its label whether it is
    accepted or blocked; it is never relabeled REALTIME. age is recorded whenever both instants parse; delay and
    adjusted age whenever they can be computed."""
    out = {"ok": False, "reason": None, "label": "", "age": None, "delay": None, "adjusted": None, "max_age": None,
           "value": None}

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
    declared, delay = False, None
    if price_kind == "bar_close_1m":
        out["label"] = "BAR"
        if any(k in price for k in DELAY_FIELDS):                                 # 2 (bars never take DELAYED)
            return block("PF-KIND")
    else:
        declared, delay = _delay_declaration(price, policy)
        out["label"] = "DELAYED" if declared else "REALTIME"
    dq = policy["delayed_quote"]
    out["max_age"] = (dq["absolute_ceiling_seconds"] if declared else policy["max_age_seconds"][price_kind])
    if price.get("status") != "ok":                                               # 3 (case-sensitive)
        return block("PF-STATUS")
    if not isinstance(raw_as_of, str) or not raw_as_of:                           # 4
        return block("PF-TIME-MISSING")
    if as_of is not None and as_of.utcoffset() is None:                           # 5
        return block("PF-TIME-NAIVE")
    if as_of is None:                                                             # 6
        return block("PF-TIME-INVALID")
    d_ny = decision.astimezone(cal.NY)
    if not cal.covers(d_ny.date()):                                               # 7
        return block("PF-CALENDAR")
    close = cal.close_at(d_ny.date())
    if close is None or not (cal.ny_at(d_ny.date(), cal.OPEN) <= d_ny <= close):  # 8 (both ends inclusive)
        return block("PF-CLOSED")
    if -out["age"] > policy["future_tolerance_seconds"]:                          # 9
        return block("PF-FUTURE")
    a_ny = as_of.astimezone(cal.NY)
    if a_ny.date() != d_ny.date() or a_ny < cal.ny_at(d_ny.date(), cal.OPEN):     # 10
        return block("PF-SESSION")
    if declared:
        if delay is None:                                                         # 11 invalid declaration
            return block("PF-DELAY-DECL")
        out["delay"] = delay
        out["adjusted"] = out["age"] - delay
        if out["adjusted"] < 0:                                                   # 12 fresher than its declared delay
            return block("PF-DELAY-DECL")
        if out["age"] > dq["absolute_ceiling_seconds"] or out["adjusted"] > dq["adjusted_max_seconds"]:   # 13
            return block("PF-STALE")
    elif out["age"] > out["max_age"]:                                             # 13 (inclusive limits)
        return block("PF-STALE")
    value = _num(price.get("value"))
    if value is None or value <= 0:                                               # 14
        return block("PF-VALUE")
    if "bid" in price or "ask" in price:                                          # 15
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


def project(raw: bytes, receipt: dict, policy: dict, *, fixture_sha: str, fixture_set: str = FIXTURE_SET,
            price_kind: str | None = None):
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

    labels = (PAPER_ONLY, DECISION_SUPPORT, fixture_set, fixture_sha)
    bufs = {n: io.StringIO(newline="") for n in CSV_FILES}
    w = {n: csv.writer(b, lineterminator="\n") for n, b in bufs.items()}
    w["decision_tape.csv"].writerow(FIELDS)
    w["decision_tape_labeled.csv"].writerow(LABELED_FIELDS)
    w["price_block_log.csv"].writerow(LOG_FIELDS)
    w["price_accept_log.csv"].writerow(ACCEPT_FIELDS)

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
    block_labels: dict[str, int] = {}
    accept_labels: dict[str, int] = {}
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
            action, kind or "", res["label"], "OK" if res["ok"] else "BLOCKED", res["reason"] or "",
            _fmt(res["age"]), _fmt(res["delay"]), _fmt(res["adjusted"]), _fmt(res["max_age"]), *labels))
        ages = (_fmt(res["age"]), _fmt(res["delay"]), _fmt(res["adjusted"]), _fmt(res["max_age"]))
        if res["ok"]:
            accept_labels[res["label"]] = accept_labels.get(res["label"], 0) + 1
            w["price_accept_log.csv"].writerow((
                i, clock["ny"], symbol, kind, res["label"], "ok", seen, *ages, source_id, row["provenance"]["kind"],
                final_action, *labels))
        else:
            blocks[res["reason"]] += 1
            block_labels[res["label"]] = block_labels.get(res["label"], 0) + 1
            as_of_raw = price.get("as_of")
            w["price_block_log.csv"].writerow((
                i, clock["ny"], symbol, kind or "", res["label"],
                "" if price.get("status") is None else str(price.get("status")),
                "" if as_of_raw is None else str(as_of_raw), *ages, res["reason"],
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
        "price_blocks": {"total": total, "by_reason": {k: v for k, v in blocks.items() if v},
                         "by_label": block_labels},
        "price_accepts": {"total": sum(accept_labels.values()), "by_label": accept_labels},
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
    official = archive_sha == ARCHIVE_SHA
    fixture_set = FIXTURE_SET if official else UNOFFICIAL_FIXTURE_SET
    files, summary = project(decisions, receipt, policy, fixture_sha=archive_sha, fixture_set=fixture_set)
    return files, {**summary, "archive_sha256": archive_sha, "fixture_set": fixture_set}


def _write(path: Path, data: bytes) -> None:
    with open(path, "xb") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())


def sha256sums(files: dict) -> bytes:
    return "".join(f"{digest(files[n])}  {n}\n" for n in sorted(files)).encode()


def _policy_display_path(path: Path) -> str:
    """Repo-relative when the file is in the repo; otherwise the basename. Never an absolute box path."""
    path = Path(path)
    try:
        return path.resolve().relative_to(REPO_ROOT.resolve()).as_posix()
    except ValueError:
        return path.name


def _configured_policy_problem() -> str | None:
    """Why the configured policy file cannot be the trust root, or None if it can.

    Reasons are path-free (repo hygiene). Approval status is never taken from a receipt.
    """
    path = POLICY_FILE
    if path.is_dir():
        return "default policy file is a directory"
    if not path.is_file():
        return "default policy file is missing"
    try:
        read_policy(path)
    except PolicyError:
        return "default policy file is invalid"
    except OSError:
        return "default policy file is unreadable"
    return None


def _configured_policy_status() -> str | None:
    """Status of the repo policy file. Approval status is never taken from a receipt."""
    if _configured_policy_problem() is not None:
        return None
    try:
        raw = json.loads(POLICY_FILE.read_bytes())
    except (ValueError, OSError):
        return None
    if not isinstance(raw, dict):
        return None
    status = raw.get("status")
    return status if isinstance(status, str) else None


def _policy_notice(_receipt: dict | None = None) -> str | None:
    """The not-owner-approved notice comes from the configured policy file, never from the folder.

    When the configured file cannot be read, status is reported as unknown and the folder is not approved.
    """
    status = _configured_policy_status()
    if status == "PROPOSED":
        return "Policy status PROPOSED is not owner-approved."
    if status is None:
        return "Policy status is unknown; this folder is not approved."
    return None


def _receipt_policy_from_file(path: Path) -> dict:
    """The price_policy object export() would write for this policy file."""
    policy, policy_sha = read_policy(path)
    return {
        "policy_id": policy["policy_id"], "status": policy["status"],
        "file_sha256": policy_sha, "path": _policy_display_path(path),
        "status_note": POLICY_STATUS_NOTE,
        "max_age_seconds": {k: _fmt(v) for k, v in policy["max_age_seconds"].items()},
        "future_tolerance_seconds": _fmt(policy["future_tolerance_seconds"]),
        "delayed_quote": {k: _fmt(v) for k, v in policy["delayed_quote"].items()},
        "age": "decision_utc - as_of_utc, inclusive limits, first failure wins",
    }


def official_projection(policy: dict | None = None):
    """Recompute the official Lesson 001 sidecars from the vendored frozen fixture and a policy."""
    decisions = OFFICIAL_FIXTURE_DIR / "decisions.json"
    receipt_path = OFFICIAL_FIXTURE_DIR / "receipt.json"
    if not decisions.is_file() or not receipt_path.is_file():
        raise TapeError("required official sidecar pin cannot be resolved: vendored Lesson 001 fixture is missing")
    raw = decisions.read_bytes()
    if digest(raw) != FROZEN_DECISIONS_SHA:
        raise TapeError("vendored Lesson 001 fixture does not match the frozen decisions pin")
    rec = json.loads(receipt_path.read_bytes())
    if policy is None:
        policy, _sha = read_policy(POLICY_FILE)
    return project(raw, rec, policy, fixture_sha=ARCHIVE_SHA, fixture_set=FIXTURE_SET)


def _check_official_sidecars(files: dict, receipt: dict) -> None:
    """Pinned official mode: recompute labeled CSV, logs and receipt policy fields, then compare bytes."""
    if not POLICY_FILE.is_file():
        raise TapeError("required policy pin cannot be resolved: default policy file is missing")
    expected_files, _summary = official_projection()
    for name in ("decision_tape.csv", "decision_tape_labeled.csv", "price_block_log.csv", "price_accept_log.csv"):
        if files[name] != expected_files[name]:
            raise TapeError(f"{name} does not match the projection from the pinned archive and configured policy")
    want = _receipt_policy_from_file(POLICY_FILE)
    got = receipt.get("price_policy") if isinstance(receipt.get("price_policy"), dict) else {}
    for key, val in want.items():
        if got.get(key) != val:
            raise TapeError(f"receipt price_policy.{key} does not match the configured policy")


def is_partial_output(path: Path) -> bool:
    """True for a hidden write_output temp folder (`.name.partial-...`). Always invalid as an output."""
    return Path(path).name.startswith(".") and PARTIAL_MARK in Path(path).name


def write_output(out: Path, files: dict) -> None:
    """All files land together or none do: build in a temp dir beside `out`, then one rename.
    An existing `out` (even empty) is refused before anything is written.

    SIGTERM is caught so the temp folder is removed (A7). SIGKILL cannot be caught and may leave a
    hidden `.name.partial-*` folder; that folder is never a valid output (verify refuses it; export
    of the real target ignores leftovers beside it).
    """
    out = Path(out)
    if is_partial_output(out):
        raise ValueError(f"refusing to write into a hidden partial folder: {out}")
    if out.exists() or out.is_symlink():
        raise FileExistsError(17, "output folder already exists", str(out))
    tmp = Path(tempfile.mkdtemp(prefix=f".{out.name}{PARTIAL_MARK}", dir=out.parent))
    previous = signal.getsignal(signal.SIGTERM)

    def _on_sigterm(_signum, _frame):
        shutil.rmtree(tmp, ignore_errors=True)
        raise SystemExit(2)

    try:
        signal.signal(signal.SIGTERM, _on_sigterm)
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
    finally:
        try:
            signal.signal(signal.SIGTERM, previous)
        except (ValueError, OSError):
            pass


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
        "price_accept_log_sha256": digest(files["price_accept_log.csv"]),
        "price_policy": {"policy_id": policy["policy_id"], "status": policy["status"],
                         "file_sha256": policy_sha, "path": _policy_display_path(policy_path),
                         "status_note": POLICY_STATUS_NOTE,
                         "max_age_seconds": {k: _fmt(v) for k, v in policy["max_age_seconds"].items()},
                         "future_tolerance_seconds": _fmt(policy["future_tolerance_seconds"]),
                         "delayed_quote": {k: _fmt(v) for k, v in policy["delayed_quote"].items()},
                         "age": "decision_utc - as_of_utc, inclusive limits, first failure wins"},
        "code_sha256": digest(Path(__file__).read_bytes()),
        "command": command,
        "python": platform.python_version(),
        "generated_at_utc": stamp,
    }
    files = {**files, "bridge_receipt.json": (json.dumps(receipt, sort_keys=True, indent=2) + "\n").encode()}
    write_output(out, files)
    return receipt


def _index_log(rows):
    out = {}
    for r in rows:
        try:
            i = int(r["row_index"])
        except (KeyError, TypeError, ValueError):
            raise TapeError("a price log row is missing a valid row_index") from None
        if i in out:
            raise TapeError(f"duplicate row_index {i} in a price log")
        out[i] = r
    return out


def _cross_check_sidecars(tape, labeled, log, acc) -> None:
    """Check sidecars against the plain-tape projection, and against each other.

    Derivable from decision_tape.csv: the 10 tape columns, bull/bear flags, exit_state, whether the
    decision-time price was accepted (non-empty observed_price) or blocked, the accept/block partition,
    and log fields that repeat the tape (clock, symbol, source_id, final action, accepted as_of).
    Fields in SIDECAR_DIAGNOSTIC_FIELDS cannot be derived from the tape; they must still agree between
    the labeled CSV and the matching log row.
    """
    accepted = _index_log(acc)
    blocked = _index_log(log)
    if set(accepted) & set(blocked):
        raise TapeError("a row appears in both price_accept_log.csv and price_block_log.csv")
    if set(accepted) | set(blocked) != set(range(len(tape))):
        raise TapeError("price logs do not partition the decision_tape.csv rows")
    for i, (plain, full) in enumerate(zip(tape, labeled, strict=True)):
        action = plain.get(FIELDS[5])  # tape action column (ABSTAIN/BLOCKED/SIGNAL_*), not an approval decision
        if plain.get("bull_signal") != ("1" if action == "SIGNAL_BULL" else "0"):
            raise TapeError(f"bull_signal does not match decision at row {i}")
        if plain.get("bear_signal") != ("1" if action == "SIGNAL_BEAR" else "0"):
            raise TapeError(f"bear_signal does not match decision at row {i}")
        if plain.get("exit_state") != "BLOCKED":
            raise TapeError(f"exit_state is not BLOCKED at row {i}")
        accepted_price = plain.get("observed_price") != ""
        if accepted_price:
            if plain.get("observed_price_at_ny") == "":
                raise TapeError(f"accepted price at row {i} has no observed_price_at_ny")
            if full.get("price_status") != "OK" or full.get("price_reason_code") != "":
                raise TapeError(f"labeled price_status does not match the accepted plain-tape price at row {i}")
            if i not in accepted:
                raise TapeError(f"accepted tape row {i} is missing from price_accept_log.csv")
            rec = accepted[i]
            if rec.get("as_of") != plain.get("observed_price_at_ny"):
                raise TapeError(f"price_accept_log.csv as_of does not match decision_tape.csv at row {i}")
            if rec.get("action") != action:
                raise TapeError(f"price_accept_log.csv action does not match decision_tape.csv at row {i}")
            if full.get("upstream_decision") != action:
                raise TapeError(f"upstream_decision does not match the accepted plain-tape decision at row {i}")
            if rec.get("status") != "ok":
                raise TapeError(f"price_accept_log.csv status is not ok at row {i}")
            _sidecar_pair(i, "accept", plain, full, rec,
                          reason="", label_reason="price_reason_code", log_reason=None,
                          as_of_key="as_of")
        else:
            if plain.get("observed_price_at_ny") != "":
                raise TapeError(f"blocked price at row {i} still has observed_price_at_ny")
            if action in SIGNALS:
                raise TapeError(f"a SIGNAL row on the plain tape has no observed price at row {i}")
            if full.get("price_status") != "BLOCKED" or not full.get("price_reason_code"):
                raise TapeError(f"labeled price_status does not match the blocked plain-tape price at row {i}")
            if i not in blocked:
                raise TapeError(f"blocked tape row {i} is missing from price_block_log.csv")
            rec = blocked[i]
            if rec.get("final_action") != action:
                raise TapeError(f"price_block_log.csv final_action does not match decision_tape.csv at row {i}")
            if action == "ABSTAIN" and full.get("upstream_decision") != "ABSTAIN":
                raise TapeError(f"upstream_decision does not match the ABSTAIN plain-tape decision at row {i}")
            _sidecar_pair(i, "block", plain, full, rec,
                          reason=full.get("price_reason_code"), label_reason="price_reason_code",
                          log_reason="reason_code", as_of_key="as_of_raw")


def _sidecar_pair(i, which, plain, full, rec, *, reason, label_reason, log_reason, as_of_key) -> None:
    if rec.get("decision_at_ny") != plain.get("decision_at_ny") or rec.get("symbol") != plain.get("symbol"):
        raise TapeError(f"price_{which}_log.csv clock/symbol does not match decision_tape.csv at row {i}")
    if rec.get("source_id") != plain.get("source_id"):
        raise TapeError(f"price_{which}_log.csv source_id does not match decision_tape.csv at row {i}")
    if rec.get("price_kind") != full.get("price_kind"):
        raise TapeError(f"price_{which}_log.csv price_kind does not match the labeled CSV at row {i}")
    if rec.get("label") != full.get("price_label"):
        raise TapeError(f"price_{which}_log.csv label does not match the labeled CSV at row {i}")
    if rec.get("age_seconds") != full.get("price_age_seconds"):
        raise TapeError(f"price_{which}_log.csv age does not match the labeled CSV at row {i}")
    if rec.get("delay_seconds") != full.get("price_delay_seconds"):
        raise TapeError(f"price_{which}_log.csv delay does not match the labeled CSV at row {i}")
    if rec.get("adjusted_age_seconds") != full.get("price_adjusted_age_seconds"):
        raise TapeError(f"price_{which}_log.csv adjusted age does not match the labeled CSV at row {i}")
    if rec.get("max_age_seconds") != full.get("price_max_age_seconds"):
        raise TapeError(f"price_{which}_log.csv max age does not match the labeled CSV at row {i}")
    if log_reason and rec.get(log_reason) != full.get(label_reason):
        raise TapeError(f"price_{which}_log.csv reason does not match the labeled CSV at row {i}")
    if reason is not None and log_reason is None and full.get(label_reason) != reason:
        raise TapeError(f"labeled {label_reason} mismatch at row {i}")


def _format_verification(v: dict) -> str:
    checked = ", ".join(v["checked"]) or "none"
    skipped = ", ".join(v["skipped"]) or "none"
    return f"{v['mode']}; checked: {checked}; skipped: {skipped}"


def _pin(got, expected, name: str) -> None:
    if expected is None:
        return
    if not got:
        raise TapeError(f"receipt is missing {name}, cannot check the expected value")
    if got != expected:
        raise TapeError(f"{name} does not match the expected value")


def _count(rows, key, start=1):
    out = dict.fromkeys(ACTIONS, 0)
    for r in rows[start:]:
        v = r[key] if isinstance(r, dict) else None
        if v in out:
            out[v] += 1
    return out


def _by(rows, key, start=1):
    out: dict[str, int] = {}
    for r in rows[start:]:
        v = r[key]
        if v != "":
            out[v] = out.get(v, 0) + 1
    return out


def verify(out: Path, *, expect_archive_sha256: str | None = None, expect_decisions_sha256: str | None = None,
           expect_code_sha256: str | None = None, expect_policy_sha256: str | None = None,
           pinned: bool = False) -> dict:
    """Check an output folder.

    Always (even without pins): SHA256SUMS and receipt file hashes, labels, a row-by-row cross-check of
    decision_tape.csv against decision_tape_labeled.csv, a plain-tape projection check of the sidecars,
    and counts / block / accept totals recomputed from the CSVs (so an edited-and-re-signed receipt is
    refused). Hidden `.*.partial-*` folders are refused. If the receipt claims the official fixture set,
    the archive sha must equal ARCHIVE_SHA. If it records the frozen decisions sha, decision_tape.csv
    must equal FROZEN_CSV_SHA. The recorded code hash is compared to the running source.

    Optional ``--expect-*-sha256`` flags compare archive, decisions, code and policy hashes to the values
    you supply. The expected policy identity comes from the configured policy file or from
    ``--expect-policy-sha256``, never from the receipt. ``pinned=True`` (the CLI default) applies every
    repo pin (ARCHIVE_SHA, FROZEN_DECISIONS_SHA, FROZEN_CSV_SHA, the running source, and the default
    policy file) and refuses if a required pin cannot be resolved. ``--self-consistency-only`` sets
    pinned=False: the archive, decisions and official-sidecar-recompute pins are off, but the
    configured-policy compare still runs. If the configured file is missing, a directory, unreadable
    or invalid, verification is refused in every mode, including self-only and the unpinned API,
    unless ``--expect-policy-sha256`` is given, in which case the receipt's policy hash is compared
    to that sha. The in-memory ``_verification`` result and the CLI output list the pins that were
    checked and the pins that were skipped (including ``official_sidecars`` in every mode), and name
    the sidecar fields that are DIAGNOSTIC (not derivable from the plain tape). The receipt's policy
    identity, status and numbers are compared to the configured policy file in every mode, including
    ``--self-consistency-only``. Identity is pinned to the configured path and content: a
    byte-identical policy copy used via ``--price-policy`` at another path is refused unless
    ``--expect-policy-sha256`` is given. In pinned official mode, the sidecar files and the receipt
    policy fields are also recomputed from the pinned archive plus that configured policy and
    compared byte-for-byte. The not-owner-approved notice comes from the configured policy, never
    from the receipt. When the configured file cannot be read, the notice states that policy status
    is unknown and the folder is not approved. This function never writes into `out`.
    """
    out = Path(out)
    if is_partial_output(out):
        raise TapeError("refusing a hidden partial output folder (an incomplete write; always invalid)")
    expected = set(OUTPUT_FILES) | {"SHA256SUMS"}
    present = {p.name for p in out.iterdir()} if out.is_dir() else set()
    extra = present - expected
    hidden = {n for n in extra if n.startswith(".")}
    present_visible = present - hidden          # leftover temp folders beside outputs are ignored, not accepted
    if present_visible != expected:
        raise TapeError(f"output incomplete or unexpected files: missing {sorted(expected - present_visible)}, "
                        f"extra {sorted(present_visible - expected)}")
    files = {n: (out / n).read_bytes() for n in OUTPUT_FILES}
    if (out / "SHA256SUMS").read_bytes() != sha256sums(files):
        raise TapeError("SHA256SUMS does not match the files")
    try:
        receipt = json.loads(files["bridge_receipt.json"])
    except ValueError:
        raise TapeError("receipt is not valid JSON") from None
    for key, name in (("csv_sha256", "decision_tape.csv"), ("labeled_csv_sha256", "decision_tape_labeled.csv"),
                      ("price_block_log_sha256", "price_block_log.csv"),
                      ("price_accept_log_sha256", "price_accept_log.csv")):
        if receipt.get(key) != digest(files[name]):
            raise TapeError(f"receipt {key} does not match {name}")

    tape = list(csv.DictReader(io.StringIO(files["decision_tape.csv"].decode())))
    labeled = list(csv.DictReader(io.StringIO(files["decision_tape_labeled.csv"].decode())))
    log = list(csv.DictReader(io.StringIO(files["price_block_log.csv"].decode())))
    acc = list(csv.DictReader(io.StringIO(files["price_accept_log.csv"].decode())))
    if len(tape) != len(labeled):
        raise TapeError("decision_tape.csv and decision_tape_labeled.csv have different row counts")
    for i, (plain, full) in enumerate(zip(tape, labeled, strict=True)):
        for col in FIELDS:
            if plain.get(col) != full.get(col):
                raise TapeError(f"decision_tape.csv does not match decision_tape_labeled.csv at row {i} ({col})")

    if (len(labeled) != receipt.get("rows") or len(log) != (receipt.get("price_blocks") or {}).get("total")
            or len(acc) != (receipt.get("price_accepts") or {}).get("total")
            or len(log) + len(acc) != len(labeled)):
        raise TapeError("receipt row or block counts do not match the CSVs")
    rec_counts = receipt.get("counts") if isinstance(receipt.get("counts"), dict) else {}
    rec_up = receipt.get("upstream_counts") if isinstance(receipt.get("upstream_counts"), dict) else {}
    if rec_counts != _count(labeled, "decision", 0) or rec_up != _count(labeled, "upstream_decision", 0):
        raise TapeError("receipt per-action counts do not match the CSVs")
    blocks = receipt.get("price_blocks") if isinstance(receipt.get("price_blocks"), dict) else {}
    accepts = receipt.get("price_accepts") if isinstance(receipt.get("price_accepts"), dict) else {}
    if (blocks.get("by_reason") or {}) != _by(log, "reason_code", 0) or (blocks.get("by_label") or {}) != _by(log, "label", 0):
        raise TapeError("receipt price_blocks do not match price_block_log.csv")
    if (accepts.get("by_label") or {}) != _by(acc, "label", 0):
        raise TapeError("receipt price_accepts do not match price_accept_log.csv")

    got = receipt.get("labels") if isinstance(receipt.get("labels"), dict) else {}
    labels = [got.get(k) for k in LABEL_FIELDS]
    for r in labeled + log + acc:
        if [r.get(k) for k in LABEL_FIELDS] != labels or labels[:2] != [PAPER_ONLY, DECISION_SUPPORT]:
            raise TapeError("a CSV row lacks the paper-only / decision-support labels")

    _cross_check_sidecars(tape, labeled, log, acc)

    # Claimed official identity must match the repo pins even without --pinned (X2b-style relabel).
    if (got.get("fixture_set") == FIXTURE_SET
            and (receipt.get("archive_sha256") != ARCHIVE_SHA or got.get("fixture_set_sha256") != ARCHIVE_SHA)):
        raise TapeError("fixture_set claims the official Lesson 001 v3 archive, but the sha256 does not match the pin")

    checked: list[str] = []
    skipped: list[str] = []

    # Archive / decisions / policy identity comes from the repo pins or a CLI flag, never from the receipt.
    if expect_archive_sha256 is not None or pinned:
        expected_archive = expect_archive_sha256 if expect_archive_sha256 is not None else ARCHIVE_SHA
        if not expected_archive:
            raise TapeError("required archive pin cannot be resolved")
        _pin(receipt.get("archive_sha256"), expected_archive, "archive_sha256")
        fixture = got.get("fixture_set_sha256")
        if fixture and fixture != expected_archive:
            raise TapeError("fixture_set_sha256 does not match the expected archive sha256")
        checked.append("archive_sha256")
    else:
        skipped.append("archive_sha256")

    if expect_decisions_sha256 is not None or pinned:
        expected_decisions = expect_decisions_sha256 if expect_decisions_sha256 is not None else FROZEN_DECISIONS_SHA
        if not expected_decisions:
            raise TapeError("required decisions pin cannot be resolved")
        _pin(receipt.get("decisions_sha256"), expected_decisions, "decisions_sha256")
        checked.append("decisions_sha256")
    else:
        skipped.append("decisions_sha256")

    tape_sha = digest(files["decision_tape.csv"])
    if pinned:
        if tape_sha != FROZEN_CSV_SHA:
            raise TapeError("decision_tape.csv does not match the pinned Lesson 001 projection")
        checked.append("csv_sha256")
    elif receipt.get("decisions_sha256") == FROZEN_DECISIONS_SHA:
        if tape_sha != FROZEN_CSV_SHA:
            raise TapeError("decision_tape.csv does not match the frozen Lesson 001 projection")
        checked.append("csv_sha256")
    else:
        skipped.append("csv_sha256")

    running_code = digest(Path(__file__).read_bytes())
    expected_code = expect_code_sha256 if expect_code_sha256 is not None else running_code
    if not expected_code:
        raise TapeError("required code pin cannot be resolved")
    if expect_code_sha256 is not None:
        _pin(receipt.get("code_sha256"), expected_code, "code_sha256")
    elif receipt.get("code_sha256") != running_code:
        raise TapeError("code_sha256 does not match the running bridge source")
    checked.append("code_sha256")

    # Receipt-vs-configured-policy compare runs in every mode, including self-only (R14 at 72078004).
    # Identity, status and numbers come from the configured file or a CLI flag, never from the receipt.
    # Fail closed: a missing, directory, unreadable or invalid configured file is refused unless
    # --expect-policy-sha256 is given (then the receipt is compared to that sha only).
    problem = _configured_policy_problem()
    if expect_policy_sha256 is not None:
        expected_policy = expect_policy_sha256
        if pinned and problem is None and expect_policy_sha256 != digest(POLICY_FILE.read_bytes()):
            raise TapeError("policy file sha256 does not match the configured policy file")
    elif problem is not None:
        raise TapeError(f"required policy pin cannot be resolved: {problem}")
    else:
        expected_policy = digest(POLICY_FILE.read_bytes())
    _pin((receipt.get("price_policy") or {}).get("file_sha256"), expected_policy, "policy file sha256")
    if problem is None and expected_policy == digest(POLICY_FILE.read_bytes()):
        want = _receipt_policy_from_file(POLICY_FILE)
        got = receipt.get("price_policy") if isinstance(receipt.get("price_policy"), dict) else {}
        # --expect-policy-sha256 is the documented escape hatch for a byte-identical copy
        # at another --price-policy path: still compare identity, status and numbers, not path.
        skip = {"path"} if expect_policy_sha256 is not None else set()
        for key, val in want.items():
            if key in skip:
                continue
            if got.get(key) != val:
                raise TapeError(f"receipt price_policy.{key} does not match the configured policy")
    checked.append("policy_file_sha256")

    if pinned:
        missing = [name for name in IDENTITY_PINS if name not in checked]
        if missing:
            raise TapeError("required pin cannot be resolved: " + ", ".join(missing))
        expected_archive = expect_archive_sha256 if expect_archive_sha256 is not None else ARCHIVE_SHA
        if expected_archive == ARCHIVE_SHA:
            _check_official_sidecars(files, receipt)
            checked.append("official_sidecars")
        else:
            skipped.append("official_sidecars")
        mode = VERIFY_WITH_PINS
    else:
        skipped.append("official_sidecars")
        if "archive_sha256" in checked or "decisions_sha256" in checked:
            mode = "self-consistency plus supplied identity pins"
        else:
            mode = VERIFY_SELF_ONLY
    receipt["_verification"] = {
        "mode": mode,
        "pinned": pinned,
        "checked": checked,
        "skipped": skipped,
        "pins": checked,  # alias of checked
        "diagnostic_fields": list(SIDECAR_DIAGNOSTIC_FIELDS),
    }
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
    v = sub.add_parser("verify", help="check an output folder against its receipt, SHA256SUMS and repo pins")
    v.add_argument("out", type=Path)
    v.add_argument("--self-consistency-only", action="store_true",
                   help="do not apply archive, decisions or official-sidecar pins; the configured-policy compare still runs")
    v.add_argument("--expect-archive-sha256", dest="expect_archive_sha256", default=None,
                   help="fail if the receipt archive / fixture sha256 is not this value")
    v.add_argument("--expect-decisions-sha256", dest="expect_decisions_sha256", default=None,
                   help="fail if the receipt decisions.json sha256 is not this value")
    v.add_argument("--expect-code-sha256", dest="expect_code_sha256", default=None,
                   help="fail if the receipt bridge code sha256 is not this value")
    v.add_argument("--expect-policy-sha256", dest="expect_policy_sha256", default=None,
                   help="fail if the receipt policy-file sha256 is not this value")
    args_in = list(argv) if argv is not None else None
    a = p.parse_args(args_in)
    raw_args = args_in if args_in is not None else sys.argv[1:]
    try:
        if a.cmd == "verify":
            r = verify(a.out, expect_archive_sha256=a.expect_archive_sha256,
                       expect_decisions_sha256=a.expect_decisions_sha256,
                       expect_code_sha256=a.expect_code_sha256,
                       expect_policy_sha256=a.expect_policy_sha256,
                       pinned=not a.self_consistency_only)
            v = r["_verification"]
            print(f"Verified output ({_format_verification(v)}): {r['rows']} rows, "
                  f"{r['price_blocks']['total']} price blocks. Paper only.")
            print("Diagnostic sidecar fields (not derived from decision_tape.csv): "
                  + ", ".join(v["diagnostic_fields"]) + ".")
            note = _policy_notice(r)
            if note:
                print(note)
            return 0
        r = export(a.bundle, a.out, a.price_policy, command=f"{MODULE_CMD} {shlex.join(raw_args)}",
                   archive_sha=_archive_sha)
    except (OSError, KeyError, ValueError, InvalidOperation, zipfile.BadZipFile) as err:
        if getattr(a, "cmd", None) == "verify":
            note = _policy_notice(None)
            if note:
                print(note, file=sys.stderr)
        print(f"bridge: {err}", file=sys.stderr)
        return 2
    print(f"Verified and exported {r['rows']} synthetic decision rows; {r['price_blocks']['total']} decision-time "
          f"price(s) blocked. Paper only, decision support only. No trades simulated.")
    note = _policy_notice(r)
    if note:
        print(note)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
