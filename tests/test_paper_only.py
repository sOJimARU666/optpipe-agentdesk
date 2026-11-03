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
# Wildcard (all-interfaces) bind hosts: never in runnable code. The dashboard accepts only the literal
# loopback addresses in cli.LOOPBACK (127.0.0.1, ::1).
WILDCARD_HOSTS = {"0.0.0.0", "::", "[::]", "", "*", "0", "::0", "0:0:0:0:0:0:0:0", "in6addr_any"}
HOST_KWARGS = {"host", "hostname", "bind", "bind_host", "listen", "address", "addr", "server_name"}
HOST_NAME = re.compile(r"(^|_)(host|bind|listen|addr|address)(_|$)", re.I)
# Process-spawning calls (subprocess.*, os.system/popen/exec*/spawn*, asyncio subprocess), any alias.
PROC_CALLS = {"run", "call", "check_call", "check_output", "Popen", "getoutput", "getstatusoutput", "system", "popen",
              "create_subprocess_exec", "create_subprocess_shell", "startfile",
              "execl", "execle", "execlp", "execlpe", "execv", "execve", "execvp", "execvpe",
              "spawnl", "spawnle", "spawnlp", "spawnlpe", "spawnv", "spawnve", "spawnvp", "spawnvpe", "posix_spawn",
              "posix_spawnp"}
# SOL-ACCEPT-001 G10: indirection that hides a network module or a write method from the checks above.
DYNAMIC_IMPORTERS = {"__import__", "import_module"}                # also tracked through aliases (import_module as im)
CODE_EXEC = {"exec", "eval", "compile"}                            # bare builtins (re.compile is an attribute: allowed)
INTROSPECTION = {"__dict__", "__getattribute__", "__builtins__"}
METHOD_OVERRIDE = re.compile(r"x-(http-)?method(-override)?$", re.I)  # X-HTTP-Method-Override, X-HTTP-Method, X-Method-Override
NET_TOOLS = r"(curl|wget|wget2|aria2c|httpie|nc|ncat|netcat|socat|telnet|ssh|scp|sftp|ftp|rsync)"
NET_TOOL_ARGV = re.compile(rf"^(\S*[/\\])?{NET_TOOLS}(\.exe)?$", re.I)                       # one argv element
NET_TOOL_CMD = re.compile(rf"(^|[\s;|&(`$'\"])(\S*[/\\])?{NET_TOOLS}(\.exe)?(?=$|[\s;|&)`'\"])", re.I)  # in a shell string


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


def _is_wildcard(node) -> bool:
    return isinstance(node, ast.Constant) and isinstance(node.value, str) and node.value.strip() in WILDCARD_HOSTS


def _strings(node) -> list[str]:
    """Every literal string inside an expression (lists, tuples, f-string parts, nested calls such as shlex.split)."""
    return [n.value for n in ast.walk(node) if isinstance(n, ast.Constant) and isinstance(n.value, str)]


def _spawns_network_tool(n: ast.Call) -> bool:
    args = list(n.args) + [kw.value for kw in n.keywords if kw.arg in {"args", "cmd", "command", "executable", "file", "path"}]
    for a in args:
        if isinstance(a, (ast.List, ast.Tuple)):                   # list form: any argv element is the tool
            if any(NET_TOOL_ARGV.match(v.strip()) or NET_TOOL_CMD.search(v) for v in _strings(a)):
                return True
        elif any(NET_TOOL_CMD.search(v) for v in _strings(a)):     # string / f-string / shlex.split("...") form
            return True
    return False


def _is_python(node) -> bool:
    """sys.executable (any wrapper such as str(Path(sys.executable))) or a literal python interpreter name."""
    if any(isinstance(x, ast.Attribute) and x.attr == "executable" for x in ast.walk(node)):
        return True
    return any(re.match(r"^(\S*[/\\])?(python|pypy)[\d.]*(\.exe)?$", v.strip(), re.I) for v in _strings(node))


def _spawns_python_code(n: ast.Call) -> bool:
    """subprocess.run([sys.executable, '-c', code]) and friends: an inline program this scanner cannot read.
    `python -m <module>` is flagged too (e.g. `-m http.server`, `-m pip download`). Running a script FILE
    from scripts/ is allowed, because that file is itself scanned."""
    args = list(n.args) + [kw.value for kw in n.keywords if kw.arg in {"args", "cmd", "command"}]
    for a in args:
        if isinstance(a, (ast.List, ast.Tuple)) and a.elts and _is_python(a.elts[0]) \
                and any(v.strip() in {"-c", "-m"} for v in _strings(a)):
            return True
        if not isinstance(a, (ast.List, ast.Tuple)) and any(re.search(r"(^|\s)(python|pypy)[\d.]*(\.exe)?\s+-[cm]\b", v, re.I)
                                                            for v in _strings(a)):
            return True
    return False


def scan_binds(tree: ast.AST, rel: str) -> list[str]:
    """Wildcard bind hosts: host=/bind=... keywords, x.bind((h, p)), --host defaults, HOST = '...' assignments."""
    out: list[str] = []
    for n in ast.walk(tree):
        if isinstance(n, ast.Call):
            for kw in n.keywords:
                if kw.arg in HOST_KWARGS and _is_wildcard(kw.value):
                    out.append(f"{rel}:{n.lineno} wildcard bind {kw.arg}={kw.value.value!r}")
            name = _callee(n.func)
            if name == "bind" and n.args and isinstance(n.args[0], ast.Tuple) and n.args[0].elts \
                    and _is_wildcard(n.args[0].elts[0]):
                out.append(f"{rel}:{n.lineno} wildcard socket bind {n.args[0].elts[0].value!r}")
            if name == "add_argument" and any(isinstance(a, ast.Constant) and isinstance(a.value, str)
                                              and HOST_NAME.search(a.value.lstrip("-").replace("-", "_")) for a in n.args):
                for kw in n.keywords:
                    if kw.arg == "default" and _is_wildcard(kw.value):
                        out.append(f"{rel}:{n.lineno} wildcard --host default {kw.value.value!r}")
            if name in {"run", "Config", "serve"} and len(n.args) >= 2 and _is_wildcard(n.args[1]):
                out.append(f"{rel}:{n.lineno} wildcard positional host {n.args[1].value!r}")
        elif isinstance(n, (ast.Assign, ast.AnnAssign)) and n.value is not None and _is_wildcard(n.value):
            targets = n.targets if isinstance(n, ast.Assign) else [n.target]
            for t in targets:
                tn = t.id if isinstance(t, ast.Name) else t.attr if isinstance(t, ast.Attribute) else ""
                if HOST_NAME.search(tn):
                    out.append(f"{rel}:{n.lineno} wildcard bind host {tn} = {n.value.value!r}")
        elif isinstance(n, ast.Dict):
            for k, v in zip(n.keys, n.values):
                if isinstance(k, ast.Constant) and k.value in HOST_KWARGS and _is_wildcard(v):
                    out.append(f"{rel}:{n.lineno} wildcard bind {{{k.value!r}: {v.value!r}}}")
    return out


def scan_network(text: str, rel: str) -> list[str]:
    """Paper-only violations in one source file (rel = repo-relative posix path)."""
    tree = ast.parse(text, filename=rel)
    allowed = NET_ALLOWED.get(rel, {})
    out: list[str] = []
    aliases: dict[str, str] = {}                       # local name -> top-level network module
    importers = set(DYNAMIC_IMPORTERS)                 # local names bound to import_module / __import__ (G10)
    for n in ast.walk(tree):
        if isinstance(n, ast.ImportFrom) and (n.module or "").split(".")[0] in {"importlib", "builtins", "operator"}:
            for a in n.names:
                if a.name in DYNAMIC_IMPORTERS:
                    importers.add(a.asname or a.name)
                if (n.module or "").split(".")[0] == "builtins":
                    out.append(f"{rel}:{n.lineno} imports {a.name} from builtins")
                if a.name in {"methodcaller", "attrgetter"}:
                    out.append(f"{rel}:{n.lineno} imports operator.{a.name} (dynamic method/attribute by name)")
        if isinstance(n, ast.Import):
            for a in n.names:
                if a.name.split(".")[0] == "builtins":
                    out.append(f"{rel}:{n.lineno} imports builtins")
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
        if isinstance(n, ast.Attribute) and n.attr in INTROSPECTION:
            out.append(f"{rel}:{n.lineno} introspection .{n.attr} (hides names from this scanner)")
        if isinstance(n, ast.Name) and n.id == "__builtins__":
            out.append(f"{rel}:{n.lineno} uses __builtins__")
        if isinstance(n, ast.Constant) and isinstance(n.value, str) and METHOD_OVERRIDE.match(n.value.strip()):
            out.append(f"{rel}:{n.lineno} HTTP method-override header {n.value!r} (a GET that acts as a write)")
        if not isinstance(n, ast.Call):
            continue
        name = _callee(n.func)
        if name in WRITE_CALLS:
            out.append(f"{rel}:{n.lineno} write/connection call {name}()")
        if name in importers and (n.args or n.keywords):
            mod = _const_strs(n.args[0]) if n.args else None
            if mod is None or mod[0].split(".")[0] in NET_MODULES:
                arg = ast.unparse(n.args[0]) if n.args else "..."
                out.append(f"{rel}:{n.lineno} dynamic import {arg}")
        if isinstance(n.func, ast.Name) and n.func.id in CODE_EXEC:
            out.append(f"{rel}:{n.lineno} {n.func.id}() runs code the scanner cannot see")
        if name in {"methodcaller", "attrgetter"}:
            out.append(f"{rel}:{n.lineno} operator.{name}() calls a method/attribute chosen by name")
        if name == "vars" and n.args and isinstance(n.args[0], ast.Name) and n.args[0].id in aliases:
            out.append(f"{rel}:{n.lineno} vars({n.args[0].id}) (reaches any attribute of a network module)")
        if name in PROC_CALLS and _spawns_python_code(n):
            out.append(f"{rel}:{n.lineno} {name}() runs Python code inline (-c / -m): {ast.unparse(n)[:80]}")
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
        if name in PROC_CALLS and _spawns_network_tool(n):
            out.append(f"{rel}:{n.lineno} {name}() runs a network tool (curl/wget/...): {ast.unparse(n)[:80]}")
        if name in PROC_CALLS and any(kw.arg == "shell" and not (isinstance(kw.value, ast.Constant) and kw.value.value is False)
                                      for kw in n.keywords):
            out.append(f"{rel}:{n.lineno} {name}(..., shell=...) runs a shell")
    out += scan_binds(tree, rel)
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
    # SOL-ACCEPT-001 G10: the 7 bypasses Moodle's probes found in the 021aed2 scanner (packet section 5).
    "methodcaller_post": ("src/optpipe/x.py", "import operator\nf = operator.methodcaller('post', 'http://x')\nf(client)\n"),
    "import_module_alias": ("src/optpipe/x.py", "from importlib import import_module as im\nm = im('soc' + 'ket')\n"),
    "builtins_import": ("src/optpipe/x.py", "import builtins\nm = builtins.__dict__['__import__']('socket')\n"),
    "exec_string": ("src/optpipe/x.py", "exec('import socket; socket.socket().connect((\"x\", 80))')\n"),
    "vars_requests_in_fetchers": ("src/optpipe/providers/fetchers.py",
                                  "import requests\nvars(requests)['po' + 'st']('http://x')\n"),
    "requests_get_method_override": ("src/optpipe/providers/fetchers.py",
                                     "import requests\nrequests.get('http://x', headers={'X-HTTP-Method-Override': 'POST'})\n"),
    "subprocess_python_c": ("src/optpipe/x.py", "import subprocess, sys\nsubprocess.run([sys.executable, '-c', "
                            "'import urllib.request as u; u.urlopen(u.Request(\"http://x\", data=b\"1\"))'])\n"),
}
# G10 variants of the same 7 holes (aliases / spellings the first fix could miss).
SCANNER_CASES_G10_VARIANTS = {
    "methodcaller_from_import": ("src/optpipe/x.py", "from operator import methodcaller as mc\nmc('put', u)(c)\n"),
    "import_module_via_importlib_alias": ("src/optpipe/x.py", "import importlib as il\nm = il.import_module(name)\n"),
    "builtins_dunder_name": ("src/optpipe/x.py", "imp = __builtins__['__import__']\n"),
    "from_builtins_import": ("src/optpipe/x.py", "from builtins import __import__ as i\ni('socket')\n"),
    "eval_string": ("src/optpipe/x.py", "eval(code)\n"),
    "compile_then_exec": ("scripts/x.py", "c = compile(src, 'x', 'exec')\n"),
    "requests_dunder_dict": ("src/optpipe/providers/fetchers.py", "import requests\nrequests.__dict__['post'](u)\n"),
    "method_override_lowercase": ("src/optpipe/providers/fetchers.py",
                                  "import requests\nH = {'x-http-method': 'DELETE'}\nrequests.get(u, headers=H)\n"),
    "x_method_override": ("src/optpipe/x.py", "H = {'X-Method-Override': 'PUT'}\n"),
    "subprocess_python_m": ("scripts/x.py", "import subprocess, sys\nsubprocess.run([sys.executable, '-m', 'http.server'])\n"),
    "subprocess_python_c_string": ("scripts/x.py", "import subprocess\nsubprocess.run('python3 -c \"print(1)\"', shell=False)\n"),
    "popen_python_path_c": ("scripts/x.py", "import subprocess, sys\nfrom pathlib import Path\n"
                            "subprocess.Popen([str(Path(sys.executable)), '-c', code])\n"),
}
# FIX-SEC-001 follow-up (Moodle, #3): wildcard binds and process calls that run curl/wget. Each is missed by c3b98b9.
BIND_CASES = {
    "bind_uvicorn_ipv6_any": ("src/optpipe/cli.py", "import uvicorn\nuvicorn.run(app, host='::', port=8765)\n"),
    "bind_uvicorn_ipv6_any_brackets": ("src/optpipe/cli.py", "uvicorn.run(app, host='[::]', port=1)\n"),
    "bind_uvicorn_empty": ("src/optpipe/cli.py", "uvicorn.run(app, host='', port=1)\n"),
    "bind_uvicorn_positional": ("src/optpipe/cli.py", "uvicorn.run(app, '::', 8765)\n"),
    "bind_config_star": ("scripts/serve.py", "cfg = uvicorn.Config(app, host='*')\n"),
    "bind_socket_ipv6_any": ("src/optpipe/x.py", "s = make()\ns.bind(('::', 8765))\n"),
    "bind_socket_empty": ("src/optpipe/x.py", "s = make()\ns.bind(('', 8765))\n"),
    "bind_argparse_default": ("src/optpipe/cli.py", "sv.add_argument('--host', default='::')\n"),
    "bind_argparse_bind_default": ("scripts/x.py", "sv.add_argument('--bind-host', default='0.0.0.0')\n"),
    "bind_constant": ("src/optpipe/cli.py", "DEFAULT_HOST = '::'\n"),
    "bind_settings_dict": ("src/optpipe/x.py", "CONF = {'host': '::'}\n"),
    "bind_any_v6_long": ("src/optpipe/x.py", "uvicorn.run(app, host='0:0:0:0:0:0:0:0')\n"),
}
PROC_CASES = {
    "proc_run_list_curl": ("src/optpipe/x.py", "import subprocess\nsubprocess.run(['curl', '-X', 'POST', u])\n"),
    "proc_run_list_abs_wget": ("scripts/x.py", "import subprocess\nsubprocess.run(['/usr/bin/wget', '--post-data=x', u])\n"),
    "proc_check_output_str_curl": ("src/optpipe/x.py", "import subprocess\nsubprocess.check_output('curl -d x ' + u)\n"),
    "proc_popen_shell_true": ("scripts/x.py", "import subprocess as sp\nsp.Popen(f'wget {u}', shell=True)\n"),
    "proc_shell_true_pipe": ("src/optpipe/x.py", "import subprocess\nsubprocess.call('echo x | curl -T - ' + u, shell=True)\n"),
    "proc_shell_true_variable": ("src/optpipe/x.py", "import subprocess\nsubprocess.run(cmd, shell=True)\n"),
    "proc_from_import_run": ("src/optpipe/x.py", "from subprocess import run\nrun(('curl', u))\n"),
    "proc_shlex_split": ("scripts/x.py", "import shlex, subprocess\nsubprocess.run(shlex.split('curl -X PUT ' + u))\n"),
    "proc_args_keyword": ("scripts/x.py", "import subprocess\nsubprocess.run(args=['wget', u])\n"),
    "proc_os_system_curl": ("src/optpipe/x.py", "import os\nos.system('curl -s ' + u)\n"),
    "proc_os_popen_wget": ("scripts/x.py", "import os\nos.popen(f'wget -qO- {u}').read()\n"),
    "proc_os_execvp": ("src/optpipe/x.py", "import os\nos.execvp('curl', ['curl', u])\n"),
    "proc_asyncio_exec": ("src/optpipe/x.py", "async def f(aio):\n    await aio.create_subprocess_exec('wget', u)\n"),
    "proc_curl_exe": ("scripts/x.py", "import subprocess\nsubprocess.run(['C:\\\\tools\\\\curl.exe', u])\n"),
}


@pytest.mark.parametrize("case", list(SCANNER_CASES))
def test_scanner_catches(case):
    rel, text = SCANNER_CASES[case]
    assert scan_network(text, rel), f"{case} not caught"


@pytest.mark.parametrize("case", list(SCANNER_CASES_G10_VARIANTS))
def test_scanner_catches_g10_variants(case):
    rel, text = SCANNER_CASES_G10_VARIANTS[case]
    assert scan_network(text, rel), f"{case} not caught"


@pytest.mark.parametrize("rel,text", [
    ("src/optpipe/x.py", "import re\nP = re.compile(r'x')\n"),                       # re.compile is not compile()
    ("scripts/x.py", "import subprocess, sys\nsubprocess.run([sys.executable, str(script), '--check'])\n"),  # a scanned file
    ("src/optpipe/x.py", "from importlib import resources\nresources.files('optpipe')\n"),
    ("src/optpipe/x.py", "import importlib\nm = importlib.import_module('optpipe.models')\n"),  # literal, non-network
    ("src/optpipe/x.py", "H = {'Accept': 'text/html', 'User-Agent': 'optpipe'}\n"),
])
def test_scanner_g10_allows_ordinary_code(rel, text):
    """Controls for G10: the new checks do not flag ordinary local code."""
    assert scan_network(text, rel) == []


@pytest.mark.parametrize("case", list(BIND_CASES))
def test_scanner_catches_wildcard_bind(case):
    rel, text = BIND_CASES[case]
    assert scan_network(text, rel), f"{case} not caught"


@pytest.mark.parametrize("case", list(PROC_CASES))
def test_scanner_catches_network_subprocess(case):
    rel, text = PROC_CASES[case]
    assert scan_network(text, rel), f"{case} not caught"


@pytest.mark.parametrize("rel,text", [
    ("src/optpipe/cli.py", "uvicorn.run(app, host=host, port=port)\nLOOPBACK = {'127.0.0.1', '::1'}\n"
                           "sv.add_argument('--host', default='127.0.0.1')\n"),
    ("src/optpipe/cli.py", "uvicorn.run(app, host='::1')\nuvicorn.run(app, host='127.0.0.1')\n"),
    ("scripts/gen_models.py", "import subprocess, sys\nfrom pathlib import Path\n"
                              "subprocess.run([str(Path(sys.executable).parent / 'datamodel-codegen'), '--input', s],"
                              " check=True, capture_output=True)\n"),
    ("scripts/x.py", "import subprocess\nsubprocess.run(['git', 'rev-parse', 'HEAD'], check=True, shell=False)\n"),
    ("src/optpipe/x.py", "w.run(SimClock(t), until)\nprint('see README: curl is not used')\n"),
])
def test_scanner_allows_loopback_and_local_subprocess(rel, text):
    assert scan_network(text, rel) == []


def test_real_datamodel_codegen_call_still_allowed():
    """Control: the one real subprocess use (scripts/gen_models.py -> datamodel-codegen) is scanned and passes."""
    text = (ROOT / "scripts" / "gen_models.py").read_text()
    assert "subprocess.run(" in text and "datamodel-codegen" in text
    assert scan_network(text, "scripts/gen_models.py") == []


def test_dashboard_loopback_set_has_no_wildcard():
    from optpipe import cli
    assert cli.LOOPBACK == {"127.0.0.1", "::1"} and not (cli.LOOPBACK & WILDCARD_HOSTS)


@pytest.mark.parametrize("rel,text", [
    ("src/optpipe/providers/fetchers.py", "import requests\nr = requests.get(u, timeout=5)\n"),
    ("src/optpipe/dashboard/app.py", "@app.get('/x')\ndef h(): ...\napp.add_api_route('/y', h, methods=['GET'])\n"),
    ("src/optpipe/records/fields.py", "F(1, method='derived')\n"),
])
def test_scanner_allows_get_only_code(rel, text):
    assert scan_network(text, rel) == []


def test_no_0_0_0_0_literal_in_sources():
    """No all-interfaces bind literal anywhere in runnable code (0.0.0.0 textually; '::', '' etc. via scan_binds)."""
    for p in SOURCES:
        assert "0.0.0.0" not in p.read_text(), _rel(p)
        assert scan_binds(ast.parse(p.read_text()), _rel(p)) == [], _rel(p)


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
    for rv in r["risk_reviews"]:              # an honest PASS: results and overall agree (INV-16, FIX-SEC-001)
        for x in rv["results"]:
            x["result"] = "PASS"
        rv["overall"].update(verdict="PASS", blocking_rule_ids=[], flag_rule_ids=[])
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
