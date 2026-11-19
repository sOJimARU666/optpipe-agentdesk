"""CI checks of the immutable R7 receipt and the narrower current-replay input pins."""
import hashlib
import json
import subprocess

import pytest

from test_r7_7_failure_replay import (
    BASE_HEAD, BASE_TREE, CURRENT_RECEIPT_SHA256, HISTORICAL_RECEIPT_SHA256, INPUT_PINS,
    OUTSIDE_SLICE_SHA256, RECEIPT, ROOT, SLICE_FILES, DRIVER, read_replay_receipt,
    verify_base_identity, verify_receipt_pins,
)

RECEIPT_COMMIT = "8344884835d5d4d54499b8e7255313f968cf8b9d"


@pytest.fixture
def snapshot(tmp_path):
    path = tmp_path / "r7-snapshot"
    subprocess.run(["git", "worktree", "add", "--detach", str(path), RECEIPT_COMMIT],
                   cwd=ROOT, check=True)
    try:
        yield path
    finally:
        # This worktree was created by this fixture. One control stages a new file.
        subprocess.run(["git", "worktree", "remove", "--force", str(path)], cwd=ROOT, check=True)


def check_historical(snapshot, current_receipt):
    """The receipt object at RECEIPT_COMMIT stays the original bytes. The working receipt is the
    slice-1 re-pin of watcher_rules.yaml (case hashes unchanged). The snapshot tree is checked
    against the pins stored in that historical receipt, not against the working pin."""
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=snapshot, text=True).strip()
    assert head == RECEIPT_COMMIT, "historical snapshot commit mismatch"
    historical = (snapshot / RECEIPT.relative_to(ROOT)).read_bytes()
    read_replay_receipt(snapshot / RECEIPT.relative_to(ROOT), snapshot / DRIVER.relative_to(ROOT),
                        HISTORICAL_RECEIPT_SHA256)
    assert hashlib.sha256(current_receipt).hexdigest() == CURRENT_RECEIPT_SHA256, "receipt bytes changed"
    hist = json.loads(historical)
    paths = {field: rel for field, rel, _digest in INPUT_PINS}
    historical_pins = tuple((field, paths[field], hist["inputs"][field]) for field, _rel, _digest in INPUT_PINS)
    verify_base_identity(snapshot, hist, base_head=BASE_HEAD, base_tree=BASE_TREE,
                         slice_files=SLICE_FILES, input_pins=historical_pins,
                         outside_sha256=OUTSIDE_SLICE_SHA256)


def test_historical_receipt(snapshot):
    check_historical(snapshot, RECEIPT.read_bytes())


def test_changed_historical_receipt_is_rejected(snapshot):
    with pytest.raises(AssertionError, match="receipt bytes changed"):
        check_historical(snapshot, RECEIPT.read_bytes() + b"\n")


def test_unrelated_tracked_file_only_invalidates_historical_identity(snapshot):
    note = snapshot / "unrelated-audit-note.md"
    note.write_text("Audit control.\n")
    subprocess.run(["git", "add", "--", note.name], cwd=snapshot, check=True)
    historical = (snapshot / RECEIPT.relative_to(ROOT)).read_bytes()
    hist = json.loads(historical)
    paths = {field: rel for field, rel, _digest in INPUT_PINS}
    historical_pins = tuple((field, paths[field], hist["inputs"][field]) for field, _rel, _digest in INPUT_PINS)
    verify_receipt_pins(snapshot, hist, BASE_HEAD, BASE_TREE, historical_pins, OUTSIDE_SLICE_SHA256)
    with pytest.raises(AssertionError, match="outside-slice manifest mismatch"):
        check_historical(snapshot, RECEIPT.read_bytes())

def test_real_receipt_snapshot_requires_recorded_base(snapshot, tmp_path):
    clone = tmp_path / "shallow"
    subprocess.run(["git", "clone", "-q", "--depth=1", snapshot.as_uri(), str(clone)], check=True)
    assert subprocess.run(["git", "cat-file", "-e", BASE_HEAD], cwd=clone).returncode != 0
    with pytest.raises(AssertionError, match=f"historical base unavailable: {BASE_HEAD}"):
        check_historical(clone, RECEIPT.read_bytes())


@pytest.mark.parametrize(("target", "reason"), [
    (INPUT_PINS[0][1], INPUT_PINS[0][1]),
    ("src/optpipe/marketdata.py", "outside-slice manifest mismatch"),
    (DRIVER.relative_to(ROOT), "driver bytes changed"),
    (RECEIPT.relative_to(ROOT), "receipt bytes changed"),
])
def test_historical_mutation_rejected(snapshot, target, reason):
    with (snapshot / target).open("ab") as stream:
        stream.write(b"\n")
    subprocess.run(["git", "add", "--", str(target)], cwd=snapshot, check=True)
    with pytest.raises(AssertionError, match=reason):
        check_historical(snapshot, RECEIPT.read_bytes())
