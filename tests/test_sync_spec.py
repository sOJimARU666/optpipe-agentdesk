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
def source(spec):
    src = Path(spec.root)
    if not (src / "MANIFEST.sha256").exists() or sync_spec.sha256(src / "MANIFEST.sha256") != sync_spec.EXPECTED_MANIFEST_SHA256:
        pytest.skip(f"{src} is not the pinned release")
    return src


@pytest.fixture()
def pinned(source, tmp_path):
    d = tmp_path / "spec"
    sync_spec.sync(source, d, sync_spec.EXPECTED_VERSION, sync_spec.EXPECTED_MANIFEST_SHA256)
    return d


def test_pin_constants():
    assert sync_spec.EXPECTED_VERSION == "1.1.1"
    assert sync_spec.EXPECTED_MANIFEST_SHA256 == "477401b68298f5b4513aa9b46eed9a56df5d1097e85175bdcf8a166cd3d4f489"


def test_sync_writes_manifest_files_and_pin_record(pinned, source):
    files = sync_spec.parse_manifest((source / "MANIFEST.sha256").read_text())
    assert len(files) == 20
    pin = json.loads((pinned / "PIN.json").read_text())
    assert pin["version"] == "1.1.1" and pin["manifest_sha256"] == sync_spec.EXPECTED_MANIFEST_SHA256
    assert pin["owner"] == "Doodle" and pin["files"] == dict(sorted(files.items()))
    assert pin["synced_at"].endswith(("+01:00", "+02:00"))               # Berlin time
    for name, digest in files.items():
        assert sync_spec.sha256(pinned / name) == digest
    assert set(pin["v1_backup_files"]) == set(sync_spec.V1_FILES)
    assert not (pinned / ".venv").exists() and not (pinned / "_v1_backup" / "risk_rules.yaml").exists()
    assert sync_spec.verify_pinned(pinned)["version"] == "1.1.1"


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
    assert "OK as 1.1.1 (schema + invariants INV-01..14)" in r.stdout
    sc = subprocess.run([str(Path(sys.executable).parent / "optpipe"), "spec-check"], env=env, capture_output=True,
                        text=True, cwd=ROOT)
    assert f"risk_rules         {pinned}/risk_rules.yaml" in sc.stdout, sc.stdout
    sync_spec.verify_pinned(pinned)


def test_default_still_reads_the_live_folder(monkeypatch):
    from optpipe.config import load_settings
    monkeypatch.delenv("OPTIONS_SPEC_DIR", raising=False)
    assert load_settings().spec_path == (ROOT.parent / "trading" / "spec").resolve()
