"""Repo hygiene scans over every git-tracked (or new, not ignored) text file.

1. No absolute box paths (/workspace, /home, /tmp, ...) - docs, README and exports use repo-relative paths.
2. No secrets / tokens / private keys, and no real email addresses (placeholders only).
3. The BLS contact email ships EMPTY (set it locally via OPTPIPE_BLS_CONTACT_EMAIL).
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

ABS_PATH = re.compile(r"""(?<![\w.~-])/(?:workspace|home|tmp|root|cursor|Users|var/folders)/[^\s"'<]*""")
ABS_ALLOW = {
    "tests/test_dashboard.py",          # asserts the export contains no "/tmp/"
}
SECRET = re.compile(
    r"ghp_[A-Za-z0-9]{20,}|gho_[A-Za-z0-9]{20,}|github_pat_\w{20,}|AKIA[0-9A-Z]{16}|\bsk-[A-Za-z0-9_-]{20,}"
    r"|-----BEGIN [A-Z ]*PRIVATE KEY|xox[abprs]-[\w-]{10,}|eyJ[\w-]{20,}\.[\w-]{20,}\."
    r"|(?i:\b(?:password|passwd|api[_-]?key|secret|access[_-]?token|auth[_-]?token)\b\s*[:=]\s*['\"][^'\"\s]{6,}['\"])")
EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
EMAIL_OK = re.compile(r"@(?:example\.(?:com|org|net)|localhost)$|^fred@stlouisfed\.org$")   # placeholders; FRED's
#                                                         public contact inside captured fixture pages


def _files():
    r = subprocess.run(["git", "ls-files", "-co", "--exclude-standard"], cwd=ROOT, capture_output=True, text=True)
    if r.returncode:
        pytest.skip("not a git checkout")
    for name in r.stdout.split():
        p = ROOT / name
        if name == "tests/test_repo_hygiene.py" or not p.is_file():
            continue
        try:
            yield name, p.read_text(encoding="utf-8")
        except UnicodeDecodeError:          # PNGs etc.
            continue


def _hits(rx, allow=lambda n, m: False):
    return [f"{n}:{i}: {m.group(0)[:60]}" for n, text in _files() for i, line in enumerate(text.splitlines(), 1)
            for m in rx.finditer(line) if not allow(n, m)]


def _frozen_release_copy(n):
    """True only for a file of a FROZEN earlier spec release (scripts/sync_spec.py FROZEN_RELEASES) that is byte-identical
    to that release's own MANIFEST.sha256 and that Doodle's conftest keeps out of collection: an archived copy that is
    never run or edited (spec 1.1.4 ships _v1_1_3_backup/ as-is). The live pinned spec gets NO exception."""
    import hashlib
    import sys
    sys.path.insert(0, str(ROOT / "scripts"))
    import sync_spec
    parts = n.split("/")
    if len(parts) != 3 or parts[0] != "spec" or parts[1] not in sync_spec.FROZEN_RELEASES:
        return False
    sub = ROOT / "spec" / parts[1]
    want = sync_spec.parse_manifest((sub / "MANIFEST.sha256").read_text()).get(parts[2])
    ignored = parts[1] in (ROOT / "spec" / "conftest.py").read_text()
    return ignored and want == hashlib.sha256((ROOT / n).read_bytes()).hexdigest()


def test_no_absolute_box_paths():
    # no exception for the live pinned spec (spec 1.1.4 vendors its fixtures under spec/fixtures/), so the old
    # PINNED_SPEC_ABS_ALLOW for spec/test_invariants_v1_1_3.py is gone; only frozen archived release copies are exempt
    assert _hits(ABS_PATH, lambda n, m: n in ABS_ALLOW or _frozen_release_copy(n)) == []


def test_live_pinned_spec_has_no_absolute_paths():
    hits = [h for h in _hits(ABS_PATH) if h.startswith("spec/") and not _frozen_release_copy(h.split(":")[0])]
    assert hits == []
    assert not _frozen_release_copy("spec/test_invariants_v1_1_3.py")      # the live file is not exempt


def test_no_secrets_or_tokens():
    assert _hits(SECRET) == []


def test_only_placeholder_emails():
    assert _hits(EMAIL, lambda n, m: bool(EMAIL_OK.search(m.group(0)))) == []


def test_bls_contact_email_ships_empty(monkeypatch):
    import yaml
    from optpipe.config import load_settings
    assert yaml.safe_load((ROOT / "config" / "settings.yaml").read_text())["sources"]["bls_contact_email"] == ""
    monkeypatch.delenv("OPTPIPE_BLS_CONTACT_EMAIL", raising=False)
    assert load_settings().sources.bls_contact_email == ""


def test_scanners_catch_what_they_should():
    assert ABS_PATH.search("see /workspace/trading/spec/x.yaml") and not ABS_PATH.search("spec/risk_rules.yaml")
    assert not ABS_PATH.search("../trading/spec") and not ABS_PATH.search("https://x.org/home/a")
    fake = "ghp_" + "a1B2" * 6
    assert SECRET.search(fake) and SECRET.search("api_key: '" + "q" * 12 + "'") and not SECRET.search("token: ''")
    assert not EMAIL_OK.search("eugene@" + "gmail.com") and EMAIL_OK.search("you@example.com")
