#!/usr/bin/env python3
"""Pin Doodle's spec into this repo's spec/ folder (approved by Moodle and Doodle, 2026-10-29).

  python scripts/sync_spec.py            copy ../trading/spec -> spec/ after verifying it is exactly the
                                         pinned release (VERSION + sha256 of MANIFEST.sha256 + every listed file)
  python scripts/sync_spec.py --check    CI: verify spec/ is untouched (manifest sha == pin, every file matches,
                                         nothing extra, nothing missing). Exit 1 on any mismatch or edit.

Doodle owns spec/. Never edit it here: changes go through Doodle in ../trading/spec, a new VERSION and
MANIFEST, then a new pin below (EXPECTED_*) and a re-run of this script. The live folder stays the source of
truth; local/default runs keep reading ../trading/spec directly, CI sets OPTIONS_SPEC_DIR=spec/.

Copied: the files listed in MANIFEST.sha256, the manifest itself, the two frozen v1.0.0 files from
_v1_backup/ that gen_models.py / make_spec_patch.py need (not covered by the manifest, so pinned by their
own sha256 in spec/PIN.json), and the frozen 1.1.0 / 1.1.1 / 1.1.2 releases (_v1_1_*_backup/, which Doodle's
1.1.3 back-compat tests read), each by its own MANIFEST.sha256 and pinned by FROZEN_RELEASES below. Nothing is globbed from the source: the copy and the release check follow
Doodle's MANIFEST.sha256 list exactly. Never copied or required: .venv/, __pycache__/, *.pyc, .pytest_cache/,
backup folders (*_backup/, e.g. _v1_1_0_backup/) other than the two pinned _v1_backup files.
The --check "nothing extra" scan of spec/ ignores the same build/backup clutter (Moodle, M4 review).
Local only: no network, no git operations.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE = ROOT.parent / "trading" / "spec"     # Doodle's live folder, the sibling of this repo
DEFAULT_DEST = ROOT / "spec"
EXPECTED_VERSION = "1.1.3"
EXPECTED_MANIFEST_SHA256 = "8785ee8b2f9f3bbc993bfbad0ff3ae8e7535cf15b42c507e32d145a25d6836c0"
MANIFEST, VERSION, PIN = "MANIFEST.sha256", "VERSION", "PIN.json"
V1_FILES = ("_v1_backup/session.schema.json", "_v1_backup/session.example.json")
# Frozen earlier releases that Doodle's own tests read (spec 1.1.3 test_invariants_v1_1_3.py back-compat tests run
# _v1_1_2_backup/validate_session.py and read every backup's session.example.json). Each one is pinned by the
# manifest sha256 this script pinned when that release was current (git history of this file), and is copied by
# its own MANIFEST.sha256 list only.
FROZEN_RELEASES = {
    "_v1_1_0_backup": ("1.1.0", "3b25cfe562bbd818589e82e069235b188c4f2b4aac434c04fce54d238fd95e07"),
    "_v1_1_1_backup": ("1.1.1", "477401b68298f5b4513aa9b46eed9a56df5d1097e85175bdcf8a166cd3d4f489"),
    "_v1_1_2_backup": ("1.1.2", "9fcc55b0b84b6a8be43d9e5fefda22c7f05102aef9991089b851d6946cde0fdd"),
}
IGNORED_DIRS = {"__pycache__", ".pytest_cache", ".venv"}  # never pinned; created by running Doodle's tests
IGNORED_SUFFIXES = (".pyc", ".pyo")


def is_ignored(rel: str) -> bool:
    """Build/test clutter and backup folders: never copied, never required, never counted as 'extra'."""
    parts = Path(rel).parts
    if rel in V1_FILES:
        return False
    return (bool(IGNORED_DIRS.intersection(parts)) or rel.endswith(IGNORED_SUFFIXES)
            or any(p.endswith("_backup") for p in parts[:-1]) or parts[-1].endswith("_backup"))


def is_frozen_clutter(rel: str) -> bool:
    """For the "nothing extra" scan: inside a pinned frozen release only build clutter is ignored."""
    parts = Path(rel).parts
    if parts[0] in FROZEN_RELEASES:
        return bool(IGNORED_DIRS.intersection(parts)) or rel.endswith(IGNORED_SUFFIXES)
    return is_ignored(rel)


class SpecMismatch(Exception):
    pass


def sha256(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def parse_manifest(text: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for n, line in enumerate(text.splitlines(), 1):
        if not line.strip():
            continue
        digest, _, name = line.partition("  ")
        name = name.lstrip("*")
        if len(digest) != 64 or not name or name.startswith("/") or ".." in Path(name).parts:
            raise SpecMismatch(f"{MANIFEST} line {n} is not a sha256sum line: {line!r}")
        if is_ignored(name):
            raise SpecMismatch(f"{MANIFEST} line {n} lists build/backup clutter: {name!r}")
        out[name] = digest
    return out


def verify_release(d: Path, version: str, manifest_sha: str) -> dict[str, str]:
    """VERSION == version, sha256(MANIFEST) == manifest_sha, every listed file present with its sha256."""
    if not (d / MANIFEST).is_file() or not (d / VERSION).is_file():
        raise SpecMismatch(f"{d}: no {MANIFEST} / {VERSION}")
    if (got := (d / VERSION).read_text().strip()) != version:
        raise SpecMismatch(f"{d}/{VERSION} is {got!r}, pinned {version!r}")
    if (got := sha256(d / MANIFEST)) != manifest_sha:
        raise SpecMismatch(f"{d}/{MANIFEST} sha256 {got} != pinned {manifest_sha}")
    files = parse_manifest((d / MANIFEST).read_text())
    if VERSION not in files:
        raise SpecMismatch(f"{MANIFEST} does not list {VERSION}")
    for name, digest in files.items():
        p = d / name
        if not p.is_file():
            raise SpecMismatch(f"{name} listed in {MANIFEST} but missing")
        if (got := sha256(p)) != digest:
            raise SpecMismatch(f"{name} sha256 {got} != manifest {digest} (edited?)")
    return files


def sync(source: Path, dest: Path, version: str, manifest_sha: str) -> dict:
    files = verify_release(source, version, manifest_sha)
    frozen = {}
    for sub, (fver, fsha) in FROZEN_RELEASES.items():
        try:
            flist = verify_release(source / sub, fver, fsha)
        except SpecMismatch as e:
            raise SpecMismatch(f"frozen release {sub}: {e}") from None
        frozen[sub] = {"version": fver, "manifest_sha256": fsha, "files": len(flist)}
    v1 = {}
    for name in V1_FILES:
        if not (source / name).is_file():
            raise SpecMismatch(f"{source}/{name} missing (needed by gen_models.py / make_spec_patch.py)")
        v1[name] = sha256(source / name)
    pin = {
        "owner": "Doodle",
        "note": "Pinned copy of Doodle's spec. Do not edit: changes go through Doodle in ../trading/spec, "
                "then scripts/sync_spec.py with a new pin. CI (OPTIONS_SPEC_DIR=spec/) fails on any edit.",
        "source": os.path.relpath(source.resolve(), ROOT) if source.is_absolute() else str(source),
        "version": version,
        "manifest_sha256": manifest_sha,
        "files": dict(sorted(files.items())),
        "v1_backup_files": v1,
        "frozen_releases": frozen,
        "synced_at": datetime.now(ZoneInfo("Europe/Berlin")).isoformat(timespec="seconds"),
        "synced_by": "scripts/sync_spec.py",
    }
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix=".spec-sync-", dir=dest.parent))
    try:
        frozen_names = [f"{sub}/{n}" for sub in FROZEN_RELEASES
                        for n in [*parse_manifest((source / sub / MANIFEST).read_text()), MANIFEST]]
        for name in [*files, MANIFEST, *V1_FILES, *frozen_names]:
            (tmp / name).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source / name, tmp / name)
        (tmp / PIN).write_text(json.dumps(pin, indent=2) + "\n")
        verify_pinned(tmp, version, manifest_sha)                 # the copy must pass the CI check
        if dest.exists():
            shutil.rmtree(dest)
        os.replace(tmp, dest)
    finally:
        if tmp.exists():
            shutil.rmtree(tmp)
    return pin


def verify_pinned(d: Path, version: str = EXPECTED_VERSION, manifest_sha: str = EXPECTED_MANIFEST_SHA256) -> dict:
    """CI check of the pinned copy: release verifies, PIN.json agrees, v1 files match, nothing else present."""
    if not (d / PIN).is_file():
        raise SpecMismatch(f"{d}/{PIN} missing (run scripts/sync_spec.py)")
    pin = json.loads((d / PIN).read_text())
    if (pin.get("version"), pin.get("manifest_sha256")) != (version, manifest_sha):
        raise SpecMismatch(f"{PIN} pins {pin.get('version')} / {pin.get('manifest_sha256')}, "
                           f"expected {version} / {manifest_sha}")
    files = verify_release(d, version, manifest_sha)
    if pin.get("files") != dict(sorted(files.items())):
        raise SpecMismatch(f"{PIN} file list differs from {MANIFEST}")
    for name, digest in (pin.get("v1_backup_files") or {}).items():
        if name not in V1_FILES or not (d / name).is_file() or sha256(d / name) != digest:
            raise SpecMismatch(f"{name} missing or edited (pinned {digest})")
    frozen = pin.get("frozen_releases") or {}
    if set(frozen) != set(FROZEN_RELEASES):
        raise SpecMismatch(f"{PIN} frozen releases {sorted(frozen)} != {sorted(FROZEN_RELEASES)}")
    allowed = {*files, MANIFEST, PIN, *V1_FILES}
    for sub, (fver, fsha) in FROZEN_RELEASES.items():
        if (frozen[sub].get("version"), frozen[sub].get("manifest_sha256")) != (fver, fsha):
            raise SpecMismatch(f"{PIN} pins {sub} as {frozen[sub]}, expected {fver} / {fsha}")
        try:
            flist = verify_release(d / sub, fver, fsha)
        except SpecMismatch as e:
            raise SpecMismatch(f"frozen release {sub}: {e}") from None
        allowed |= {f"{sub}/{n}" for n in [*flist, MANIFEST]}
    extra = sorted(rel for p in d.rglob("*") if p.is_file()
                   and (rel := p.relative_to(d).as_posix()) not in allowed and not is_frozen_clutter(rel))
    if extra:
        raise SpecMismatch(f"files not in the pinned spec: {extra} (changes go through Doodle)")
    return pin


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    ap.add_argument("--dest", type=Path, default=DEFAULT_DEST)
    ap.add_argument("--version", default=EXPECTED_VERSION)
    ap.add_argument("--manifest-sha256", default=EXPECTED_MANIFEST_SHA256)
    ap.add_argument("--check", action="store_true", help="verify the pinned copy only (CI); no copying")
    a = ap.parse_args(argv)
    try:
        if a.check:
            pin = verify_pinned(a.dest, a.version, a.manifest_sha256)
            print(f"OK: {a.dest} is the pinned spec {pin['version']} (manifest sha256 {pin['manifest_sha256']}, "
                  f"{len(pin['files'])} files + {len(pin['v1_backup_files'])} v1 files + "
                  f"frozen {', '.join(v['version'] for v in pin['frozen_releases'].values())}, synced {pin['synced_at']})")
        else:
            pin = sync(a.source, a.dest, a.version, a.manifest_sha256)
            print(f"synced {a.source} -> {a.dest}: spec {pin['version']}, manifest sha256 {pin['manifest_sha256']}, "
                  f"{len(pin['files'])} files + {len(pin['v1_backup_files'])} v1 files + "
                  f"frozen {', '.join(v['version'] for v in pin['frozen_releases'].values())}; pin record {a.dest / PIN}")
    except SpecMismatch as e:
        print(f"spec pin mismatch: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
