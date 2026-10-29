"""Read-only access to Doodle's spec folder (default /workspace/trading/spec, env OPTIONS_SPEC_DIR).

The folder is the source of truth for the session schema (session.schema.json), the Risk Officer
rules + test cases (risk_rules.yaml: `config`, `rules`, `fixture`, `tests`), the reference
implementation (risk_reference.py) and the session validator (validate_session.py).
This module never writes to the folder and never copies it; it loads files in place.
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

    def session_schema(self) -> Optional[dict[str, Any]]:
        p = self.path("session_schema")
        return json.loads(p.read_text()) if p else None

    def load_module(self, key: str) -> Optional[ModuleType]:
        """Import a spec .py (validator / reference impl) in place, without copying it."""
        p = self.path(key)
        if not p:
            return None
        return _import(str(p))


@lru_cache(maxsize=None)
def _import(path: str) -> ModuleType:
    p = Path(path)
    name = f"_doodle_spec_{p.stem}"
    if str(p.parent) not in sys.path:          # validate_session/test modules import siblings by name
        sys.path.append(str(p.parent))
    spec = importlib.util.spec_from_file_location(name, p)
    mod = importlib.util.module_from_spec(spec)
    sys.dont_write_bytecode, prev = True, sys.dont_write_bytecode   # don't drop .pyc files into Doodle's folder
    try:
        spec.loader.exec_module(mod)  # type: ignore[union-attr]
    finally:
        sys.dont_write_bytecode = prev
    return mod


def discover(root: Path) -> SpecFiles:
    return SpecFiles(Path(root))
