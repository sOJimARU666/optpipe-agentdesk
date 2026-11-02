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
ROOT = PKG.parents[1]                       # repo root (src/optpipe -> src -> repo)
SRC_SOURCES = sorted(p for p in PKG.rglob("*.py") if "__pycache__" not in p.parts)
SCRIPT_SOURCES = sorted((ROOT / "scripts").glob("*.py"))
SOURCES = SRC_SOURCES + SCRIPT_SOURCES       # everything that can run: the package AND scripts/

BROKER_MODULES = {"ib_insync", "ib_async", "ibapi", "alpaca", "alpaca_trade_api", "tda", "schwab", "tastytrade",
                  "robin_stocks", "webull", "tradier", "etrade", "ccxt", "degiro", "degiroapi", "trading212",
                  "saxo_openapi", "oandapyV20", "fix", "quickfix", "kiteconnect", "questrade"}
ORDER_NAME = re.compile(r"(place|submit|send|route|execute|cancel|modify|transmit)_?(live_)?order|^order_", re.I)
WRITE_HTTP = {"post", "put", "patch", "delete"}

# --- network scan (FIX-SEC-001 item 6) ----------------------------------------------------------------
# Network modules may be imported ONLY in providers/fetchers.py, and only requests.get may be used there.
NET_MODULES = {"requests", "httpx", "urllib", "urllib3", "http", "aiohttp", "websocket", "websockets", "socket",
               "ssl", "ftplib", "smtplib", "telnetlib", "xmlrpc", "asyncio", "pycurl", "grpc", "paramiko"}
NET_ALLOWED = {"src/optpipe/providers/fetchers.py": {"requests": {"get"}}}
# Any call with one of these names is a potential write / raw connection, whatever the receiver or alias.
WRITE_CALLS = WRITE_HTTP | {"request", "send", "sendall", "sendto", "stream", "urlopen", "open_connection",
                            "create_connection", "connect", "websocket", "websocket_route", "api_route",
                            "add_api_websocket_route"}
ROUTE_CALLS = {"add_api_route", "add_route", "route"}
HTTP_VERBS = {"GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "CONNECT", "TRACE"}


def _rel(p: Path) -> str:
    return p.resolve().relative_to(ROOT.resolve()).as_posix()


def _callee(f) -> str | None:
    return f.attr if isinstance(f, ast.Attribute) else f.id if isinstance(f, ast.Name) else None


def _const_strs(node) -> list[str] | None:
    """['GET'] for "GET" / ["GET"] / ("GET",); None when not a literal."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return [node.value]
    if isinstance(node, (ast.List, ast.Tuple, ast.Set)) and all(
            isinstance(e, ast.Constant) and isinstance(e.value, str) for e in node.elts):
        return [e.value for e in node.elts]
    return None


def scan_network(text: str, rel: str) -> list[str]:
    """Paper-only violations in one source file (rel = repo-relative posix path)."""
    tree = ast.parse(text, filename=rel)
    allowed = NET_ALLOWED.get(rel, {})
    out: list[str] = []
    aliases: dict[str, str] = {}                       # local name -> top-level network module
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            for a in n.names:
                top = a.name.split(".")[0]
                if top in NET_MODULES:
                    aliases[(a.asname or a.name).split(".")[0]] = top
                    if top not in allowed:
                        out.append(f"{rel}:{n.lineno} imports network module {a.name}")
        elif isinstance(n, ast.ImportFrom):
            top = (n.module or "").split(".")[0]
            if top in NET_MODULES:
                ok = allowed.get(top, set())
                for a in n.names:
                    if top not in allowed or a.name not in ok:
                        out.append(f"{rel}:{n.lineno} imports {a.name} from network module {n.module}")
    for n in ast.walk(tree):
        if isinstance(n, ast.Attribute) and isinstance(n.value, ast.Name) and n.value.id in aliases:
            top = aliases[n.value.id]
            if n.attr not in allowed.get(top, set()):
                out.append(f"{rel}:{n.lineno} uses {top}.{n.attr} (only {sorted(allowed.get(top, set()))} allowed)")
        if not isinstance(n, ast.Call):
            continue
        name = _callee(n.func)
        if name in WRITE_CALLS:
            out.append(f"{rel}:{n.lineno} write/connection call {name}()")
        if name in {"__import__", "import_module"} and n.args:
            mod = _const_strs(n.args[0])
            if mod is None or mod[0].split(".")[0] in NET_MODULES:
                out.append(f"{rel}:{n.lineno} dynamic import {ast.unparse(n.args[0])}")
        if name == "getattr" and len(n.args) >= 2:
            attr = _const_strs(n.args[1])
            if attr is None or attr[0] in WRITE_CALLS:
                out.append(f"{rel}:{n.lineno} getattr(..., {ast.unparse(n.args[1])})")
        for kw in n.keywords:
            if kw.arg in {"method", "methods"}:
                vals = _const_strs(kw.value)
                bad = [v for v in (vals or []) if v.upper() in HTTP_VERBS - {"GET"}]
                if bad:
                    out.append(f"{rel}:{n.lineno} {kw.arg}={bad}")
        if name in ROUTE_CALLS:
            kws = {kw.arg: kw.value for kw in n.keywords}
            methods = _const_strs(kws["methods"]) if "methods" in kws else None
            if methods is None or [m.upper() for m in methods] != ["GET"]:
                out.append(f"{rel}:{n.lineno} {name}() without methods=[\"GET\"]")
    return out


def _trees():
    for p in SOURCES:
        yield p, ast.parse(p.read_text(), filename=_rel(p))


def test_sources_found():
    assert len(SRC_SOURCES) >= 50, len(SRC_SOURCES)       # 55 at FIX-SEC-001
    assert len(SCRIPT_SOURCES) >= 5, len(SCRIPT_SOURCES)  # export_dashboard, gen_models, make_spec_patch, ...
    assert all(_rel(p) in NET_ALLOWED or _rel(p).startswith(("src/", "scripts/")) for p in SOURCES)


def test_scripts_dir_scanned():
    rels = {_rel(p) for p in SOURCES}
    assert {"scripts/sync_spec.py", "scripts/gen_models.py", "scripts/make_watch_tape.py"} <= rels
    assert "src/optpipe/providers/fetchers.py" in rels


def test_no_broker_sdk_imports():
    for p, tree in _trees():
        for n in ast.walk(tree):
            names = [a.name for a in n.names] if isinstance(n, ast.Import) else \
                    [n.module or ""] if isinstance(n, ast.ImportFrom) else []
            for name in names:
                assert name.split(".")[0] not in BROKER_MODULES, f"{_rel(p)}: imports broker SDK {name}"


def test_no_order_placing_functions_or_classes():
    for p, tree in _trees():
        for n in ast.walk(tree):
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                assert not ORDER_NAME.search(n.name), f"{_rel(p)}:{n.lineno} defines {n.name}"
            if isinstance(n, ast.Attribute):
                assert not ORDER_NAME.search(n.attr), f"{_rel(p)}:{n.lineno} calls .{n.attr}"


def test_http_is_read_only_get():
    """The only network code is HttpFetcher (providers/fetchers.py), and it only calls requests.get."""
    problems = [v for p in SOURCES for v in scan_network(p.read_text(), _rel(p))]
    assert problems == []


def test_fetchers_uses_requests_get():
    """Control for the scanner: the allowed file really is scanned and really does use requests.get."""
    text = (PKG / "providers" / "fetchers.py").read_text()
    assert "requests.get(" in text
    assert scan_network(text, "src/optpipe/providers/fetchers.py") == []
    assert scan_network(text, "src/optpipe/providers/nasdaq.py")      # the same code anywhere else is flagged


# Each snippet is something the old scanner (receiver-name allowlist, 6 modules, src/ only) let through.
SCANNER_CASES = {
    "requests_request_post": ("src/optpipe/x.py", "import requests\nrequests.request('POST', u, data=d)\n"),
    "requests_alias_post": ("src/optpipe/x.py", "import requests as rq\nrq.post(u)\n"),
    "from_import_post": ("src/optpipe/providers/fetchers.py", "from requests import post\npost(u)\n"),
    "fetchers_requests_put": ("src/optpipe/providers/fetchers.py", "import requests\nrequests.put(u)\n"),
    "session_post": ("src/optpipe/providers/fetchers.py",
                     "import requests\nclient = requests.Session()\nclient.post(u, json=o)\n"),
    "httpx_client_post": ("src/optpipe/x.py", "import httpx\nwith httpx.Client() as c:\n    c.post(u)\n"),
    "urllib_post": ("src/optpipe/x.py", "import urllib.request as ur\n"
                    "ur.urlopen(ur.Request(u, data=b'x', method='POST'))\n"),
    "http_client": ("src/optpipe/x.py", "import http.client\nc = http.client.HTTPSConnection(h)\n"
                    "c.request('POST', '/orders')\n"),
    "raw_socket": ("src/optpipe/x.py", "import socket\ns = socket.socket()\ns.connect((h, 443))\ns.sendall(b)\n"),
    "socket_from_import": ("src/optpipe/x.py", "from socket import create_connection as cc\ncc((h, 80))\n"),
    "asyncio_conn": ("src/optpipe/x.py", "import asyncio\nasync def f():\n"
                     "    r, w = await asyncio.open_connection(h, 443)\n"),
    "dynamic_import": ("src/optpipe/x.py", "m = __import__('socket')\n"),
    "getattr_post": ("src/optpipe/x.py", "f = getattr(client, 'post')\n"),
    "method_kw_post": ("src/optpipe/x.py", "client.fetch(u, method='POST')\n"),
    "add_api_route_post": ("src/optpipe/dashboard/app.py", "app.add_api_route('/x', h, methods=['POST'])\n"),
    "add_api_route_no_methods": ("src/optpipe/dashboard/app.py", "app.add_api_route('/x', h)\n"),
    "router_route": ("src/optpipe/dashboard/app.py", "@router.route('/x', methods=['GET', 'POST'])\ndef h(): ...\n"),
    "decorator_on_any_receiver": ("src/optpipe/dashboard/app.py", "@api.post('/x')\ndef h(): ...\n"),
    "websocket_route": ("src/optpipe/dashboard/app.py", "@app.websocket('/ws')\nasync def h(ws): ...\n"),
    "script_dir": ("scripts/push.py", "import requests\nrequests.get(u)\n"),
}


@pytest.mark.parametrize("case", list(SCANNER_CASES))
def test_scanner_catches(case):
    rel, text = SCANNER_CASES[case]
    assert scan_network(text, rel), f"{case} not caught"


@pytest.mark.parametrize("rel,text", [
    ("src/optpipe/providers/fetchers.py", "import requests\nr = requests.get(u, timeout=5)\n"),
    ("src/optpipe/dashboard/app.py", "@app.get('/x')\ndef h(): ...\napp.add_api_route('/y', h, methods=['GET'])\n"),
    ("src/optpipe/records/fields.py", "F(1, method='derived')\n"),
])
def test_scanner_allows_get_only_code(rel, text):
    assert scan_network(text, rel) == []


def test_no_0_0_0_0_literal_in_sources():
    """No all-interfaces bind literal anywhere in runnable code."""
    for p in SOURCES:
        assert "0.0.0.0" not in p.read_text(), _rel(p)


def test_dashboard_is_get_only():
    """No write route decorators (@app.post/put/patch/delete/api_route/websocket) and no forms or hx-post in templates."""
    bad_dec = WRITE_HTTP | {"api_route", "websocket", "add_api_route", "add_api_websocket_route"}
    for p, tree in _trees():
        for n in ast.walk(tree):
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
                for d in n.decorator_list:
                    f = d.func if isinstance(d, ast.Call) else d
                    assert not (isinstance(f, ast.Attribute) and f.attr in bad_dec), f"{p}:{n.lineno} @{f.attr}"
    for t in (PKG / "dashboard" / "templates").glob("*.html"):
        html = t.read_text().lower()
        for needle in ("<form", "hx-post", "hx-put", "hx-patch", "hx-delete", 'method="post"'):
            assert needle not in html, f"{t.name} contains {needle}"


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
