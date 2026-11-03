"""Runtime pin check of Doodle's spec folder (SOL-ACCEPT-001 G8).

`optpipe run`, `validate`, `watch` and `spec-check` refuse a spec folder that is not the pinned release: VERSION
must be the pinned version, sha256(MANIFEST.sha256) the pinned manifest hash, and every file the manifest lists
present with its listed sha256 (so an edited validate_session.py, a changed VERSION or a missing validator is
SPEC-PIN-MISMATCH, exit 2, never an OK).

Single source of truth: the pin is READ from scripts/sync_spec.py (its literal EXPECTED_VERSION /
EXPECTED_MANIFEST_SHA256 assignments, parsed with ast, never executed), so a re-pin through
scripts/sync_spec.py re-pins the runtime too. If that file is missing or its constants are not plain literals of
the right shape, the pin is unknown and every spec folder is SPEC-PIN-MISMATCH (fail closed, never an OK).
"""
from __future__ import annotations

import ast
import hashlib
import re
from pathlib import Path
from typing import Optional, Tuple

PIN_SOURCE = Path(__file__).resolve().parents[2] / "scripts" / "sync_spec.py"
_PIN_NAMES = ("EXPECTED_VERSION", "EXPECTED_MANIFEST_SHA256")


def load_pin(source: Path = PIN_SOURCE) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    """(version, manifest_sha256, None) read from scripts/sync_spec.py, or (None, None, why) if unreadable."""
    try:
        tree = ast.parse(Path(source).read_text(encoding="utf-8"), filename=str(source))
    except (OSError, SyntaxError, ValueError) as e:
        return None, None, f"pin source {source.name} unreadable ({type(e).__name__})"
    found: dict = {}
    for node in tree.body:                                  # module-level `NAME = "literal"` only
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name) \
                and node.targets[0].id in _PIN_NAMES:
            name = node.targets[0].id
            if name in found or not (isinstance(node.value, ast.Constant) and isinstance(node.value.value, str)):
                return None, None, f"pin source {source.name}: {name} is not a single string literal"
            found[name] = node.value.value
    version, sha = found.get(_PIN_NAMES[0]), found.get(_PIN_NAMES[1])
    if not version or not re.fullmatch(r"\d+\.\d+\.\d+", version):
        return None, None, f"pin source {source.name}: no valid {_PIN_NAMES[0]}"
    if not sha or not re.fullmatch(r"[0-9a-f]{64}", sha):
        return None, None, f"pin source {source.name}: no valid {_PIN_NAMES[1]}"
    return version, sha, None


EXPECTED_VERSION, EXPECTED_MANIFEST_SHA256, PIN_SOURCE_ERROR = load_pin()
SPEC_PIN_MISMATCH = "SPEC-PIN-MISMATCH"
MANIFEST, VERSION = "MANIFEST.sha256", "VERSION"


def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def pin_problem(root: Optional[Path], version: Optional[str] = None,
                manifest_sha: Optional[str] = None) -> Optional[str]:
    """None when `root` is exactly the pinned release (default: the module pin), else why not (one line, no OK)."""
    version = version or EXPECTED_VERSION
    manifest_sha = manifest_sha or EXPECTED_MANIFEST_SHA256
    if not version or not manifest_sha:
        return PIN_SOURCE_ERROR or "no runtime pin (scripts/sync_spec.py constants unreadable)"
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
