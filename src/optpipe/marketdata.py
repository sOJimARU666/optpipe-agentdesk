"""optpipe.marketdata: the app's fail-closed loader for Oodle's market-data packs (FEAT-OODLE-01 PR A).

PAPER ONLY. Pure file I/O: no network, no broker, no credentials, never writes. It mirrors Doodle's reference loader
(spec 1.1.8 `market_data_loader.py`) and delegates every document and cross-file rule (MD-01..MD-15) to the PINNED
spec's `validate_market_snapshot.py` (loaded in place through optpipe.spec, refused unless spec_pin says it is the
pinned release). On top of that it adds app-side defence in depth for Sol's R7 criteria, tagged with the ids Doodle
PROPOSED in trading/proposals/R7-acceptance-proposal.md (MD-16..MD-20; proposal only, not in spec 1.1.8):

  MD-16-NONFINITE  NaN / Infinity / -Infinity / 1e400 (and |int| > 2**53) are rejected at parse time and again after it
  MD-16-DUPKEY     a JSON object with the same key twice, at any depth
  MD-16-PATTERN    a control character (U+0000-U+001F, U+007F, U+0085, U+2028, U+2029) in any JSON key or string,
                   which closes the `$`-anchored-pattern hole (e.g. a pack_id with a trailing newline)
  MD-16-ENTRIES    more than MAX_ENTRIES directory entries (counted with a bounded iterator before any stat)
  MD-16-TOTAL      more than MAX_PACK_BYTES in one pack (checked before and during reading)
  MD-16-EMPTY      an index with zero tickers
  MD-16-ENCODING   a UTF-8 BOM or invalid UTF-8 in a JSON file (reported with MD-03-JSON, exit 2, as the spec does)
  MD-17-ROOT       the date dir is not a direct child of the data root, or its name is not a YYYY-MM-DD date
  MD-17-HARDLINK   a pack file with st_nlink != 1 (a hard link can point at bytes outside the pack)
  MD-17-WRITABLE   the root, the date dir or a pack file is group- or world-writable
  MD-17-OWNER      the root, the date dir or a pack file is owned by neither this user nor root
  MD-17-VANISH     an entry that disappears between listing and stat / open
  MD-17-SNAPSHOT   a file replaced or changed while the pack was read (inode / size / mtime re-checked at the end)
  MD-19-FUTURE-PACK  pack_built_at later than the injected clock `now` + skew: the WHOLE pack fails
  MD-19-FUTURE     any as_of / sources[].as_of later than now + skew
  MD-19-TZ         a naive `now` (every pack time must already carry an offset: MD-04-TIME-NAIVE)
  MD-20-CONFLICT   an earnings_date that is not the next CONFIRMED earnings date in catalysts.json, an earnings_time
                   that contradicts the matching earnings entry's event_time session, or two earnings entries
                   (CONFIRMED / UNCONFIRMED) for one ticker on different dates within 30 days: the pack fails closed.
                   A disagreement with an UNCONFIRMED entry only is a NO IDEA reason for that ticker.
  MD-20-UNVERIFIED an earnings_time BMO / AMC that catalysts.json cannot check (no matching event_time): NO IDEA for
                   that ticker (PR B: split out of MD-20-CONFLICT, Moodle to-do a; nothing contradicts it, it is
                   just not corroborated).
  MD-20-CRITICAL / -NO-IDEA / -SCOPE  no_idea_reasons(): critical UNKNOWN, stale, future or conflicting inputs, a
                   pack that is not ok, or data that is not MARKET (EXAMPLE / TEST) mean NO IDEA.
Every MD-16..MD-21 id in a returned error carries "(proposed)", and MD-00-SPEC / MD-00-ROOT carry "(app)" (label_ids).
Not covered here: MD-18 provenance binding (builder / ingest record; not in PR B either). The MD-19 per-feed
freshness windows, the versioned calendar (2026 + 2027), R07 / R08 and the replay pack selection are in
optpipe.mdfeeds and optpipe.nyse_calendar (PR B); the consumers are optpipe.providers.pack (discovery / research)
and optpipe.watcher.runtime.PackQuoteSource (watcher).
Root: there is no default data root. load_date(root, date) / load_pack(date_dir, root=...) require one; None is
MD-00-ROOT (exit 2) and nothing is opened.
The path to the date dir is opened one component at a time from "/" with O_NOFOLLOW (no symlinked ancestor, no
check-then-open race on the ancestors), the date dir is opened relative to the root's fd, every file relative to the
date dir's fd, once, and the sha256 is computed over the exact bytes that are then parsed.

Clock skew: spec 1.1.8 market_data_rules.yaml defines none, so DEFAULT_SKEW_SECONDS = 0 (fail closed). The proposal's
60 s (MD-19-SKEW, = R01's future tolerance) is an open question (Q-4) and is NOT adopted here.
Staleness: MD-05 (staleness_limit_minutes vs pack_built_at, 20 in spec 1.1.8) is enforced by the pinned validator. At
decision time no_idea_reasons() applies the same limit, read from the pinned spec at load time (PackResult.quote_limit),
against the injected `now`; R01's 15 minutes stays with the risk officer and is not changed.

API
  load_pack(date_dir, *, root, now=None, skew_seconds=0, spec=None) -> PackResult   # never raises; root required
  load_date(root, date, **kw) -> PackResult
  PackResult(ok, date, index, snapshots, summary_md, catalysts, errors) and .exit_code() (0 valid / 1 rule errors /
      2 unreadable, crash or spec not pinned)
  field_usable(field) -> bool; usable_value(field)
  no_idea_reasons(result, ticker, *, now, allow_labels=("MARKET",)) -> list[str]   # [] = the inputs are usable
  usable_contracts(result, ticker, *, now) -> list[dict]   # only these may feed an idea
  data_root_from_env() -> Optional[Path]; label_ids(errors); error_id(error)
"""
from __future__ import annotations

import errno
import hashlib
import json
import math
import os
import re
import stat
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field as dc_field
from datetime import date as _date, datetime, timedelta, timezone
from pathlib import Path
from types import MappingProxyType, ModuleType
from typing import Any, Optional

ENV_DATA_DIR = "OPTPIPE_MARKET_DATA_DIR"        # there is NO default root: --data-dir or this env var (PR #22 review)
VALIDATOR_FILE = "validate_market_snapshot.py"
MAX_ENTRIES = 520                                # proposed MD-16-ENTRIES (500 tickers + 4 pack files + slack)
MAX_PACK_BYTES = 64 * 1024 * 1024                # proposed MD-16-TOTAL
DEFAULT_SKEW_SECONDS = 0                         # spec 1.1.8 sets no market-data skew: fail closed
MAX_SAFE_INT = 2 ** 53
# MD-18 is not implemented in PR B. pack.sha256 checks that the directory bytes match the manifest the
# producer wrote. That is integrity of those bytes, not a trusted external manifest, fetch log, or pre-use
# ingest record. A later PR may close it; until then this stays OPEN.
MD18_PROVENANCE = "OPEN"
FATAL_IDS = frozenset({"MD-01-DIR", "MD-01-UNREADABLE", "MD-03-JSON", "MD-00-CRASH", "MD-00-SPEC", "MD-00-ROOT"})
# ids that are NOT spec 1.1.8 ids are labelled in every error string the module returns, so a later spec that adopts
# or renumbers them cannot be confused with these: MD-16..MD-21 are Doodle's R7 PROPOSAL, MD-00-SPEC / MD-00-ROOT are
# this app's own (MD-00-CRASH and MD-01..MD-15 are spec ids and stay as they are).
PROPOSED = re.compile(r"MD-(1[6-9]|2[01])-[A-Z0-9-]+")
APP_IDS = frozenset({"MD-00-SPEC", "MD-00-ROOT"})
LINE = re.compile(r"([0-9a-f]{64})  (.+)")       # used with fullmatch only
CTRL = re.compile(r"[\x00-\x1f\x7f\x85\u2028\u2029]")
_FLAGS = getattr(os, "O_CLOEXEC", 0)
_DIR = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | _FLAGS


@dataclass
class PackResult:
    ok: bool = False
    date: Optional[str] = None
    index: Optional[dict] = None
    snapshots: dict = dc_field(default_factory=dict)
    summary_md: Optional[str] = None
    catalysts: Optional[dict] = None
    errors: list = dc_field(default_factory=list)
    quote_limit: Optional[timedelta] = None   # spec market_data_rules.yaml staleness.staleness_limit_minutes

    def exit_code(self) -> int:
        return exit_code(self.errors)

    @property
    def schema_version(self) -> Optional[str]:
        return self.index.get("schema_version") if isinstance(self.index, Mapping) else None


def exit_code(errors: list[str]) -> int:
    """same contract as the spec validator: 0 no errors; 2 any unreadable / crash / spec class; else 1"""
    if not errors:
        return 0
    return 2 if any(e.split(" ", 1)[0] in FATAL_IDS for e in errors) else 1


def label_ids(errors: list[str]) -> list[str]:
    """'MD-16-DUPKEY x' -> 'MD-16-DUPKEY (proposed) x'; 'MD-00-ROOT x' -> 'MD-00-ROOT (app) x' (in place; idempotent)"""
    for i, e in enumerate(errors):
        head, _, rest = e.partition(" ")
        if rest.startswith(("(proposed)", "(app)")):
            continue
        tag = "(proposed)" if PROPOSED.fullmatch(head) else "(app)" if head in APP_IDS else None
        if tag:
            errors[i] = f"{head} {tag} {rest}" if rest else f"{head} {tag}"
    return errors


def error_id(e: str) -> str:
    """the id of an error string, with its (proposed) / (app) label if it has one"""
    head, _, rest = e.partition(" ")
    tag = rest.split(" ", 1)[0]
    return f"{head} {tag}" if tag in ("(proposed)", "(app)") else head


def is_seq(o) -> bool:
    """A JSON array. Excludes str, which is a Sequence. Tuples are the frozen form of pack lists."""
    return isinstance(o, Sequence) and not isinstance(o, (str, bytes, bytearray))


def _freeze(o):
    """Deep-freeze JSON documents as MappingProxy / tuple.

    A dict subclass is not enough: dict.update(leaf, ...) uses the C implementation and writes through
    an overridden __setitem__. MappingProxyType is not a dict, so that call raises.
    """
    if type(o) is MappingProxyType:
        return o
    if isinstance(o, Mapping):
        return MappingProxyType({k: _freeze(v) for k, v in o.items()})
    if isinstance(o, list):
        return tuple(_freeze(v) for v in o)
    if isinstance(o, tuple):
        return tuple(_freeze(v) for v in o)
    return o


def _freeze_documents(res: "PackResult") -> None:
    """Index, ticker snapshots and catalysts are read-only after a load. errors stays a list."""
    res.index = _freeze(res.index) if isinstance(res.index, Mapping) else res.index
    res.snapshots = _freeze(res.snapshots) if isinstance(res.snapshots, Mapping) else MappingProxyType({})
    res.catalysts = _freeze(res.catalysts) if isinstance(res.catalysts, Mapping) else res.catalysts


def field_usable(f) -> bool:
    """fail closed: only an OK field with a (finite) value may feed an idea; STALE / UNKNOWN / UNCONFIRMED never"""
    if not (isinstance(f, Mapping) and f.get("status") == "OK" and f.get("value") is not None):
        return False
    v = f["value"]
    return not (isinstance(v, float) and not math.isfinite(v))


def usable_value(f):
    return f["value"] if field_usable(f) else None


# --------------------------------------------------------------------------- the pinned spec validator
class SpecUnavailable(Exception):
    pass


def _vms(spec=None) -> ModuleType:
    """the pinned spec's validate_market_snapshot module; SpecUnavailable unless spec_pin accepts the folder"""
    from .spec import SpecFiles, _import, discover
    from .spec_pin import pin_error
    if spec is None:
        from .config import load_settings
        spec = discover(load_settings().spec_path)
    elif not isinstance(spec, SpecFiles):
        spec = discover(Path(spec))
    perr = pin_error(spec.root)
    if perr:
        raise SpecUnavailable(perr)
    p = spec.root / VALIDATOR_FILE
    if not p.is_file():
        raise SpecUnavailable(f"{VALIDATOR_FILE} not found in the pinned spec dir")
    try:
        mod = _import(str(p))
    except Exception as ex:  # noqa: BLE001 - an unimportable validator fails closed
        raise SpecUnavailable(f"{VALIDATOR_FILE} could not be imported ({type(ex).__name__}: {ex})") from ex
    for name in ("RULES", "validate_document", "pack_errors", "email_errors", "parse_time", "V118"):
        if not hasattr(mod, name):
            raise SpecUnavailable(f"{VALIDATOR_FILE} has no {name}")
    return mod


# --------------------------------------------------------------------------- strict JSON (MD-16)
class _Reject(ValueError):
    def __init__(self, rid: str, msg: str):
        super().__init__(msg)
        self.rid = rid


def _const(s):
    raise _Reject("MD-16-NONFINITE", f"non-finite number {s} (NaN / Infinity are not JSON numbers)")


def _float(s):
    v = float(s)
    if not math.isfinite(v):
        raise _Reject("MD-16-NONFINITE", f"number {s[:40]} overflows to {v}")
    return v


def _int(s):
    v = int(s)
    if abs(v) > MAX_SAFE_INT:
        raise _Reject("MD-16-NONFINITE", f"integer {s[:40]} is outside +/-2**53")
    return v


def _pairs(pairs):
    out = {}
    for k, v in pairs:
        if k in out:
            raise _Reject("MD-16-DUPKEY", f"duplicate key {k!r} in one object")
        out[k] = v
    return out


def _walk_strict(x, where: str = "$") -> Optional[tuple[str, str]]:
    """post-parse belt and braces: (id, message) for the first non-finite number or control character"""
    stack = [(x, where)]
    while stack:
        v, w = stack.pop()
        if isinstance(v, Mapping):
            for k, c in v.items():
                if isinstance(k, str) and CTRL.search(k):
                    return "MD-16-PATTERN", f"{w}: key {k!r} contains a control character"
                stack.append((c, f"{w}.{k}"))
        elif isinstance(v, list):
            stack.extend((c, f"{w}[{i}]") for i, c in enumerate(v))
        elif isinstance(v, bool):
            continue
        elif isinstance(v, float) and not math.isfinite(v):
            return "MD-16-NONFINITE", f"{w}: non-finite number {v}"
        elif isinstance(v, int) and abs(v) > MAX_SAFE_INT:
            return "MD-16-NONFINITE", f"{w}: integer outside +/-2**53"
        elif isinstance(v, str) and CTRL.search(v):
            return "MD-16-PATTERN", f"{w}: {v[:60]!r} contains a control character (identifiers must match whole)"
    return None


def parse_json_strict(name: str, data: bytes, errors: list[str]):
    """a dict, or None with coded errors. Strict UTF-8 (no BOM), no NaN/Infinity/overflow, no duplicate keys."""
    if data.startswith(b"\xef\xbb\xbf"):
        errors.append(f"MD-16-ENCODING {name}: UTF-8 BOM (pack JSON is plain UTF-8)")
        errors.append(f"MD-03-JSON {name}: not UTF-8 JSON: UTF-8 BOM")
        return None
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as ex:
        errors.append(f"MD-16-ENCODING {name}: invalid UTF-8 at byte {ex.start}")
        errors.append(f"MD-03-JSON {name}: not UTF-8 JSON: {type(ex).__name__}: {ex}")
        return None
    try:
        doc = json.loads(text, parse_constant=_const, parse_float=_float, parse_int=_int, object_pairs_hook=_pairs)
    except _Reject as ex:
        errors.append(f"{ex.rid} {name}: {ex}")
        return None
    except (ValueError, RecursionError) as ex:
        errors.append(f"MD-03-JSON {name}: not UTF-8 JSON: {type(ex).__name__}: {ex}")
        return None
    if not isinstance(doc, Mapping):
        errors.append(f"MD-03-JSON {name}: a JSON {type(doc).__name__}, not an object")
        return None
    bad = _walk_strict(doc)
    if bad:
        errors.append(f"{bad[0]} {name} {bad[1]}")
        return None
    return doc


# --------------------------------------------------------------------------- confinement (MD-17)
def _unsafe_name(name: str) -> Optional[str]:
    if os.path.isabs(name) or name.startswith(("/", "\\")) or re.match(r"[A-Za-z]:", name):
        return "absolute path"
    if ".." in re.split(r"[/\\]", name):
        return "'..' component escapes the date dir"
    if "/" in name or "\\" in name or "\x00" in name:
        return "path separator (names must be plain file names in the date dir)"
    if name in (".", ".."):
        return "'.' / '..' is not a file name"
    if name.startswith("."):
        return "dotfile"
    if CTRL.search(name):
        return "control character in the name"
    return None


_WRITE_FLAGS = os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND


def _open_ro(name: str, flags: int, dir_fd: Optional[int] = None) -> int:
    """the loader's ONLY open: read-only by construction (any write flag is a programming error)"""
    if flags & _WRITE_FLAGS:
        raise ValueError("optpipe.marketdata opens read-only")
    return os.open(name, flags, dir_fd=dir_fd)


def _open_dir_nofollow(path: str, errors: list[str], what: str) -> Optional[int]:
    """open an absolute directory path one component at a time with O_NOFOLLOW; None + MD-01 error on any link"""
    parts = [p for p in path.split(os.sep) if p]
    fd = _open_ro(os.sep, os.O_RDONLY | os.O_DIRECTORY | _FLAGS)
    try:
        for i, part in enumerate(parts):
            try:
                nfd = _open_ro(part, _DIR, dir_fd=fd)
            except OSError as ex:
                shown = os.sep.join(parts[i:])
                try:
                    st = os.stat(part, dir_fd=fd, follow_symlinks=False)
                    link = stat.S_ISLNK(st.st_mode)
                except OSError:
                    link = False
                if link or ex.errno == errno.ELOOP:
                    errors.append(f"MD-01-SYMLINK {what}: the path goes through a symlink at '{part}' "
                                  "(no symlinked ancestor allowed)")
                elif ex.errno == errno.ENOENT:
                    errors.append(f"MD-01-DIR {what}: missing ('{shown}' does not exist)")
                else:
                    errors.append(f"MD-01-DIR {what}: cannot open '{part}' as a directory: {ex.strerror}")
                return None
            os.close(fd)
            fd = nfd
        out, fd = fd, -1
        return out
    finally:
        if fd >= 0:
            os.close(fd)


def _shown(name: str) -> str:
    return repr(name) if CTRL.search(name) else name


def _writable(st) -> bool:
    return bool(st.st_mode & 0o022)


def _foreign(st) -> bool:
    """MD-17-OWNER: owned by neither this user nor root (on today's box producer and consumer are the same user)"""
    return hasattr(os, "getuid") and st.st_uid not in (os.getuid(), 0)


# --------------------------------------------------------------------------- loader
def data_root_from_env() -> Optional[Path]:
    """$OPTPIPE_MARKET_DATA_DIR if set and non-empty, else None. There is deliberately NO fallback (no live folder by
    default): a caller that has neither --data-dir nor the env var gets MD-00-ROOT and nothing is opened."""
    env = os.environ.get(ENV_DATA_DIR)
    return Path(env) if env else None


def load_date(root, date: str, **kw) -> PackResult:
    """the confined entry point: <root>/<date>, where date must fullmatch YYYY-MM-DD (MD-17-ROOT)"""
    if root is None or str(root) == "":
        return _no_root(date)
    return load_pack(Path(root) / str(date), root=root, **kw)


def _no_root(date) -> PackResult:
    return PackResult(date=None if date is None else str(date), errors=label_ids([
        f"MD-00-ROOT no market-data root given (pass --data-dir or set ${ENV_DATA_DIR}); nothing was opened"]))


def _wall_clock(tz=timezone.utc) -> datetime:
    """The only wall-clock read in this module. Replay and pack runs pass `now` and never call it."""
    return datetime.now(tz)


def load_pack(date_dir, *, root, now: Optional[datetime] = None, skew_seconds: float = DEFAULT_SKEW_SECONDS,
              spec=None) -> PackResult:
    """load + verify + validate one date dir under `root` (REQUIRED: MD-17-ROOT always applies; None is MD-00-ROOT,
    exit 2, nothing opened). Never raises: any exception is MD-00-CRASH (fail closed)."""
    if root is None or str(root) == "":
        res = _no_root(Path(str(date_dir)).name)
    else:
        try:
            if now is None:
                now = _wall_clock(timezone.utc)
            res = _load(Path(date_dir), Path(root), now, skew_seconds, spec)
        except Exception as ex:  # noqa: BLE001 - fail closed, never a pass
            res = PackResult(date=Path(str(date_dir)).name,
                             errors=[f"MD-00-CRASH {Path(str(date_dir)).name}: loader raised {type(ex).__name__}: {ex}"])
        label_ids(res.errors)
    _freeze_documents(res)
    return res


def _load(date_dir: Path, root: Path, now: datetime, skew: float, spec) -> PackResult:
    errors: list[str] = []
    res = PackResult(date=date_dir.name, errors=errors)
    if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
        errors.append(f"MD-19-TZ now: the clock {now!r} has no UTC offset (times must be tz-aware)")
        return res
    try:
        vms = _vms(spec)
    except SpecUnavailable as ex:
        errors.append(f"MD-00-SPEC {date_dir.name}: market-data validator unavailable: {ex}")
        return res
    try:
        mins = vms.RULES["staleness"]["staleness_limit_minutes"]
        if isinstance(mins, bool) or not isinstance(mins, int) or mins <= 0:
            raise ValueError(repr(mins))
        res.quote_limit = timedelta(minutes=mins)
    except (KeyError, TypeError, ValueError) as ex:
        errors.append(f"MD-00-SPEC {date_dir.name}: staleness.staleness_limit_minutes unusable in the spec: {ex}")
        return res
    absdir = os.path.abspath(date_dir)
    absroot = os.path.abspath(root)
    name = os.path.basename(absdir)
    if os.path.dirname(absdir) != absroot:
        errors.append(f"MD-17-ROOT {date_dir.name}: the date dir is not a direct child of the data root")
        return res
    pat = vms.RULES["layout"]["date_dir_pattern"]
    try:
        ok_name = re.fullmatch(pat, name) is not None and _date.fromisoformat(name).isoformat() == name
    except ValueError:
        ok_name = False
    if not ok_name:
        errors.append(f"MD-17-ROOT {name!r}: the date dir name must be a real YYYY-MM-DD date (fullmatch)")
        return res
    rfd = _open_dir_nofollow(absroot, errors, "data root")
    if rfd is None:
        return res
    try:
        rst = os.fstat(rfd)
        if _writable(rst):
            errors.append("MD-17-WRITABLE data root: group- or world-writable (others could swap a date dir)")
            return res
        if _foreign(rst):
            errors.append(f"MD-17-OWNER data root: owned by uid {rst.st_uid}, not this user or root")
            return res
        try:
            dfd = _open_ro(name, _DIR, dir_fd=rfd)
        except OSError as ex:
            try:
                link = stat.S_ISLNK(os.stat(name, dir_fd=rfd, follow_symlinks=False).st_mode)
            except OSError:
                link = False
            if link or ex.errno == errno.ELOOP:
                errors.append(f"MD-01-SYMLINK {name}: the date dir is a symlink")
            elif ex.errno == errno.ENOENT:
                errors.append(f"MD-01-DIR {name}: date dir missing")
            else:
                errors.append(f"MD-01-DIR {name}: not a directory or unreadable: {ex.strerror}")
            return res
        try:
            dst = os.fstat(dfd)
            if _writable(dst):
                errors.append(f"MD-17-WRITABLE {name}: the date dir is group- or world-writable")
            elif _foreign(dst):
                errors.append(f"MD-17-OWNER {name}: the date dir is owned by uid {dst.st_uid}, not this user or root")
            else:
                _load_fd(vms, dfd, name, res, now, timedelta(seconds=skew))
        finally:
            os.close(dfd)
    finally:
        os.close(rfd)
        if errors:
            res.index, res.snapshots, res.summary_md, res.catalysts = None, {}, None, None
        res.ok = not errors
    return res


def _allowed(vms, name: str, v118: bool) -> bool:
    lay = vms.RULES["layout"]
    return (name in (lay["manifest"], lay["index"], lay["summary"])
            or re.fullmatch(lay["ticker_file_pattern"], name) is not None
            or (v118 and name == lay["catalysts"]))


def _read(dfd: int, name: str, errors: list[str], max_bytes: int, budget: list[int], expect=None):
    """(bytes, fstat) of one regular file read once relative to the date dir fd, never following a link"""
    try:
        fd = _open_ro(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | _FLAGS, dir_fd=dfd)
    except FileNotFoundError:
        errors.append(f"MD-17-VANISH {name}: disappeared before it was opened")
        return None, None
    except OSError as ex:
        errors.append(f"MD-01-UNREADABLE {name}: cannot open without following links: {type(ex).__name__}: {ex.strerror}")
        return None, None
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode):
            errors.append(f"MD-01-NOT-REGULAR {name}: not a regular file")
            return None, None
        if expect is not None and _sig(st) != _sig(expect):
            errors.append(f"MD-17-SNAPSHOT {name}: replaced between the directory scan and open")
            return None, None
        if st.st_nlink != 1:
            errors.append(f"MD-17-HARDLINK {name}: {st.st_nlink} hard links (pack files must have exactly one)")
            return None, None
        if _writable(st):
            errors.append(f"MD-17-WRITABLE {name}: group- or world-writable")
            return None, None
        if _foreign(st):
            errors.append(f"MD-17-OWNER {name}: owned by uid {st.st_uid}, not this user or root")
            return None, None
        if st.st_size > max_bytes:
            errors.append(f"MD-01-SIZE {name}: {st.st_size} bytes > max_file_bytes {max_bytes}")
            return None, None
        chunks, n = [], 0
        while True:
            b = os.read(fd, 65536)
            if not b:
                break
            chunks.append(b)
            n += len(b)
            budget[0] -= len(b)
            if n > max_bytes:
                errors.append(f"MD-01-SIZE {name}: grew beyond max_file_bytes {max_bytes} while reading")
                return None, None
            if budget[0] < 0:
                errors.append(f"MD-16-TOTAL {name}: the pack exceeds {MAX_PACK_BYTES} bytes in total")
                return None, None
        return b"".join(chunks), st
    except OSError as ex:
        errors.append(f"MD-01-UNREADABLE {name}: {type(ex).__name__}: {ex.strerror}")
        return None, None
    finally:
        os.close(fd)


def _list(dfd: int, errors: list[str]) -> Optional[list[str]]:
    names: list[str] = []
    with os.scandir(dfd) as it:
        for e in it:
            names.append(e.name)
            if len(names) > MAX_ENTRIES:
                errors.append(f"MD-16-ENTRIES date dir: more than {MAX_ENTRIES} entries (stopped counting)")
                return None
    return sorted(names)


def _peek_v118(vms, dfd: int, names: list[str], scan: dict) -> bool:
    idx = vms.RULES["layout"]["index"]
    if idx not in names or idx not in scan:
        return False
    data, _ = _read(dfd, idx, [], int(vms.RULES["layout"]["max_file_bytes"]), [MAX_PACK_BYTES], scan.get(idx))
    doc = parse_json_strict(idx, data, []) if data is not None else None
    return isinstance(doc, Mapping) and doc.get("schema_version") == vms.V118


def _load_fd(vms, dfd: int, dir_name: str, res: PackResult, now: datetime, skew: timedelta) -> None:
    errors = res.errors
    lay = vms.RULES["layout"]
    MANIFEST, INDEX, SUMMARY, CATALYSTS = lay["manifest"], lay["index"], lay["summary"], lay["catalysts"]
    max_bytes = int(lay["max_file_bytes"])
    names = _list(dfd, errors)
    if names is None:
        return
    # ---- MD-01 / MD-17: every entry, without following anything
    scan: dict[str, os.stat_result] = {}
    on_disk: set[str] = set()
    rejected: set[str] = set()
    pre: list[str] = []
    for name in names:
        try:
            st = os.stat(name, dir_fd=dfd, follow_symlinks=False)
        except FileNotFoundError:
            errors.append(f"MD-17-VANISH {name}: disappeared between listing and stat")
            continue
        scan[name] = st
    v118 = _peek_v118(vms, dfd, names, scan)
    allowed = f"{MANIFEST}, {INDEX}, {SUMMARY}, {CATALYSTS}, <TICKER>.json" if v118 else \
        f"{MANIFEST}, {INDEX}, {SUMMARY}, <TICKER>.json"
    total = 0
    for name, st in scan.items():
        if stat.S_ISLNK(st.st_mode):
            pre.append(f"MD-01-SYMLINK {name}: symlinks are not allowed in a date dir")
        elif stat.S_ISDIR(st.st_mode):
            pre.append(f"MD-01-NAME {name}: sub-directories are not allowed in a date dir")
        elif not stat.S_ISREG(st.st_mode):
            pre.append(f"MD-01-NOT-REGULAR {name}: not a regular file (FIFO, socket or device)")
        elif name.startswith(".") or CTRL.search(name) or not _allowed(vms, name, v118):
            pre.append(f"MD-01-NAME {_shown(name)}: not an allowed pack file name ({allowed})")
        else:
            if st.st_mode & 0o111:
                pre.append(f"MD-01-EXEC {name}: executable bit set (pack files are data, never scripts)")
            if st.st_size > max_bytes:
                pre.append(f"MD-01-SIZE {name}: {st.st_size} bytes > max_file_bytes {max_bytes}")
            if st.st_nlink != 1:
                pre.append(f"MD-17-HARDLINK {name}: {st.st_nlink} hard links (pack files must have exactly one)")
            if _writable(st):
                pre.append(f"MD-17-WRITABLE {name}: group- or world-writable")
            if _foreign(st):
                pre.append(f"MD-17-OWNER {name}: owned by uid {st.st_uid}, not this user or root")
            total += st.st_size
            on_disk.add(name)
            continue
        on_disk.add(name)
        rejected.add(name)
    errors.extend(pre)
    if total > MAX_PACK_BYTES:
        errors.append(f"MD-16-TOTAL date dir: {total} bytes > {MAX_PACK_BYTES} (nothing is read)")
        return
    # ---- MD-02: the manifest
    if MANIFEST not in on_disk:
        errors.append(f"MD-02-MISSING {MANIFEST}: no manifest in the date dir (fail closed: nothing is loaded)")
        return
    budget = [MAX_PACK_BYTES]
    read_st: dict[str, os.stat_result] = {}
    if MANIFEST in rejected:
        return
    raw, mst = _read(dfd, MANIFEST, errors, max_bytes, budget, scan.get(MANIFEST))
    if raw is None:
        return
    read_st[MANIFEST] = mst
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        errors.append(f"MD-02-FORMAT {MANIFEST}: not UTF-8")
        return
    listed: dict[str, str] = {}
    for n, line in enumerate(text.splitlines(), 1):
        m = LINE.fullmatch(line)
        if not m:
            errors.append(f"MD-02-FORMAT {MANIFEST} line {n}: expected '<64 lowercase hex>  <name>'")
            continue
        digest, name = m.groups()
        why = _unsafe_name(name)
        if why:
            errors.append(f"MD-02-PATH {MANIFEST} line {n}: {name!r}: {why}")
            continue
        if name == MANIFEST:
            errors.append(f"MD-02-FORMAT {MANIFEST} line {n}: the manifest must not list itself")
            continue
        if not _allowed(vms, name, v118):
            errors.append(f"MD-01-NAME {MANIFEST} line {n}: {name!r} is not an allowed pack file name")
            continue
        if name in listed:
            errors.append(f"MD-02-DUPLICATE {MANIFEST} line {n}: {name} listed twice")
            continue
        listed[name] = digest
    if not text.strip():
        errors.append(f"MD-02-FORMAT {MANIFEST}: empty")
    for name in sorted(on_disk - set(listed) - {MANIFEST}):
        errors.append(f"MD-02-EXTRA {name}: on disk but not listed in {MANIFEST}")
    for name in sorted(set(listed) - on_disk):
        errors.append(f"MD-02-ABSENT {name}: listed in {MANIFEST} but not in the date dir")
    # ---- read + hash every listed file once (hash over the exact bytes parsed)
    verified: dict[str, bytes] = {}
    for name in sorted((set(listed) & on_disk) - rejected):
        data, st = _read(dfd, name, errors, max_bytes, budget, scan.get(name))
        if data is None:
            if any(e.startswith("MD-16-TOTAL") for e in errors):
                return
            continue
        read_st[name] = st
        got = hashlib.sha256(data).hexdigest()
        if got != listed[name]:
            errors.append(f"MD-02-HASH {name}: sha256 {got} != {MANIFEST} {listed[name]}")
            continue
        verified[name] = data
    # ---- MD-03..MD-10 per file (pinned validator), after strict parsing
    if INDEX not in listed:
        errors.append(f"MD-02-INDEX {INDEX}: not listed in {MANIFEST}")
    docs: dict[str, dict] = {}
    summary = None
    for name, data in sorted(verified.items()):
        if name == SUMMARY:
            try:
                summary = data.decode("utf-8")
            except UnicodeDecodeError:
                errors.append(f"MD-16-ENCODING {SUMMARY}: invalid UTF-8")
                errors.append(f"MD-03-JSON {SUMMARY}: not UTF-8 text")
                continue
            errors.extend(vms.email_errors(summary, SUMMARY))
            continue
        doc = parse_json_strict(name, data, errors)
        if doc is None:
            continue
        want = "pack_index" if name == INDEX else "catalyst_log" if name == CATALYSTS else "ticker_snapshot"
        if doc.get("kind") != want:
            errors.append(f"MD-03-SCHEMA {name}: kind {doc.get('kind')!r}, expected {want!r}")
            continue
        e = vms.validate_document(doc, name)
        errors.extend(e)
        if not any(x.startswith("MD-03-SCHEMA") for x in e):
            docs[name] = doc
    index = docs.pop(INDEX, None)
    catalysts = docs.pop(CATALYSTS, None)
    _snapshot_check(dfd, names, read_st, errors)
    if index is None:
        return
    if v118 and index.get("schema_version") != vms.V118 and CATALYSTS in listed:
        errors.append(f"MD-01-NAME {CATALYSTS}: allowed only in a 1.1.8 pack, but the verified index.json has "
                      f"schema_version {index.get('schema_version')!r}")
    named = {t["file"] for t in index["tickers"]}
    for name in sorted(named - set(listed)):
        errors.append(f"MD-02-INDEX {name}: named in {INDEX} but not listed in {MANIFEST}")
    tf = lay["ticker_file_pattern"]
    for name in sorted({n for n in listed if re.fullmatch(tf, n) and n != INDEX} - named):
        errors.append(f"MD-02-INDEX {name}: listed in {MANIFEST} but not named in {INDEX}")
    errors.extend(vms.pack_errors(index, docs, dir_name, catalysts) if v118 else vms.pack_errors(index, docs, dir_name))
    # ---- app-side R7 hardening (proposed MD-16 / MD-19 / MD-20 ids)
    if not index["tickers"]:
        errors.append(f"MD-16-EMPTY {INDEX}: zero tickers (an empty pack is not 'valid but empty')")
    errors.extend(_future_errors(vms, index, docs, catalysts, now, skew))
    errors.extend(_conflict_errors(docs, catalysts, dir_name))
    res.index, res.catalysts, res.summary_md = index, catalysts, summary
    res.snapshots = {d["ticker"]: d for d in docs.values()}


def _sig(st) -> tuple:
    """what must not change between the scan, the open and the end of the load"""
    return st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns, st.st_ctime_ns


def _snapshot_check(dfd: int, names: list[str], read_st: dict, errors: list[str]) -> None:
    """MD-17-SNAPSHOT: the directory listing and every file read are unchanged after reading"""
    try:
        again = sorted(os.listdir(dfd))
    except OSError as ex:
        errors.append(f"MD-17-SNAPSHOT date dir: cannot re-list ({ex.strerror})")
        return
    if again != names:
        errors.append("MD-17-SNAPSHOT date dir: entries changed while the pack was read")
        return
    for name, st in sorted(read_st.items()):
        try:
            now_st = os.stat(name, dir_fd=dfd, follow_symlinks=False)
        except OSError:
            errors.append(f"MD-17-VANISH {name}: disappeared after it was read")
            continue
        if _sig(now_st) != _sig(st):
            errors.append(f"MD-17-SNAPSHOT {name}: changed or replaced while the pack was read")


def _times(x, path: str = "$"):
    """(path, key, value) for every as_of / pack_built_at string at any depth"""
    stack = [(x, path)]
    while stack:
        v, w = stack.pop()
        if isinstance(v, Mapping):
            for k, c in v.items():
                if k in ("as_of", "pack_built_at") and isinstance(c, str):
                    yield f"{w}.{k}", c
                else:
                    stack.append((c, f"{w}.{k}"))
        elif isinstance(v, list):
            stack.extend((c, f"{w}[{i}]") for i, c in enumerate(v))


def _aware(s: str) -> Optional[datetime]:
    try:
        t = datetime.fromisoformat(s)
    except (TypeError, ValueError):
        return None
    return t if t.tzinfo is not None and t.utcoffset() is not None else None


def _future_errors(vms, index, docs, catalysts, now: datetime, skew: timedelta) -> list[str]:
    limit = now + skew
    out: list[str] = []
    built = _aware(index.get("pack_built_at") or "")
    if built is not None and built > limit:
        out.append(f"MD-19-FUTURE-PACK index.json: pack_built_at {index['pack_built_at']} is after the clock "
                   f"{now.isoformat()} + {skew.total_seconds():g} s (the whole pack fails)")
    files = [("index.json", index)] + sorted(docs.items()) + ([("catalysts.json", catalysts)] if catalysts else [])
    n = 0
    for fname, doc in files:
        for w, s in sorted(_times(doc)):
            t = _aware(s)
            if t is not None and t > limit and not w.endswith("pack_built_at"):
                n += 1
                if n <= 20:
                    out.append(f"MD-19-FUTURE {fname} {w[2:]}: {s} is after the clock {now.isoformat()}")
    if n > 20:
        out.append(f"MD-19-FUTURE pack: ... and {n - 20} more as_of after the clock (first 20 listed)")
    return out


def _conflict_errors(docs, catalysts, pack_date: str) -> list[str]:
    """MD-20-CONFLICT (pack-fatal), proposal (a) and (b), 1.1.8 packs with catalysts.json:
    (b) two earnings entries for one ticker, CONFIRMED or UNCONFIRMED, on different dates within 30 days
        (two CONFIRMED quarters 87 days apart are NOT a conflict);
    (a) a ticker file's earnings_date that is not the next CONFIRMED earnings date (the earliest one on or after the
        pack date), or an earnings_time (BMO / AMC) that contradicts the NY session of the matching earnings
        entry's event_time (before 09:30 = BMO, from 16:00 = AMC, in between = neither).
    A ticker file that disagrees only with UNCONFIRMED entries, or an earnings_time that catalysts.json cannot check
    (no event_time), is a per-ticker NO IDEA reason in no_idea_reasons(), not a pack failure."""
    if not isinstance(catalysts, Mapping):
        return []
    confirmed = _earnings_dates(catalysts, ("CONFIRMED",))
    dated = _earnings_dates(catalysts, ("CONFIRMED", "UNCONFIRMED"))
    out: list[str] = []
    for t, dates in sorted(dated.items()):
        ds = sorted(dates)
        close = [(a, b) for a, b in zip(ds, ds[1:]) if _days(a, b) is not None and _days(a, b) <= 30]
        if close:
            out.append(f"MD-20-CONFLICT catalysts.json {t}: earnings entries on different dates within 30 days "
                       f"{[list(p) for p in close]} (CONFIRMED / UNCONFIRMED)")
    for fname, d in sorted(docs.items()):
        if not isinstance(d, Mapping):
            continue
        t, ed, et = d.get("ticker"), d.get("earnings_date"), d.get("earnings_time")
        nxt = _next_date(confirmed.get(t, ()), pack_date)
        if isinstance(ed, Mapping) and ed.get("value") is not None and nxt is not None and ed["value"] != nxt:
            out.append(f"MD-20-CONFLICT {fname} earnings_date: {ed['value']} ({ed.get('status')}) contradicts the "
                       f"next CONFIRMED catalysts.json earnings {nxt} (all CONFIRMED: {sorted(confirmed[t])})")
        if isinstance(ed, Mapping) and isinstance(et, Mapping) and et.get("value") in ("BMO", "AMC"):
            for c in _earnings_entries(catalysts, t, ed.get("value")):
                sess = _session(c.get("event_time"))
                if sess is not None and sess != et["value"]:
                    out.append(f"MD-20-CONFLICT {fname} earnings_time: {et['value']} ({et.get('status')}) contradicts "
                               f"the {c.get('status')} catalysts.json earnings event_time {c['event_time']} ({sess})")
    return out


def _next_date(dates, pack_date: str) -> Optional[str]:
    ahead = sorted(x for x in dates if isinstance(x, str) and x >= str(pack_date))
    return ahead[0] if ahead else None


def _earnings_entries(catalysts, ticker, event_date) -> list[dict]:
    return [c for c in (catalysts or {}).get("catalysts") or []
            if isinstance(c, Mapping) and c.get("catalyst_type") == "earnings" and c.get("ticker") == ticker
            and c.get("status") in ("CONFIRMED", "UNCONFIRMED") and c.get("event_date") == event_date]


def _session(event_time) -> Optional[str]:
    """NY session of an earnings event_time: BMO before 09:30, AMC from 16:00, DURING in between, None if absent"""
    t = _aware(event_time) if isinstance(event_time, str) else None
    if t is None:
        return None
    from zoneinfo import ZoneInfo
    ny = t.astimezone(ZoneInfo("America/New_York"))
    hm = (ny.hour, ny.minute)
    return "BMO" if hm < (9, 30) else "AMC" if hm >= (16, 0) else "DURING"


def _earnings_dates(catalysts, statuses) -> dict[str, set]:
    out: dict[str, set] = {}
    for c in (catalysts or {}).get("catalysts") or []:
        if isinstance(c, Mapping) and c.get("catalyst_type") == "earnings" and c.get("status") in statuses \
                and isinstance(c.get("ticker"), str) and isinstance(c.get("event_date"), str):
            out.setdefault(c["ticker"], set()).add(c["event_date"])
    return out


def _days(a: str, b: str) -> Optional[int]:
    try:
        return abs((_date.fromisoformat(b) - _date.fromisoformat(a)).days)
    except ValueError:
        return None


# --------------------------------------------------------------------------- decision time (MD-20)
IDEA_TOP_FIELDS = ("last_price", "earnings_date")             # pinned exactly by a test: widen / narrow deliberately
CONTRACT_CRITICAL = ("bid", "ask", "open_interest", "spread_pct_of_mid")   # R01 / R02 / R03 / R05
QUOTE_LEAVES = frozenset({"last_price", "iv", "iv_rank", "implied_move", "open_interest", "volume", "bid", "ask",
                          "spread", "spread_pct_of_mid"})


def _field_reason(path: str, leaf: str, f, now: datetime, skew: timedelta, limit: timedelta) -> Optional[str]:
    if not isinstance(f, Mapping):
        return f"MD-20-CRITICAL {path}: missing (UNKNOWN)"
    if not field_usable(f):
        return f"MD-20-CRITICAL {path}: status {f.get('status')} (only OK with a value is usable)"
    t = _aware(f.get("as_of") or "")
    if t is None:
        return f"MD-19-TZ {path}: as_of missing, naive or unparsable"
    if t > now + skew:
        return f"MD-19-FUTURE {path}: as_of {f['as_of']} is after the clock"
    if leaf in QUOTE_LEAVES and now - t > limit:
        return (f"MD-19-STALE-quote {path}: {now - t} old at decision time (> {limit}, the spec's "
                "staleness_limit_minutes; R01's 15 min is separate)")
    return None


def usable_contracts(res: PackResult, ticker: str, *, now: datetime,
                     skew_seconds: float = DEFAULT_SKEW_SECONDS) -> list[dict]:
    """the ticker's pack contracts whose EVERY CONTRACT_CRITICAL field is OK, valued, not future and fresh at `now`
    (Moodle to-do c: an idea may use only these; one usable contract never makes another one usable).
    [] when the pack is not ok, the clock is naive or anything is odd (fail closed)."""
    try:
        if not res.ok or not isinstance(res.quote_limit, timedelta) or not isinstance(now, datetime) \
                or now.tzinfo is None or now.utcoffset() is None:
            return []
        d = res.snapshots.get(ticker)
        contracts = ((d or {}).get("option_liquidity") or {}).get("contracts") or [] if isinstance(d, Mapping) else []
        skew = timedelta(seconds=skew_seconds)
        return [c for c in contracts if isinstance(c, Mapping)
                and not any(_field_reason("c", k, c.get(k), now, skew, res.quote_limit) for k in CONTRACT_CRITICAL)]
    except Exception:  # noqa: BLE001 - fail closed
        return []


def no_idea_reasons(res: PackResult, ticker: str, *, now: datetime, skew_seconds: float = DEFAULT_SKEW_SECONDS,
                    allow_labels: tuple = ("MARKET",)) -> list[str]:
    """[] only if every critical idea input for `ticker` is usable at `now`; otherwise the reasons: NO IDEA.
    This never makes a candidate: R01..R13 still decide in the session record. Fail closed on anything odd.

    Checked here: IDEA_TOP_FIELDS (last_price, earnings_date) OK, fresh and not future; earnings_date vs UNCONFIRMED
    catalysts; earnings_time BMO / AMC corroborated by a matching catalysts.json earnings event_time (else
    MD-20-UNVERIFIED), and an UNKNOWN / null earnings_time is MD-20-CRITICAL (PR B decision, fail closed); at least
    one contract with every CONTRACT_CRITICAL field usable. A caller MUST then use only usable_contracts(): one
    usable contract does not make the ticker's other contracts usable.
    The per-feed freshness windows (MD-19), the calendar and R07 / R08 against a hold window are in
    optpipe.mdfeeds.decision_reasons(), which every consumer calls (it starts with this function)."""
    try:
        if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
            return label_ids(["MD-19-TZ now: naive clock (NO IDEA)"])
        if not res.ok or not isinstance(res.index, Mapping) or not isinstance(res.quote_limit, timedelta):
            first = error_id(res.errors[0]) if res.errors else "not loaded"
            return label_ids([f"MD-20-NO-IDEA {ticker}: the pack is not ok ({first})"])
        label = res.index.get("data_label")
        if label not in allow_labels:
            return label_ids([f"MD-20-SCOPE {ticker}: data_label {label} is not market data for a real idea"])
        d = res.snapshots.get(ticker)
        if not isinstance(d, Mapping):
            return label_ids([f"MD-20-NO-IDEA {ticker}: not in the pack"])
        skew, limit = timedelta(seconds=skew_seconds), res.quote_limit
        out = [r for k in IDEA_TOP_FIELDS if (r := _field_reason(f"{ticker}.{k}", k, d.get(k), now, skew, limit))]
        ed, et = d.get("earnings_date"), d.get("earnings_time")
        other = _earnings_dates(res.catalysts, ("UNCONFIRMED",)).get(ticker, set())
        if isinstance(ed, Mapping) and ed.get("value") is not None and other and {ed["value"]} != other:
            out.append(f"MD-20-CONFLICT {ticker}.earnings_date: {ed['value']} disagrees with UNCONFIRMED catalysts "
                       f"{sorted(other)}")
        if isinstance(et, Mapping) and et.get("value") in ("BMO", "AMC") and field_usable(et):
            entries = _earnings_entries(res.catalysts, ticker, ed.get("value") if isinstance(ed, Mapping) else None)
            if not any(_session(c.get("event_time")) == et["value"] for c in entries):
                out.append(f"MD-20-UNVERIFIED {ticker}.earnings_time: {et['value']} is not corroborated by a "
                           "catalysts.json earnings event_time for the same date (cannot be checked: NO IDEA)")
        elif isinstance(et, Mapping) and et.get("value") in ("BMO", "AMC"):
            # STALE / UNKNOWN / UNCONFIRMED with a session label is not corroboration. R07 cannot place the print.
            out.append(f"MD-20-CRITICAL {ticker}.earnings_time: status {et.get('status')} value {et.get('value')} "
                       "(only OK is usable; R07 cannot place the event: fail closed)")
        else:
            # PR B decision (Moodle to-do b): an UNKNOWN / null earnings_time is a critical UNKNOWN for R07 (BMO vs
            # AMC decides on which side of a hold-window edge the event falls), so it is NO IDEA: fail closed.
            shown = et.get("value") if isinstance(et, Mapping) else "missing"
            out.append(f"MD-20-CRITICAL {ticker}.earnings_time: {shown} (UNKNOWN; R07 cannot place the event: "
                       "fail closed)")
        if not usable_contracts(res, ticker, now=now, skew_seconds=skew_seconds):
            out.append(f"MD-20-CRITICAL {ticker}.option_liquidity: no contract with usable, fresh "
                       f"{'/'.join(CONTRACT_CRITICAL)}")
        return label_ids(out)
    except Exception as ex:  # noqa: BLE001 - fail closed
        return [f"MD-00-CRASH {ticker}: no_idea_reasons raised {type(ex).__name__} (NO IDEA)"]
