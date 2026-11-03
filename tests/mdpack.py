"""Helpers for the market-data loader tests (FEAT-OODLE-01 PR A). Offline only: every pack is a tmp_path COPY of a
committed spec fixture (spec/fixtures/market_data*), never Oodle's live ../trading/market-data folder."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = ROOT / "spec"
FIX = SPEC / "fixtures" / "market_data"
FIX117 = SPEC / "fixtures" / "market_data_v1_1_7"
DATE = "2026-10-20"
NY = timezone(timedelta(hours=-4))
BUILT = datetime(2026, 10, 20, 9, 0, tzinfo=NY)          # pack_built_at of the spec's example pack
NOW = BUILT + timedelta(minutes=5)                       # injected clock: 09:05 New York


def copy_pack(tmp_path: Path, case: str = "valid", base: Path = FIX, date: str = DATE) -> Path:
    """tmp copy of a committed fixture pack, with deterministic modes (0644 files, 0755 dirs)"""
    root = Path(os.path.realpath(tmp_path)) / "md" / case
    root.mkdir(parents=True)
    d = root / date
    shutil.copytree(base / case / DATE, d, symlinks=True)
    for p in [root, d] + [q for q in d.iterdir()]:
        if p.is_symlink():
            continue
        p.chmod(0o755 if p.is_dir() else 0o644)
    return d


def rehash(d: Path) -> None:
    """rewrite pack.sha256 for the files it lists (same order), as an honest producer would"""
    m = d / "pack.sha256"
    out = []
    for line in m.read_text().splitlines():
        digest, name = line.split("  ", 1)
        p = d / name
        if p.is_file() and not p.is_symlink():
            digest = hashlib.sha256(p.read_bytes()).hexdigest()
        out.append(f"{digest}  {name}")
    m.write_text("\n".join(out) + "\n")


def edit_text(d: Path, name: str, old: str, new: str, *, count: int = 1, hash_it: bool = True) -> None:
    p = d / name
    t = p.read_text()
    assert old in t, (name, old)
    p.write_text(t.replace(old, new, count))
    if hash_it:
        rehash(d)


def edit_json(d: Path, name: str, fn, *, hash_it: bool = True) -> None:
    p = d / name
    doc = json.loads(p.read_text())
    fn(doc)
    p.write_text(json.dumps(doc, indent=2) + "\n")
    if hash_it:
        rehash(d)


def drop_file(d: Path, name: str) -> None:
    (d / name).unlink()
    m = d / "pack.sha256"
    m.write_text("".join(l + "\n" for l in m.read_text().splitlines() if not l.endswith("  " + name)))


def ids(errors) -> set:
    return {e.split(" ", 1)[0] for e in errors}


def load(d: Path, **kw):
    from optpipe import marketdata as md
    kw.setdefault("now", NOW)
    kw.setdefault("spec", SPEC)
    kw.setdefault("root", Path(d).parent)
    return md.load_pack(d, **kw)


def assert_closed(r, *want, rc=1):
    """fail closed: not ok, nothing exposed, every wanted id present, exit code as given"""
    got = ids(r.errors)
    assert not r.ok and r.index is None and r.snapshots == {} and r.catalysts is None and r.summary_md is None, r
    assert set(want) <= got, (want, r.errors)
    assert r.exit_code() == rc, r.errors
    assert "MD-00-CRASH" not in got or "MD-00-CRASH" in want, r.errors
