"""Load sessions from an out/ directory for the dashboard (read-only).

Session artifacts are opened only as direct children of the configured output directory.
Symlinks are not followed: a link is a containment failure even when its target would
still land inside that directory, because following it re-opens a check-then-use window.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Optional
from zoneinfo import ZoneInfo

from ..records.compat import V11_SHAPE, from_v1

BERLIN = ZoneInfo("Europe/Berlin")
NY = ZoneInfo("America/New_York")
_V1 = re.compile(r"^session-(\d{4}-\d{2}-\d{2})\.json$")


def sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


_DIR_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC
_FILE_FLAGS = os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC
_ARTIFACTS = (
    ("v1", ".json"),
    ("v1_1", ".v1_1.json"),
    ("trace", ".trace.json"),
    ("sha256", ".trace.json.sha256"),
    ("md", ".md"),
    ("watch", ".watch.json"),
    ("watch_sha256", ".watch.json.sha256"),
)


def _single_component(name: str) -> bool:
    """True when `name` cannot be a path: one final component, no separator or NUL."""
    if not name or name in {".", ".."}:
        return False
    if "/" in name or "\\" in name or "\x00" in name:
        return False
    return Path(name).name == name


def _direct_file_info(dirfd: int, root: Path, name: str) -> Optional[os.stat_result]:
    """lstat of one direct child. None if absent. KeyError if the entry is not a contained regular file.

    `name` must be a single component. openat would treat a slash or `..` as a path relative to
    the directory descriptor and could leave the output directory.
    """
    if not _single_component(name):
        raise KeyError(name)
    try:
        info = os.stat(name, dir_fd=dirfd, follow_symlinks=False)
    except FileNotFoundError:
        return None
    except OSError:
        raise KeyError(name) from None
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        raise KeyError(name)
    candidate = root / name
    try:
        if candidate.is_symlink() or not candidate.resolve().is_relative_to(root):
            raise KeyError(name)
    except (OSError, RuntimeError):
        raise KeyError(name) from None
    return info


def _read_direct_file(dirfd: int, root: Path, name: str) -> Optional[bytes]:
    """Bytes of `name` inside the already-opened output directory.

    None if that entry is absent. KeyError if it is a symlink, not a regular file, or its
    resolved path is outside `root`. The descriptor is opened with O_NOFOLLOW and must still
    be the same inode the lstat saw, so a swap to a link between the check and the read fails
    closed instead of following the new target.
    """
    info = _direct_file_info(dirfd, root, name)
    if info is None:
        return None
    try:
        fd = os.open(name, _FILE_FLAGS, dir_fd=dirfd)
    except OSError:
        raise KeyError(name) from None
    try:
        opened = os.fstat(fd)
        if opened.st_dev != info.st_dev or opened.st_ino != info.st_ino or not stat.S_ISREG(opened.st_mode):
            raise KeyError(name)
        chunks: list[bytes] = []
        while True:
            block = os.read(fd, 1024 * 1024)
            if not block:
                break
            chunks.append(block)
        return b"".join(chunks)
    finally:
        os.close(fd)


@dataclass
class Integrity:
    trace_sha256: Optional[str] = None
    sidecar_ok: Optional[bool] = None         # trace.json.sha256 matches the trace file
    trace_ref_ok: Optional[bool] = None       # v1.1 trace_ref.sha256 matches the trace file
    session_ref_ok: Optional[bool] = None     # trace.session_ref.sha256 matches the record content (minus trace_ref)
    watch_sidecar_ok: Optional[bool] = None   # <session>.watch.json.sha256 matches the watch file
    watch_session_ok: Optional[bool] = None   # watch.session.sha256 matches session-<date>.json
    guard_errors: list[str] = field(default_factory=list)   # records/guards.check_record on the record (SOL-ACCEPT-001 G1)
    problems: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.problems and self.trace_sha256 is not None


@dataclass
class Session:
    date: str
    record: dict[str, Any]                    # v1.1 shape (preview file, or upgraded from v1)
    origin: str                               # "v1.1 preview" | "v1 (upgraded for display)"
    trace: Optional[dict[str, Any]]
    integrity: Integrity
    files: dict[str, str]
    watch: Optional[dict[str, Any]] = None    # <session>.watch.json (M3 watcher output), if present

    @property
    def runs(self) -> list[dict]:
        return self.record.get("runs", [])

    def trace_run(self, run_id: str) -> Optional[dict]:
        return next((r for r in (self.trace or {}).get("runs", []) if r["run_id"] == run_id), None)

    def per_run(self, key: str, run_id: Optional[str]) -> Optional[dict]:
        """premarket_scans / global_snapshots entry for run_id (latest when None)."""
        items = self.record.get(key) or []
        if not items:
            legacy = self.record.get(key[:-1])
            return legacy if legacy and (run_id is None or legacy.get("run_id") == run_id) else None
        if run_id is None:
            return items[-1]
        return next((i for i in items if i.get("run_id") == run_id), None)


def guard_errors(rec: Any) -> list[str]:
    """optpipe's record guards (INV-05/06/16) on a record the dashboard is about to show. Never raises: a guard
    crash is itself an error (fail closed), so the page still renders and says the record is not trustworthy."""
    from ..records.guards import check_record
    if not isinstance(rec, dict):
        return [f"GUARD-CRASHED record is a {type(rec).__name__}, not an object (fail closed)"]
    try:
        return list(check_record(rec))
    except Exception as e:  # noqa: BLE001 - display path: report, never crash
        return [f"GUARD-CRASHED check_record raised {type(e).__name__}: {e} (fail closed)"]


class OutDir:
    def __init__(self, root: Path | str):
        self.root = Path(root)

    def _directory(self) -> tuple[Path, int]:
        """Resolved output directory and an O_DIRECTORY descriptor for it. Caller closes the descriptor."""
        root = self.root.resolve()
        return root, os.open(root, _DIR_FLAGS)

    def read_contained(self, name: str) -> Optional[bytes]:
        """Read one direct child of the output directory. See `_read_direct_file`."""
        if not _single_component(name):
            raise KeyError(name)
        try:
            root, dirfd = self._directory()
        except (OSError, RuntimeError):
            raise KeyError(name) from None
        try:
            return _read_direct_file(dirfd, root, name)
        finally:
            os.close(dirfd)

    def dates(self) -> list[str]:
        if not self.root.is_dir():
            return []
        try:
            root, dirfd = self._directory()
        except (OSError, RuntimeError):
            return []
        try:
            try:
                listed = [entry.name for entry in os.scandir(dirfd)]
            except OSError:
                return []
            found = []
            for name in listed:
                match = _V1.match(name)
                if not match:
                    continue
                try:
                    if _direct_file_info(dirfd, root, name) is not None:
                        found.append(match.group(1))
                except KeyError:
                    continue
            return sorted(found, reverse=True)
        finally:
            os.close(dirfd)

    def load(self, date: str) -> Session:
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date):
            raise KeyError(date)
        # The path component is a date this directory actually listed, not the request string.
        safe_date = next((candidate for candidate in self.dates() if candidate == date), None)
        if safe_date is None:
            raise KeyError(date)
        names = {key: f"session-{safe_date}{suffix}" for key, suffix in _ARTIFACTS}
        try:
            root, dirfd = self._directory()
        except (OSError, RuntimeError):
            raise KeyError(safe_date) from None
        try:
            blobs = {key: _read_direct_file(dirfd, root, name) for key, name in names.items()}
        finally:
            os.close(dirfd)
        raw = blobs["v1"]
        if raw is None:
            raise KeyError(safe_date)
        main = json.loads(raw)
        preview = blobs["v1_1"]
        if main.get("schema_version") in V11_SHAPE:
            rec, origin = main, f"record (schema {main['schema_version']})"
        elif preview is not None:
            rec, origin = json.loads(preview), "v1.1 preview (older output)"
        else:
            rec, origin = from_v1(main), "v1 (upgraded for display)"
        integ, trace = Integrity(), None
        integ.guard_errors = guard_errors(rec)
        integ.problems += [f"guard: {e}" for e in integ.guard_errors]
        trace_name = names["trace"]
        trace_bytes = blobs["trace"]
        if trace_bytes is not None:
            trace = json.loads(trace_bytes)
            integ.trace_sha256 = sha256_bytes(trace_bytes)
            sidecar = blobs["sha256"]
            if sidecar is not None:
                integ.sidecar_ok = sidecar.decode("utf-8").split()[0] == integ.trace_sha256
                if not integ.sidecar_ok:
                    integ.problems.append(f"{names['sha256']} does not match {trace_name}")
            ref = rec.get("trace_ref")
            if ref:
                integ.trace_ref_ok = ref.get("sha256") == integ.trace_sha256 and ref.get("path") == trace_name
                if not integ.trace_ref_ok:
                    integ.problems.append("trace_ref in the record does not match the trace file")
            from ..records.store import verify_session_ref
            ok, msg = verify_session_ref(names["v1"], trace, main)
            integ.session_ref_ok = ok
            if ok is False:
                integ.problems.append(msg)
        else:
            integ.problems.append(f"{trace_name} not found")
        watch_bytes = blobs["watch"]
        watch = None
        if watch_bytes is not None:
            watch = json.loads(watch_bytes)
            watch_sidecar = blobs["watch_sha256"]
            if watch_sidecar is not None:
                integ.watch_sidecar_ok = watch_sidecar.decode("utf-8").split()[0] == sha256_bytes(watch_bytes)
                if not integ.watch_sidecar_ok:
                    integ.problems.append(f"{names['watch_sha256']} does not match {names['watch']}")
            wref = (watch.get("session") or {}).get("sha256")
            if wref:
                integ.watch_session_ok = wref == sha256_bytes(raw)
                if not integ.watch_session_ok:
                    integ.problems.append(
                        f"{names['watch']} was made from a different {names['v1']} (re-run optpipe watch)")
        files = {key: names[key] for key, blob in blobs.items() if blob is not None}
        return Session(safe_date, rec, origin, trace, integ, files, watch)


# ---------------------------------------------------------------- formatting helpers (Jinja filters)
def berlin(ts: Optional[str]) -> str:
    """Any ISO timestamp (Z, offset) -> 'YYYY-MM-DD HH:MM Berlin'."""
    if not ts:
        return "-"
    if isinstance(ts, dict):
        return pair(ts)
    try:
        d = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return ts
    if d.tzinfo is None:
        return ts
    return d.astimezone(BERLIN).strftime("%Y-%m-%d %H:%M %Z")


def clock(ts: Optional[str]) -> str:
    """Wall-clock trace stamp -> 'HH:MM:SS.mmm Berlin'."""
    try:
        d = datetime.fromisoformat(str(ts).replace("Z", "+00:00")).astimezone(BERLIN)
    except ValueError:
        return str(ts)
    return f"{d:%H:%M:%S}.{d.microsecond // 1000:03d} {d:%Z}"


def pair(p: Any) -> str:
    """{ny, berlin} pair -> '07:52 ET (13:52 Berlin)'."""
    if not isinstance(p, dict) or "ny" not in p:
        return berlin(p) if isinstance(p, str) else "-"
    ny = datetime.fromisoformat(p["ny"])
    b = datetime.fromisoformat(p["berlin"]) if p.get("berlin") else ny.astimezone(BERLIN)
    return f"{ny:%Y-%m-%d %H:%M} ET ({b:%H:%M} Berlin)"


def fval(f: Any, digits: int = 4) -> str:
    """Field object -> display value."""
    if not isinstance(f, dict) or "status" not in f:
        return "-" if f is None else str(f)
    v = f.get("value")
    if v is None:
        return "-"
    if isinstance(v, float):
        return f"{v:,.{digits}f}".rstrip("0").rstrip(".") if abs(v) < 1e6 else f"{v:,.0f}"
    if isinstance(v, list):
        return f"[{len(v)} values]"
    return str(v)


def ftitle(f: Any) -> str:
    """Tooltip: status, source, as_of, method/formula/note."""
    if not isinstance(f, dict) or "status" not in f:
        return ""
    bits = [f"status: {f['status']}", f"source: {f.get('source_id') or '-'}", f"as_of: {f.get('as_of') or '-'}"]
    for k in ("method", "formula", "note"):
        if f.get(k):
            bits.append(f"{k}: {f[k]}")
    return " | ".join(bits)


def compact(v: Any) -> str:
    if v is None:
        return "-"
    if isinstance(v, (dict, list)):
        return json.dumps(v, separators=(", ", ": "), default=str)
    return str(v)
