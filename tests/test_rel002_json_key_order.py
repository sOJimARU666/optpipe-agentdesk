"""REL-002: offline metamorphic test for JSON object-key order invariance.

Proposed test-only change. No production dependency, network or broker access.
Run from repository root: pytest -q tests/test_rel002_json_key_order.py
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from mdpack import copy_pack, load, rehash


def _reverse_object_keys(value):
    """Recursively reverse object insertion order, preserving array order and values."""
    if isinstance(value, dict):
        return {key: _reverse_object_keys(item) for key, item in reversed(list(value.items()))}
    if isinstance(value, list):
        return [_reverse_object_keys(item) for item in value]
    return value


@pytest.mark.parametrize("filename", ["index.json", "JPM.json", "WDC.json", "XMPLA.json", "XMPLB.json", "catalysts.json"])
def test_valid_pack_json_object_key_order_does_not_change_semantics(tmp_path: Path, filename: str):
    """An honestly rehashed, semantically identical JSON file must stay valid."""
    pack = copy_pack(tmp_path)
    original = load(pack)
    assert original.ok, original.errors

    path = pack / filename
    document = json.loads(path.read_text(encoding="utf-8"))
    path.write_text(json.dumps(_reverse_object_keys(document), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    rehash(pack)  # Prevent manifest integrity errors from masking parser behavior.

    result = load(pack)
    assert result.ok, (filename, result.errors)
    assert result.errors == original.errors
    assert result.index == original.index
    assert result.snapshots == original.snapshots
    assert result.catalysts == original.catalysts
    assert result.summary_md == original.summary_md
    assert result.exit_code() == original.exit_code()
