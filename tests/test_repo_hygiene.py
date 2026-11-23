"""Repo hygiene scans over every git-tracked (or new, not ignored) text file.

1. No absolute box paths (/workspace, /home, /tmp, ...) - docs, README and exports use repo-relative paths.
2. No secrets / tokens / private keys, and no real email addresses (placeholders only).
3. The BLS contact email ships EMPTY (set it locally via OPTPIPE_BLS_CONTACT_EMAIL).
"""
from __future__ import annotations

import os
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
# A whole environment-style assignment, not a key-name mention or a Python call.
# This supplements known credential formats; it is not proof that arbitrary text is secret-free.
ENV_SECRET = re.compile(
    r"(?i)^[ \t]*(?:export[ \t]+)?(?:[a-z_][a-z0-9_]*_)?"
    r"(?:password|passwd|api_?key|secret|access_?key|private_?key|(?:access|auth|refresh)_token|token)"
    r"[ \t]*=[ \t]*[^ \t\r\n'\"#(){}\[\]]{6,}[ \t]*(?:#[^\r\n]*)?$")
EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
EMAIL_OK = re.compile(r"@(?:example\.(?:com|org|net)|localhost)$|^fred@stlouisfed\.org$")   # placeholders; FRED's
#                                                         public contact inside captured fixture pages


def _git_paths(*, tracked_only=False):
    args = ["git", "ls-files", "--cached", "-z"]
    if not tracked_only:
        args += ["--others", "--exclude-standard"]
    r = subprocess.run(args, cwd=ROOT, capture_output=True)
    if r.returncode:
        pytest.skip("not a git checkout")
    # Git's NUL form preserves spaces, newlines and non-ASCII filenames without C quoting.
    return list(dict.fromkeys(os.fsdecode(name) for name in r.stdout.split(b"\0") if name))


def _files():
    for name in _git_paths():
        p = ROOT / name
        if name == "tests/test_repo_hygiene.py" or not p.is_file():
            continue
        try:
            yield name, p.read_text(encoding="utf-8")
        except UnicodeDecodeError:          # PNGs etc.
            continue


def _display_name(name):
    # Keep filenames useful without allowing credential-shaped names or control characters into logs.
    name = ENV_SECRET.sub("[redacted]", SECRET.sub("[redacted]", name))
    return name.encode("unicode_escape").decode("ascii")


def _hits(rx, allow=lambda n, m: False):
    return [f"{_display_name(n)}:{i}: matched content (redacted)" for n, text in _files() for i, line in enumerate(text.splitlines(), 1)
            for m in rx.finditer(line) if not allow(n, m)]


def _empty_environment_template(path):
    if path.is_symlink() or not path.is_file():
        return False
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError):
        return False
    blank_assignment = re.compile(r"(?:export[ \t]+)?[A-Za-z_][A-Za-z0-9_]*[ \t]*=[ \t]*(?:\"\"|'')?[ \t]*(?:#.*)?")
    return all(not line.strip() or line.lstrip().startswith("#")
               or blank_assignment.fullmatch(line.strip()) for line in lines)


def _credential_file_hits():
    hits = []
    key_names = {"id_rsa", "id_dsa", "id_ecdsa", "id_ed25519"}
    key_suffixes = {".pem", ".key", ".p12", ".pfx"}
    for name in _git_paths(tracked_only=True):
        parts = Path(name).parts
        env_parts = [p for p in parts if p.lower() == ".env" or p.lower().startswith(".env.")]
        if env_parts:
            # Only this exact final filename is an exception; every value must be empty.
            template = len(env_parts) == 1 and parts[-1] == ".env.example"
            if not template or not _empty_environment_template(ROOT / name):
                hits.append(f"{_display_name(name)}: prohibited environment file or populated template")
        if parts[-1].lower() in key_names or Path(parts[-1].lower()).suffix in key_suffixes:
            hits.append(f"{_display_name(name)}: prohibited credential-container filename")
    return hits


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
    manifest = (sub / "MANIFEST.sha256").read_bytes()
    if hashlib.sha256(manifest).hexdigest() != sync_spec.FROZEN_RELEASES[parts[1]][1]:
        return False                                  # the backup's own manifest must be the pinned one (PR #6 nit)
    want = sync_spec.parse_manifest(manifest.decode()).get(parts[2])
    return _collect_ignored(parts[1]) and want == hashlib.sha256((ROOT / n).read_bytes()).hexdigest()


def _collect_ignored(sub: str) -> bool:
    """`sub` is an entry of spec/conftest.py's collect_ignore list (parsed, not a substring match)."""
    import ast
    for node in ast.walk(ast.parse((ROOT / "spec" / "conftest.py").read_text())):
        if isinstance(node, ast.Assign) and any(getattr(t, "id", None) == "collect_ignore" for t in node.targets):
            try:
                vals = ast.literal_eval(node.value)
            except ValueError:
                return False
            return sub in {str(v).rstrip("/") for v in vals}
    return False


def test_no_absolute_box_paths():
    # no exception for the live pinned spec (spec 1.1.4 vendors its fixtures under spec/fixtures/), so the old
    # PINNED_SPEC_ABS_ALLOW for spec/test_invariants_v1_1_3.py is gone; only frozen archived release copies are exempt
    assert _hits(ABS_PATH, lambda n, m: n in ABS_ALLOW or _frozen_release_copy(n)) == []


def test_live_pinned_spec_has_no_absolute_paths():
    hits = [h for h in _hits(ABS_PATH) if h.startswith("spec/") and not _frozen_release_copy(h.split(":")[0])]
    assert hits == []
    assert not _frozen_release_copy("spec/test_invariants_v1_1_3.py")      # the live file is not exempt


def test_no_secrets_or_tokens():
    # Ignores prevent accidental staging; this separate tracked-name gate also catches forced adds.
    assert _credential_file_hits() + _hits(SECRET) + _hits(ENV_SECRET) == []


def test_only_placeholder_emails():
    assert _hits(EMAIL, lambda n, m: bool(EMAIL_OK.search(m.group(0)))) == []


def test_bls_contact_email_ships_empty(monkeypatch):
    import yaml
    from optpipe.config import load_settings
    assert yaml.safe_load((ROOT / "config" / "settings.yaml").read_text())["sources"]["bls_contact_email"] == ""
    monkeypatch.delenv("OPTPIPE_BLS_CONTACT_EMAIL", raising=False)
    assert load_settings().sources.bls_contact_email == ""


def test_frozen_copy_exemption_needs_the_pinned_manifest(tmp_path, monkeypatch):
    """NEGATIVE (PR #6 nit, passed on 49debf0): a frozen backup whose files AND MANIFEST.sha256 are edited together
    (manifest re-hashed) is no longer exempt from the scan, without relying on sync_spec --check."""
    import shutil
    import sys
    fake = tmp_path / "repo"
    (fake / "spec").mkdir(parents=True)
    (fake / "scripts").mkdir()
    shutil.copy(ROOT / "scripts" / "sync_spec.py", fake / "scripts")
    shutil.copy(ROOT / "spec" / "conftest.py", fake / "spec")
    shutil.copytree(ROOT / "spec" / "_v1_1_3_backup", fake / "spec" / "_v1_1_3_backup")
    n = "spec/_v1_1_3_backup/test_invariants_v1_1_3.py"
    monkeypatch.setattr(sys.modules[__name__], "ROOT", fake)
    assert _frozen_release_copy(n)                                       # untouched copy: exempt
    import hashlib
    p = fake / n
    p.write_text(p.read_text() + "\n# " + "/" + "tmp/evil\n")
    m = fake / "spec" / "_v1_1_3_backup" / "MANIFEST.sha256"
    old = hashlib.sha256((ROOT / n).read_bytes()).hexdigest()
    m.write_text(m.read_text().replace(old, hashlib.sha256(p.read_bytes()).hexdigest()))
    assert not _frozen_release_copy(n)                                   # re-hashed manifest != pin: not exempt
    assert not _collect_ignored("_v1_1_9_backup") and _collect_ignored("_v1_1_3_backup")


def test_scanners_catch_what_they_should():
    assert ABS_PATH.search("see /workspace/trading/spec/x.yaml") and not ABS_PATH.search("spec/risk_rules.yaml")
    assert not ABS_PATH.search("../trading/spec") and not ABS_PATH.search("https://x.org/home/a")
    fake = "ghp_" + "a1B2" * 6
    assert SECRET.search(fake) and SECRET.search("api_key: '" + "q" * 12 + "'") and not SECRET.search("token: ''")
    assert not EMAIL_OK.search("eugene@" + "gmail.com") and EMAIL_OK.search("you@example.com")
