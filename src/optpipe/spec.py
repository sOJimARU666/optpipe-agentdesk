"""Read-only access to Doodle's spec folder (default ../trading/spec next to this repo, env OPTIONS_SPEC_DIR).

Local/default runs read Doodle's live folder directly. CI sets OPTIONS_SPEC_DIR=spec/, the pinned copy written by
scripts/sync_spec.py (VERSION + MANIFEST sha; `sync_spec.py --check` fails on any edit). Doodle owns both.

The folder is the source of truth for the session schema (session.schema.json), the Risk Officer
rules + test cases (risk_rules.yaml: `config`, `rules`, `fixture`, `tests`), the reference
implementation (risk_reference.py) and the session validator (validate_session.py).
This module never writes to the folder and never copies it; it loads files in place (copying is sync_spec.py's job).
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from types import ModuleType
from typing import Any, Optional

import yaml

NAMES = {
    "session_schema": "session.schema.json",
    "risk_rules": "risk_rules.yaml",
    "reference_impl": "risk_reference.py",
    "session_validator": "validate_session.py",
    "reference_tests": "test_risk_reference.py",
    "session_example": "session.example.json",
    "watcher_rules": "watcher_rules.yaml",          # spec v1.1 (2026-10-29)
    "watcher_reference": "watcher_reference.py",
}


@dataclass(frozen=True)
class SpecFiles:
    root: Path

    @property
    def exists(self) -> bool:
        return self.root.is_dir()

    def path(self, key: str) -> Optional[Path]:
        p = self.root / NAMES[key]
        return p if p.is_file() else None

    def status(self) -> dict[str, str]:
        out = {"spec_dir": str(self.root) + ("" if self.exists else " (NOT FOUND - risk officer and validation unavailable)")}
        for k in NAMES:
            p = self.path(k)
            out[k] = str(p) if p else "MISSING"
        rules = self.risk_rules()
        if rules:
            out["ruleset_version"] = str(rules.get("ruleset_version"))
            out["risk_test_cases"] = f"{len(rules.get('tests') or [])} cases embedded in risk_rules.yaml"
        sch = self.path("session_schema")
        if sch:
            out["schema_sha256"] = hashlib.sha256(sch.read_bytes()).hexdigest()
        return out

    def risk_rules(self) -> Optional[dict[str, Any]]:
        p = self.path("risk_rules")
        return yaml.safe_load(p.read_text()) if p else None

    def watcher_rules(self) -> Optional[dict[str, Any]]:
        p = self.path("watcher_rules")
        return yaml.safe_load(p.read_text()) if p else None

    def session_schema(self) -> Optional[dict[str, Any]]:
        p = self.path("session_schema")
        return json.loads(p.read_text()) if p else None

    def load_module(self, key: str) -> Optional[ModuleType]:
        """Import a spec .py (validator / reference impl) in place, without copying it."""
        p = self.path(key)
        if not p:
            return None
        return _import(str(p))

    def validator_for_schema(self, schema_path: Path) -> Optional[ModuleType]:
        """Doodle's validate_session.py (same INV-01..09 invariants) bound to ANOTHER schema file,
        e.g. the v1.1 proposal in spec_patches/. Separate module instance; the spec file is only read."""
        p = self.path("session_validator")
        if not p or not Path(schema_path).exists():
            return None
        return _import(str(p), str(Path(schema_path).resolve()))


@lru_cache(maxsize=None)
def _import(path: str, schema_override: str = "") -> ModuleType:
    p = Path(path)
    name = f"_doodle_spec_{p.stem}" + (f"_{abs(hash(schema_override))}" if schema_override else "")
    if str(p.parent) not in sys.path:          # validate_session/test modules import siblings by name
        sys.path.append(str(p.parent))
    spec = importlib.util.spec_from_file_location(name, p)
    mod = importlib.util.module_from_spec(spec)
    sys.dont_write_bytecode, prev = True, sys.dont_write_bytecode   # don't drop .pyc files into Doodle's folder
    try:
        spec.loader.exec_module(mod)  # type: ignore[union-attr]
    finally:
        sys.dont_write_bytecode = prev
    if schema_override:
        import json
        mod.SCHEMA = json.loads(Path(schema_override).read_text())   # module global used by validate()
    return mod


def discover(root: Path) -> SpecFiles:
    return SpecFiles(Path(root))
