"""scripts/sync_spec.py: pinned copy of Doodle's spec in spec/ (VERSION + MANIFEST sha), CI --check catches edits.
Works on temp copies only; never touches ../trading/spec or the repo's spec/."""
from __future__ import annotations

import importlib.util
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
_s = importlib.util.spec_from_file_location("sync_spec", ROOT / "scripts" / "sync_spec.py")
sync_spec = importlib.util.module_from_spec(_s)
_s.loader.exec_module(sync_spec)


@pytest.fixture(scope="module")
def source():
    """Hermetic: the release under test is the repo's own pinned spec/ (it carries MANIFEST, VERSION, every listed
    file and the two _v1_backup files), never Doodle's live folder, which may be mid-edit."""
    src = ROOT / "spec"
    sync_spec.verify_pinned(src)
    return src


@pytest.fixture()
def pinned(source, tmp_path):
    d = tmp_path / "spec"
    sync_spec.sync(source, d, sync_spec.EXPECTED_VERSION, sync_spec.EXPECTED_MANIFEST_SHA256)
    return d


def test_pin_constants():
    assert sync_spec.EXPECTED_VERSION == "1.1.2"
    assert sync_spec.EXPECTED_MANIFEST_SHA256 == "9fcc55b0b84b6a8be43d9e5fefda22c7f05102aef9991089b851d6946cde0fdd"


def test_sync_writes_manifest_files_and_pin_record(pinned, source):
    files = sync_spec.parse_manifest((source / "MANIFEST.sha256").read_text())
    assert len(files) == 21 and "test_direction_v1_1_2.py" in files
    pin = json.loads((pinned / "PIN.json").read_text())
    assert pin["version"] == "1.1.2" and pin["manifest_sha256"] == sync_spec.EXPECTED_MANIFEST_SHA256
    assert pin["owner"] == "Doodle" and pin["files"] == dict(sorted(files.items()))
    assert pin["synced_at"].endswith(("+01:00", "+02:00"))               # Berlin time
    for name, digest in files.items():
        assert sync_spec.sha256(pinned / name) == digest
    assert set(pin["v1_backup_files"]) == set(sync_spec.V1_FILES)
    assert not (pinned / ".venv").exists() and not (pinned / "_v1_backup" / "risk_rules.yaml").exists()
    assert sync_spec.verify_pinned(pinned)["version"] == "1.1.2"


@pytest.mark.parametrize("tamper", ["edit_file", "edit_manifest", "extra_file", "missing_file", "edit_v1", "edit_pin"])
def test_check_fails_on_any_edit(pinned, tamper):
    if tamper == "edit_file":
        p = pinned / "risk_rules.yaml"
        p.write_text(p.read_text().replace("threshold", "threshold ", 1))
    elif tamper == "edit_manifest":
        with (pinned / "MANIFEST.sha256").open("a") as f:
            f.write("\n")
    elif tamper == "extra_file":
        (pinned / "local_notes.md").write_text("mine")
    elif tamper == "missing_file":
        (pinned / "watcher_rules.yaml").unlink()
    elif tamper == "edit_v1":
        p = pinned / "_v1_backup" / "session.schema.json"
        p.write_text(p.read_text() + " ")
    elif tamper == "edit_pin":
        pin = json.loads((pinned / "PIN.json").read_text())
        pin["version"] = "9.9.9"
        (pinned / "PIN.json").write_text(json.dumps(pin))
    with pytest.raises(sync_spec.SpecMismatch):
        sync_spec.verify_pinned(pinned)
    r = subprocess.run([sys.executable, str(ROOT / "scripts" / "sync_spec.py"), "--check", "--dest", str(pinned)],
                       capture_output=True, text=True)
    assert r.returncode == 1 and "spec pin mismatch" in r.stderr


def test_pycache_from_running_doodles_tests_is_ignored(pinned):
    (pinned / "__pycache__").mkdir()
    (pinned / "__pycache__" / "x.pyc").write_bytes(b"\0")
    sync_spec.verify_pinned(pinned)


def test_sync_refuses_a_source_that_is_not_the_pinned_release(source, tmp_path):
    src = tmp_path / "src"
    shutil.copytree(source, src, ignore=shutil.ignore_patterns(".venv", "__pycache__", ".pytest_cache"))
    dest = tmp_path / "dest"
    with pytest.raises(sync_spec.SpecMismatch, match="pinned"):
        sync_spec.sync(src, dest, sync_spec.EXPECTED_VERSION, "0" * 64)          # different manifest sha
    p = src / "watcher_rules.yaml"
    p.write_text(p.read_text() + "\n# local edit\n")
    with pytest.raises(sync_spec.SpecMismatch, match="edited"):
        sync_spec.sync(src, dest, sync_spec.EXPECTED_VERSION, sync_spec.EXPECTED_MANIFEST_SHA256)
    with pytest.raises(sync_spec.SpecMismatch, match="VERSION"):
        sync_spec.sync(src, dest, "1.1.0", sync_spec.EXPECTED_MANIFEST_SHA256)
    assert not dest.exists(), "a failed sync must not leave a partial spec/"


def test_pipeline_runs_against_the_pinned_copy(pinned, tmp_path):
    """CI mode: OPTIONS_SPEC_DIR=spec/ - the fixture run validates as 1.1.1 and the copy stays pinned."""
    import os
    env = {**os.environ, "OPTIONS_SPEC_DIR": str(pinned), "PYTHONDONTWRITEBYTECODE": "1"}
    out = tmp_path / "out"
    r = subprocess.run([str(Path(sys.executable).parent / "optpipe"), "run", "--out", str(out)], env=env,
                       capture_output=True, text=True, cwd=ROOT)
    assert r.returncode == 0, r.stdout[-2000:] + r.stderr[-2000:]
    assert "OK as 1.1.2 (schema + invariants INV-01..15)" in r.stdout
    sc = subprocess.run([str(Path(sys.executable).parent / "optpipe"), "spec-check"], env=env, capture_output=True,
                        text=True, cwd=ROOT)
    assert f"risk_rules         {pinned}/risk_rules.yaml" in sc.stdout, sc.stdout
    sync_spec.verify_pinned(pinned)


def test_default_still_reads_the_live_folder(monkeypatch):
    from optpipe.config import load_settings
    monkeypatch.delenv("OPTIONS_SPEC_DIR", raising=False)
    assert load_settings().spec_path == (ROOT.parent / "trading" / "spec").resolve()


def test_clutter_and_backups_are_never_copied_or_counted(source, tmp_path):
    """__pycache__/, *.pyc, .pytest_cache/ and *_backup/ dirs (e.g. Doodle's _v1_1_0_backup) are ignored: the copy
    follows MANIFEST.sha256 exactly, and --check does not count them as extra files."""
    src = tmp_path / "src"
    shutil.copytree(source, src)
    for junk in ("__pycache__/conftest.cpython-313.pyc", "stray.pyc", ".pytest_cache/v/cache/x",
                 "_v1_1_0_backup/session.schema.json", "_v1_1_1_backup/validate_session.py",
                 "_v1_backup/risk_rules.yaml"):
        (src / junk).parent.mkdir(parents=True, exist_ok=True)
        (src / junk).write_text("junk")
    dest = tmp_path / "dest"
    pin = sync_spec.sync(src, dest, sync_spec.EXPECTED_VERSION, sync_spec.EXPECTED_MANIFEST_SHA256)
    copied = sorted(p.relative_to(dest).as_posix() for p in dest.rglob("*") if p.is_file())
    assert copied == sorted([*pin["files"], "MANIFEST.sha256", "PIN.json", *sync_spec.V1_FILES])
    for junk in ("__pycache__/a.pyc", "b.pyc", "_v1_1_1_backup/x.py", "_v1_backup/extra.json"):
        (dest / junk).parent.mkdir(parents=True, exist_ok=True)
        (dest / junk).write_text("junk")
    sync_spec.verify_pinned(dest)                                    # clutter is not "extra"
    (dest / "notes.md").write_text("mine")
    with pytest.raises(sync_spec.SpecMismatch, match="notes.md"):    # but a real extra file still fails
        sync_spec.verify_pinned(dest)


@pytest.mark.parametrize("name", ["__pycache__/x.pyc", "x.pyc", "_v1_1_0_backup/session.schema.json"])
def test_manifest_listing_clutter_is_refused(name):
    with pytest.raises(sync_spec.SpecMismatch, match="clutter"):
        sync_spec.parse_manifest(f"{'0' * 64}  {name}\n")


def test_is_ignored_keeps_the_pinned_v1_files():
    assert not any(sync_spec.is_ignored(n) for n in sync_spec.V1_FILES)
    assert sync_spec.is_ignored("_v1_backup/risk_rules.yaml") and sync_spec.is_ignored("_v1_1_1_backup/a.py")
    assert not sync_spec.is_ignored("risk_rules.yaml") and not sync_spec.is_ignored("verticals.py")


def test_suite_is_hermetic_by_default():
    """Unless OPTIONS_SPEC_DIR was set explicitly, every test (and subprocess) reads the pinned spec/."""
    import os
    from conftest import PINNED_SPEC, USER_SPEC_DIR
    if USER_SPEC_DIR is None:
        assert Path(os.environ["OPTIONS_SPEC_DIR"]) == PINNED_SPEC
        from optpipe.config import load_settings
        assert load_settings().spec_path == PINNED_SPEC.resolve()
