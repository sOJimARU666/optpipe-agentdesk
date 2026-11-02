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
    assert sync_spec.EXPECTED_VERSION == "1.1.4"
    assert sync_spec.EXPECTED_MANIFEST_SHA256 == "f58db767b1c631d9d7c08d15df1e7bbedaa2215c5b2efa3b1a5f4b596a2d2610"
    assert sync_spec.FROZEN_RELEASES == {          # the earlier pins of this file, in order (git history)
        "_v1_1_0_backup": ("1.1.0", "3b25cfe562bbd818589e82e069235b188c4f2b4aac434c04fce54d238fd95e07"),
        "_v1_1_1_backup": ("1.1.1", "477401b68298f5b4513aa9b46eed9a56df5d1097e85175bdcf8a166cd3d4f489"),
        "_v1_1_2_backup": ("1.1.2", "9fcc55b0b84b6a8be43d9e5fefda22c7f05102aef9991089b851d6946cde0fdd"),
        "_v1_1_3_backup": ("1.1.3", "8785ee8b2f9f3bbc993bfbad0ff3ae8e7535cf15b42c507e32d145a25d6836c0"),
    }


def test_sync_writes_manifest_files_and_pin_record(pinned, source):
    files = sync_spec.parse_manifest((source / "MANIFEST.sha256").read_text())
    assert len(files) == 29 and {"test_invariants_v1_1_3.py", "session_fixtures.py", "test_release_v1_1_4.py"} <= set(files)
    vendored = [n for n in files if n.startswith("fixtures/")]          # spec 1.1.4: hermetic fixtures in a subdirectory
    assert vendored and all((pinned / n).is_file() for n in vendored)
    pin = json.loads((pinned / "PIN.json").read_text())
    assert pin["version"] == "1.1.4" and pin["manifest_sha256"] == sync_spec.EXPECTED_MANIFEST_SHA256
    assert pin["owner"] == "Doodle" and pin["files"] == dict(sorted(files.items()))
    assert pin["synced_at"].endswith(("+01:00", "+02:00"))               # Berlin time
    for name, digest in files.items():
        assert sync_spec.sha256(pinned / name) == digest
    assert set(pin["v1_backup_files"]) == set(sync_spec.V1_FILES)
    assert not (pinned / ".venv").exists() and not (pinned / "_v1_backup" / "risk_rules.yaml").exists()
    assert {k: v["version"] for k, v in pin["frozen_releases"].items()} == {
        "_v1_1_0_backup": "1.1.0", "_v1_1_1_backup": "1.1.1", "_v1_1_2_backup": "1.1.2", "_v1_1_3_backup": "1.1.3"}
    for sub in sync_spec.FROZEN_RELEASES:                  # copied by their own manifest, byte-identical
        for name, digest in sync_spec.parse_manifest((source / sub / "MANIFEST.sha256").read_text()).items():
            assert sync_spec.sha256(pinned / sub / name) == digest
    assert sync_spec.verify_pinned(pinned)["version"] == "1.1.4"


@pytest.mark.parametrize("tamper", ["edit_file", "edit_manifest", "extra_file", "missing_file", "edit_v1", "edit_pin",
                                    "edit_frozen", "extra_in_frozen", "missing_frozen", "edit_pin_frozen",
                                    "unknown_backup_dir", "unknown_backup_dir_empty", "nested_backup_dir"])
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
    elif tamper == "edit_frozen":
        p = pinned / "_v1_1_2_backup" / "validate_session.py"
        p.write_text(p.read_text() + "\n# local edit\n")
    elif tamper == "extra_in_frozen":
        (pinned / "_v1_1_2_backup" / "local_notes.md").write_text("mine")
    elif tamper == "missing_frozen":
        shutil.rmtree(pinned / "_v1_1_1_backup")
    elif tamper == "unknown_backup_dir":                 # NEGATIVE (PR #6 nit): passed --check on 49debf0
        shutil.copytree(pinned / "_v1_1_3_backup", pinned / "_v1_1_9_backup")
    elif tamper == "unknown_backup_dir_empty":
        (pinned / "_v1_0_9_backup").mkdir()
    elif tamper == "nested_backup_dir":
        (pinned / "fixtures" / "old_backup").mkdir(parents=True)
        (pinned / "fixtures" / "old_backup" / "x.json").write_text("{}")
    elif tamper == "edit_pin_frozen":
        pin = json.loads((pinned / "PIN.json").read_text())
        del pin["frozen_releases"]["_v1_1_0_backup"]
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
    """CI mode: OPTIONS_SPEC_DIR=spec/ - the fixture run validates as 1.1.4 and the copy stays pinned."""
    import os
    env = {**os.environ, "OPTIONS_SPEC_DIR": str(pinned), "PYTHONDONTWRITEBYTECODE": "1"}
    out = tmp_path / "out"
    r = subprocess.run([str(Path(sys.executable).parent / "optpipe"), "run", "--out", str(out)], env=env,
                       capture_output=True, text=True, cwd=ROOT)
    assert r.returncode == 0, r.stdout[-2000:] + r.stderr[-2000:]
    assert "OK as 1.1.4 (schema + invariants INV-01..16)" in r.stdout
    sc = subprocess.run([str(Path(sys.executable).parent / "optpipe"), "spec-check"], env=env, capture_output=True,
                        text=True, cwd=ROOT)
    assert f"risk_rules         {pinned}/risk_rules.yaml" in sc.stdout, sc.stdout
    sync_spec.verify_pinned(pinned)


def test_default_still_reads_the_live_folder(monkeypatch):
    from optpipe.config import load_settings
    monkeypatch.delenv("OPTIONS_SPEC_DIR", raising=False)
    assert load_settings().spec_path == (ROOT.parent / "trading" / "spec").resolve()


def test_clutter_and_backups_are_never_copied_or_counted(source, tmp_path):
    """__pycache__/, *.pyc, .pytest_cache/ and unpinned *_backup/ dirs are ignored: the copy follows MANIFEST.sha256
    (and each frozen release's own MANIFEST.sha256) exactly, and --check does not count clutter as extra files."""
    src = tmp_path / "src"
    shutil.copytree(source, src)
    for junk in ("__pycache__/conftest.cpython-313.pyc", "stray.pyc", ".pytest_cache/v/cache/x",
                 "_v1_0_9_backup/session.schema.json", "_v1_1_1_backup/unlisted_notes.md",
                 "_v1_1_0_backup/__pycache__/x.pyc", "_v1_backup/risk_rules.yaml"):
        (src / junk).parent.mkdir(parents=True, exist_ok=True)
        (src / junk).write_text("junk")
    dest = tmp_path / "dest"
    pin = sync_spec.sync(src, dest, sync_spec.EXPECTED_VERSION, sync_spec.EXPECTED_MANIFEST_SHA256)
    copied = sorted(p.relative_to(dest).as_posix() for p in dest.rglob("*") if p.is_file())
    frozen = [f"{sub}/{n}" for sub in sync_spec.FROZEN_RELEASES
              for n in [*sync_spec.parse_manifest((src / sub / "MANIFEST.sha256").read_text()), "MANIFEST.sha256"]]
    assert copied == sorted([*pin["files"], "MANIFEST.sha256", "PIN.json", *sync_spec.V1_FILES, *frozen])
    for junk in ("__pycache__/a.pyc", "b.pyc", "_v1_1_1_backup/__pycache__/x.pyc", "_v1_backup/extra.json"):
        (dest / junk).parent.mkdir(parents=True, exist_ok=True)
        (dest / junk).write_text("junk")
    sync_spec.verify_pinned(dest)                                    # clutter is not "extra"
    (dest / "_v1_0_9_backup").mkdir()                                # but an unpinned backup folder is (PR #6 nit)
    with pytest.raises(sync_spec.SpecMismatch, match="_v1_0_9_backup"):
        sync_spec.verify_pinned(dest)
    (dest / "_v1_0_9_backup").rmdir()
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
