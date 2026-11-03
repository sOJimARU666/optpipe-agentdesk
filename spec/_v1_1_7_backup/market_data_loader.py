"""Reference LOADER for Oodle's market-data packs (spec 1.1.7). The app (optpipe.marketdata) mirrors this.
PAPER ONLY. Read-only, pure file I/O, no network, never writes. Fails closed: `ok` is True only if every rule
MD-01..MD-12 passed; on any error `index` is None and `snapshots` is empty, so no idea can be built from the pack.

API
  load_pack(date_dir) -> PackResult            # never raises
  PackResult.ok: bool                          # True iff errors == []
  PackResult.date: str | None                  # the date dir name (YYYY-MM-DD, America/New_York)
  PackResult.index: dict | None                # parsed index.json (None unless ok)
  PackResult.snapshots: dict[str, dict]        # ticker -> parsed ticker_snapshot ({} unless ok)
  PackResult.summary_md: str | None            # summary.md text if present (None unless ok)
  PackResult.errors: list[str]                 # MD-xx id tokens first; exit_code() maps them to 0 / 1 / 2
  PackResult.exit_code() -> int                # same contract as validate_market_snapshot.py
  field_usable(field) -> bool                  # True only for status OK with a value (STALE / UNKNOWN / UNCONFIRMED: unusable)
  usable_value(field)                          # the value if field_usable, else None

Layout (market_data_rules.yaml `layout`): <root>/<YYYY-MM-DD>/ holds pack.sha256, index.json, one <TICKER>.json per
index entry and optionally summary.md; nothing else. pack.sha256 lists every other file (sha256sum format).
Safety: the date dir must be a real directory reached without any symlink (realpath == absolute path); every entry
is lstat-ed (no symlinks, no sub-directories, no dotfiles, no FIFOs / sockets / devices, no executable bit, no
other names or extensions such as .py); manifest names must be plain file names (no /, \\, .., absolute paths); files
are opened relative to the date dir's fd with O_NOFOLLOW, fstat-checked as regular files, size-capped, and the
sha256 is computed over the exact bytes that are then parsed (no time-of-check / time-of-use gap)."""
from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from dataclasses import dataclass, field as dc_field
from pathlib import Path

import validate_market_snapshot as vms

LAYOUT = vms.RULES["layout"]
MANIFEST, INDEX, SUMMARY = LAYOUT["manifest"], LAYOUT["index"], LAYOUT["summary"]
TICKER_FILE = re.compile(LAYOUT["ticker_file_pattern"])
MAX_BYTES = int(LAYOUT["max_file_bytes"])
LINE = re.compile(r"^([0-9a-f]{64})  (.+)$")


@dataclass
class PackResult:
    ok: bool = False
    date: str | None = None
    index: dict | None = None
    snapshots: dict = dc_field(default_factory=dict)
    summary_md: str | None = None
    errors: list = dc_field(default_factory=list)

    def exit_code(self) -> int:
        return vms.exit_code(self.errors)


def field_usable(f) -> bool:
    """fail closed: only an OK field with a value may feed an idea"""
    return isinstance(f, dict) and f.get("status") == "OK" and f.get("value") is not None


def usable_value(f):
    return f["value"] if field_usable(f) else None


def allowed_name(name: str) -> bool:
    return name in (MANIFEST, INDEX, SUMMARY) or bool(TICKER_FILE.fullmatch(name))


def _unsafe_name(name: str) -> str | None:
    if os.path.isabs(name) or name.startswith(("/", "\\")) or re.match(r"^[A-Za-z]:", name):
        return "absolute path"
    if ".." in re.split(r"[/\\]", name):
        return "'..' component escapes the date dir"
    if "/" in name or "\\" in name or "\x00" in name:
        return "path separator (names must be plain file names in the date dir)"
    if name in (".", ".."):
        return "'.' / '..' is not a file name"
    if name.startswith("."):
        return "dotfile"
    return None


def _read(dfd: int, name: str, errors: list[str]) -> bytes | None:
    """read one regular file relative to the date dir fd, never following a symlink; None + error on any problem"""
    try:
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | getattr(os, "O_CLOEXEC", 0), dir_fd=dfd)
    except OSError as ex:
        errors.append(f"MD-01-UNREADABLE {name}: cannot open without following links: {type(ex).__name__}: {ex.strerror}")
        return None
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode):
            errors.append(f"MD-01-NOT-REGULAR {name}: not a regular file")
            return None
        if st.st_size > MAX_BYTES:
            errors.append(f"MD-01-SIZE {name}: {st.st_size} bytes > max_file_bytes {MAX_BYTES}")
            return None
        chunks, n = [], 0
        while True:
            b = os.read(fd, 65536)
            if not b:
                break
            chunks.append(b); n += len(b)
            if n > MAX_BYTES:
                errors.append(f"MD-01-SIZE {name}: grew beyond max_file_bytes {MAX_BYTES} while reading")
                return None
        return b"".join(chunks)
    except OSError as ex:
        errors.append(f"MD-01-UNREADABLE {name}: {type(ex).__name__}: {ex.strerror}")
        return None
    finally:
        os.close(fd)


def _parse_json(name: str, data: bytes, errors: list[str]):
    try:
        doc = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as ex:
        errors.append(f"MD-03-JSON {name}: not UTF-8 JSON: {type(ex).__name__}: {ex}")
        return None
    if not isinstance(doc, dict):
        errors.append(f"MD-03-JSON {name}: a JSON {type(doc).__name__}, not an object")
        return None
    return doc


def load_pack(date_dir) -> PackResult:
    """load + verify + validate one date dir. Never raises; any exception is MD-00-CRASH (fail closed)."""
    try:
        return _load(Path(date_dir))
    except Exception as ex:  # noqa: BLE001 - fail closed, never a pass
        return PackResult(errors=[f"MD-00-CRASH {date_dir}: loader raised {type(ex).__name__}: {ex}"])


def _load(date_dir: Path) -> PackResult:
    errors: list[str] = []
    res = PackResult(date=date_dir.name, errors=errors)
    absdir = os.path.abspath(date_dir)
    try:
        st = os.lstat(absdir)
    except OSError as ex:
        errors.append(f"MD-01-DIR {date_dir}: date dir missing or unreadable: {type(ex).__name__}: {ex.strerror}")
        return res
    if stat.S_ISLNK(st.st_mode):
        errors.append(f"MD-01-SYMLINK {date_dir}: the date dir is a symlink")
        return res
    if not stat.S_ISDIR(st.st_mode):
        errors.append(f"MD-01-DIR {date_dir}: not a directory")
        return res
    if os.path.realpath(absdir) != absdir:
        errors.append(f"MD-01-SYMLINK {date_dir}: the path to the date dir goes through a symlink")
        return res
    try:
        dfd = os.open(absdir, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0))
    except OSError as ex:
        errors.append(f"MD-01-DIR {date_dir}: cannot open date dir: {type(ex).__name__}: {ex.strerror}")
        return res
    try:
        _load_fd(dfd, date_dir.name, res)
    finally:
        os.close(dfd)
    if errors:
        res.index, res.snapshots, res.summary_md = None, {}, None
    res.ok = not errors
    return res


def _load_fd(dfd: int, dir_name: str, res: PackResult) -> None:
    errors = res.errors
    # ---- MD-01: every entry in the date dir, without following anything
    on_disk: set[str] = set()
    rejected: set[str] = set()   # present but never opened (symlink, directory, non-regular, bad name)
    for name in sorted(os.listdir(dfd)):
        st = os.stat(name, dir_fd=dfd, follow_symlinks=False)
        if stat.S_ISLNK(st.st_mode):
            errors.append(f"MD-01-SYMLINK {name}: symlinks are not allowed in a date dir")
        elif stat.S_ISDIR(st.st_mode):
            errors.append(f"MD-01-NAME {name}: sub-directories are not allowed in a date dir")
        elif not stat.S_ISREG(st.st_mode):
            errors.append(f"MD-01-NOT-REGULAR {name}: not a regular file (FIFO, socket or device)")
        elif name.startswith(".") or not allowed_name(name):
            errors.append(f"MD-01-NAME {name}: not an allowed pack file name ({MANIFEST}, {INDEX}, {SUMMARY}, <TICKER>.json)")
        else:
            if st.st_mode & 0o111:
                errors.append(f"MD-01-EXEC {name}: executable bit set (pack files are data, never scripts)")
            if st.st_size > MAX_BYTES:
                errors.append(f"MD-01-SIZE {name}: {st.st_size} bytes > max_file_bytes {MAX_BYTES}")
            on_disk.add(name)
            continue
        on_disk.add(name)   # present but rejected: counts as present for the manifest comparison, never opened
        rejected.add(name)
    # ---- MD-02: the manifest
    if MANIFEST not in on_disk:
        errors.append(f"MD-02-MISSING {MANIFEST}: no manifest in the date dir (fail closed: nothing is loaded)")
        return
    raw = _read(dfd, MANIFEST, errors)
    if raw is None:
        return
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        errors.append(f"MD-02-FORMAT {MANIFEST}: not UTF-8")
        return
    listed: dict[str, str] = {}
    for n, line in enumerate(text.splitlines(), 1):
        m = LINE.match(line)
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
        if not allowed_name(name):
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
    # ---- read + hash every listed file that is present (hash over the exact bytes parsed)
    verified: dict[str, bytes] = {}
    for name in sorted((set(listed) & on_disk) - rejected):
        data = _read(dfd, name, errors)
        if data is None:
            continue
        got = hashlib.sha256(data).hexdigest()
        if got != listed[name]:
            errors.append(f"MD-02-HASH {name}: sha256 {got} != {MANIFEST} {listed[name]}")
            continue
        verified[name] = data
    # ---- MD-03..MD-10 per file
    if INDEX not in listed:
        errors.append(f"MD-02-INDEX {INDEX}: not listed in {MANIFEST}")
    docs: dict[str, dict] = {}
    for name, data in sorted(verified.items()):
        if name == SUMMARY:
            try:
                res.summary_md = data.decode("utf-8")
            except UnicodeDecodeError:
                errors.append(f"MD-03-JSON {SUMMARY}: not UTF-8 text")
                continue
            errors.extend(vms.email_errors(res.summary_md, SUMMARY))
            continue
        doc = _parse_json(name, data, errors)
        if doc is None:
            continue
        want = "pack_index" if name == INDEX else "ticker_snapshot"
        if doc.get("kind") != want:
            errors.append(f"MD-03-SCHEMA {name}: kind {doc.get('kind')!r}, expected {want!r}")
            continue
        e = vms.validate_document(doc, name)
        errors.extend(e)
        if not any(x.startswith("MD-03-SCHEMA") for x in e):
            docs[name] = doc
    index = docs.pop(INDEX, None)
    if index is None:
        return
    # ---- MD-02-INDEX: the ticker files listed == the files the index names
    named = {t["file"] for t in index["tickers"]}
    for name in sorted(named - set(listed)):
        errors.append(f"MD-02-INDEX {name}: named in {INDEX} but not listed in {MANIFEST}")
    for name in sorted({n for n in listed if TICKER_FILE.fullmatch(n) and n != INDEX} - named):
        errors.append(f"MD-02-INDEX {name}: listed in {MANIFEST} but not named in {INDEX}")
    # ---- MD-01-DATE, MD-11, MD-12
    errors.extend(vms.pack_errors(index, docs, dir_name))
    res.index = index
    res.snapshots = {d["ticker"]: d for d in docs.values()}
