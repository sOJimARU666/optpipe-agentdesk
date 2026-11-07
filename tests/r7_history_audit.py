"""CI checks of the immutable R7 receipt and the narrower current-replay input pins."""
import json
import subprocess

import pytest

from test_r7_7_failure_replay import (
    BASE_HEAD, BASE_TREE, INPUT_PINS, OUTSIDE_SLICE_SHA256, RECEIPT, ROOT,
    SLICE_FILES, DRIVER, read_replay_receipt, verify_base_identity, verify_receipt_pins,
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
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=snapshot, text=True).strip()
    assert head == RECEIPT_COMMIT, "historical snapshot commit mismatch"
    read_replay_receipt(snapshot / RECEIPT.relative_to(ROOT), snapshot / DRIVER.relative_to(ROOT))
    historical = (snapshot / RECEIPT.relative_to(ROOT)).read_bytes()
    assert current_receipt == historical, "historical receipt changed"
    verify_base_identity(snapshot, json.loads(historical), base_head=BASE_HEAD,
                         base_tree=BASE_TREE, slice_files=SLICE_FILES,
                         input_pins=INPUT_PINS, outside_sha256=OUTSIDE_SLICE_SHA256)


def test_historical_receipt(snapshot):
    check_historical(snapshot, RECEIPT.read_bytes())


def test_changed_historical_receipt_is_rejected(snapshot):
    with pytest.raises(AssertionError, match="historical receipt changed"):
        check_historical(snapshot, RECEIPT.read_bytes() + b"\n")


def test_unrelated_tracked_file_only_invalidates_historical_identity(snapshot):
    note = snapshot / "unrelated-audit-note.md"
    note.write_text("Audit control.\n")
    subprocess.run(["git", "add", "--", note.name], cwd=snapshot, check=True)
    receipt = RECEIPT.read_bytes()
    verify_receipt_pins(snapshot, json.loads(receipt), BASE_HEAD, BASE_TREE,
                        INPUT_PINS, OUTSIDE_SLICE_SHA256)
    with pytest.raises(AssertionError, match="outside-slice manifest mismatch"):
        check_historical(snapshot, receipt)

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
