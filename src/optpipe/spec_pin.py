"""Runtime pin check of Doodle's spec folder (SOL-ACCEPT-001 G8).

`optpipe run`, `validate`, `watch` and `spec-check` refuse a spec folder that is not the pinned release: VERSION
must be the pinned version, sha256(MANIFEST.sha256) the pinned manifest hash, and every file the manifest lists
present with its listed sha256 (so an edited validate_session.py, a changed VERSION or a missing validator is
SPEC-PIN-MISMATCH, exit 2, never an OK).

The pin is the same pair as scripts/sync_spec.py EXPECTED_VERSION / EXPECTED_MANIFEST_SHA256
(tests/test_sol_g8_spec_pin.py asserts they agree); a new spec release re-pins both, through Doodle.
"""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Optional

EXPECTED_VERSION = "1.1.5"
EXPECTED_MANIFEST_SHA256 = "ecc73140a23fc5b4b0e4bedf46020981bca5cefcfbda8011c3bd72ef206bd7a4"
SPEC_PIN_MISMATCH = "SPEC-PIN-MISMATCH"
MANIFEST, VERSION = "MANIFEST.sha256", "VERSION"


def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def pin_problem(root: Optional[Path], version: str = EXPECTED_VERSION,
                manifest_sha: str = EXPECTED_MANIFEST_SHA256) -> Optional[str]:
    """None when `root` is exactly the pinned release, else why not (one line, no OK)."""
    if root is None or not Path(root).is_dir():
        return f"spec dir {root} not found"
    d = Path(root)
    if not (d / VERSION).is_file() or not (d / MANIFEST).is_file():
        return f"no {VERSION} / {MANIFEST} in the spec dir"
    try:
        got = (d / VERSION).read_text().strip()
        if got != version:
            return f"spec {VERSION} is {got!r}, pinned {version!r}"
        if (h := _sha(d / MANIFEST)) != manifest_sha:
            return f"{MANIFEST} sha256 {h} != pinned {manifest_sha}"
        listed = 0
        for n, line in enumerate((d / MANIFEST).read_text().splitlines(), 1):
            if not line.strip():
                continue
            digest, _, name = line.partition("  ")
            name = name.lstrip("*")
            if len(digest) != 64 or not name or name.startswith("/") or ".." in Path(name).parts:
                return f"{MANIFEST} line {n} is not a sha256sum line"
            p = d / name
            if not p.is_file():
                return f"{name} listed in {MANIFEST} but missing"
            if _sha(p) != digest:
                return f"{name} sha256 differs from {MANIFEST} (edited?)"
            listed += 1
        if not listed:
            return f"{MANIFEST} lists no files"
    except OSError as e:
        return f"spec dir unreadable ({type(e).__name__}: {e})"
    return None


def pin_error(root: Optional[Path]) -> Optional[str]:
    """The SPEC-PIN-MISMATCH message for `root`, or None when it is the pinned release."""
    why = pin_problem(root)
    if why is None:
        return None
    return (f"{SPEC_PIN_MISMATCH}: {why}; the spec folder is not the pinned release {EXPECTED_VERSION} "
            "(re-sync with scripts/sync_spec.py); nothing is validated (fail closed)")
