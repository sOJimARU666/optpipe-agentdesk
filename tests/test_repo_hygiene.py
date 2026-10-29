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


def test_no_absolute_box_paths():
    assert _hits(ABS_PATH, lambda n, m: n in ABS_ALLOW) == []


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
