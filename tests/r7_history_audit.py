"""CI checks of the immutable R7 receipt and the narrower current-replay input pins."""
import json
import subprocess

import pytest

from test_r7_7_failure_replay import (
    BASE_HEAD, BASE_TREE, INPUT_PINS, OUTSIDE_SLICE_SHA256, RECEIPT, ROOT,
    SLICE_FILES, verify_base_identity, verify_receipt_pins,
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
    with pytest.raises(AssertionError):
        check_historical(snapshot, receipt)
