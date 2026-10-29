"""PAPER ONLY is structural. These tests walk the source tree and fail if anything that could place,
route or modify a live order appears. There is no broker integration and none may be added."""
import ast
import re
from pathlib import Path

import pytest
from pydantic import ValidationError

import optpipe
from optpipe.config import Settings, load_settings
from optpipe.models.contracts import HYPOTHETICAL_LABEL

PKG = Path(optpipe.__file__).parent
SOURCES = sorted(p for p in PKG.rglob("*.py") if "__pycache__" not in p.parts)

BROKER_MODULES = {"ib_insync", "ib_async", "ibapi", "alpaca", "alpaca_trade_api", "tda", "schwab", "tastytrade",
                  "robin_stocks", "webull", "tradier", "etrade", "ccxt", "degiro", "degiroapi", "trading212",
                  "saxo_openapi", "oandapyV20", "fix", "quickfix", "kiteconnect", "questrade"}
ORDER_NAME = re.compile(r"(place|submit|send|route|execute|cancel|modify|transmit)_?(live_)?order|^order_", re.I)
WRITE_HTTP = {"post", "put", "patch", "delete"}


def _trees():
    for p in SOURCES:
        yield p, ast.parse(p.read_text(), filename=str(p))


def test_sources_found():
    assert len(SOURCES) > 20


def test_no_broker_sdk_imports():
    for p, tree in _trees():
        for n in ast.walk(tree):
            names = [a.name for a in n.names] if isinstance(n, ast.Import) else \
                    [n.module or ""] if isinstance(n, ast.ImportFrom) else []
            for name in names:
                assert name.split(".")[0] not in BROKER_MODULES, f"{p}: imports broker SDK {name}"


def test_no_order_placing_functions_or_classes():
    for p, tree in _trees():
        for n in ast.walk(tree):
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                assert not ORDER_NAME.search(n.name), f"{p}:{n.lineno} defines {n.name}"
            if isinstance(n, ast.Attribute):
                assert not ORDER_NAME.search(n.attr), f"{p}:{n.lineno} calls .{n.attr}"


def test_http_is_read_only_get():
    """The only network client is HttpFetcher, and it only issues GETs."""
    for p, tree in _trees():
        for n in ast.walk(tree):
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr in WRITE_HTTP \
                    and isinstance(n.func.value, ast.Name) and n.func.value.id in {"requests", "httpx", "session", "s"}:
                pytest.fail(f"{p}:{n.lineno} write HTTP call {n.func.value.id}.{n.func.attr}")
        for n in ast.walk(tree):
            if isinstance(n, (ast.Import, ast.ImportFrom)):
                mods = [a.name for a in n.names] if isinstance(n, ast.Import) else [n.module or ""]
                if any(m.split(".")[0] in {"requests", "httpx", "urllib3", "aiohttp", "websocket", "websockets"}
                       for m in mods):
                    assert p.name == "fetchers.py", f"{p} imports a network client"


def test_no_broker_or_execution_packages():
    for d in PKG.rglob("*"):
        if d.is_dir():
            assert not re.search(r"broker|execution|exec|orders?$|trading", d.name, re.I), d


def test_settings_mode_only_paper():
    assert load_settings().mode == "paper_only"
    raw = load_settings().model_dump()
    for bad in ("live", "real", "paper", ""):
        with pytest.raises(ValidationError):
            Settings.model_validate(raw | {"mode": bad})
    with pytest.raises(ValidationError):                       # no hidden broker credentials/endpoints
        Settings.model_validate(raw | {"broker": {"name": "x", "api_key": "k"}})


def test_orchestrator_mode_constant():
    from optpipe import orchestrator
    assert orchestrator.MODE == "paper"


def test_tickets_are_labelled_hypothetical(rec):
    assert "HYPOTHETICAL" in HYPOTHETICAL_LABEL.upper()
    assert rec["session"]["mode"] == "paper"
    assert all(t["label"] == HYPOTHETICAL_LABEL for t in rec["tickets"])


def test_watcher_never_force_closes(rec, settings, spec):
    """Watcher rules (for a ticket that would PASS) are inactive alerts/suggestions only."""
    from optpipe.watcher import WatcherAgent
    import copy
    r = copy.deepcopy(rec)
    for rv in r["risk_reviews"]:
        rv["overall"]["verdict"] = "PASS"
    from optpipe.models.contracts import WatcherInput
    rules = WatcherAgent().run(WatcherInput(tickets=r["tickets"], risk_reviews=r["risk_reviews"]))
    rules = rules.model_dump(mode="json")["watcher_rules"]
    assert len(rules) >= len(r["tickets"])
    for w in rules:
        assert w["active"] is False and w["schedule"] is None
        assert w["action"] in {"alert", "suggest_close"}


def test_cli_has_no_order_command():
    from optpipe import cli
    src = Path(cli.__file__).read_text()
    assert not re.search(r"add_parser\(\s*['\"](order|trade|buy|sell|execute|submit)", src)
