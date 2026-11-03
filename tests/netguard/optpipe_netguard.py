"""Runtime network guard for the test suite (SOL-ACCEPT-001 G9). PAPER ONLY.

While installed, every way the code base (src/ AND scripts/) could reach the network fails:

- the socket layer: socket.socket.connect / connect_ex / sendto / sendmsg on IP sockets,
  socket.create_connection, and socket.getaddrinfo for any non-loopback name;
- requests: Session.request and Session.send. A non-GET is reported as such, and a GET is still a network call;
- urllib.request.urlopen and OpenerDirector.open;
- http.client: HTTPConnection.connect and .request (HTTPSConnection inherits them);
- httpx: Client.send and AsyncClient.send on a real network transport, plus HTTPTransport.handle_request and
  AsyncHTTPTransport.handle_async_request. In-process transports such as Starlette's TestClient and httpx.MockTransport
  open no socket and are allowed;
- asyncio: open_connection and BaseEventLoop.create_connection;
- aiohttp ClientSession._request, if aiohttp is installed (it is not in this repo).

A blocked call raises NetworkBlocked. It derives from BaseException, so an `except Exception` in the code under
test (for example HttpFetcher turning errors into DataUnavailable) cannot swallow it. Every violation is also
appended to the file named by OPTPIPE_NETGUARD_LOG. The conftest fails the test that caused it, including
violations inside a subprocess such as the optpipe CLI or a scripts/*.py run. Subprocesses install the same guard
through tests/netguard/sitecustomize.py, which the conftest puts on PYTHONPATH.

AF_UNIX sockets are not network and are allowed. Loopback TCP is blocked too: no test needs a real socket."""
from __future__ import annotations

import functools
import os
import socket
import sys
import threading

__all__ = ["NetworkBlocked", "install", "uninstall", "installed", "suspended", "violations", "clear"]

_ORIG: dict[tuple[object, str], object] = {}
_STATE = threading.local()
_VIOLATIONS: list[str] = []
_LOOPBACK_NAMES = {None, "", "localhost", "127.0.0.1", "::1", b"localhost", b"127.0.0.1", b"::1"}


class NetworkBlocked(BaseException):
    """Raised by the runtime network guard. Deliberately not an Exception subclass."""


def _blocked(what: str):
    if getattr(_STATE, "off", False):
        return None
    msg = f"network guard: {what} is blocked during tests (PAPER ONLY, offline suite)"
    _VIOLATIONS.append(msg)
    log = os.environ.get("OPTPIPE_NETGUARD_LOG")
    if log:
        try:
            with open(log, "a", encoding="utf-8") as fh:
                fh.write(f"pid {os.getpid()}: {msg}\n")
        except OSError:
            pass
    raise NetworkBlocked(msg)


def violations() -> list[str]:
    return list(_VIOLATIONS)


def clear() -> None:
    _VIOLATIONS.clear()


class suspended:
    """Context manager that turns the guard off in this thread (used for the opt-in `live` tests)."""

    def __enter__(self):
        self._prev = getattr(_STATE, "off", False)
        _STATE.off = True

    def __exit__(self, *exc):
        _STATE.off = self._prev
        return False


def _patch(owner, name, make):
    if (owner, name) in _ORIG:
        return
    orig = getattr(owner, name)
    _ORIG[(owner, name)] = orig
    setattr(owner, name, make(orig))


def _off() -> bool:
    return getattr(_STATE, "off", False)


def _is_ip(sock) -> bool:
    fam = getattr(sock, "family", None)
    return fam in (socket.AF_INET, socket.AF_INET6)


def _sock_method(name):
    def make(orig):
        @functools.wraps(orig)
        def guarded(self, *a, **kw):
            if _is_ip(self) and not _off():
                _blocked(f"socket.{name}{a[-1:] if name.startswith('send') else a[:1]}")
            return orig(self, *a, **kw)
        return guarded
    return make


def _always(label):
    def make(orig):
        @functools.wraps(orig)
        def guarded(*a, **kw):
            if not _off():
                _blocked(label)
            return orig(*a, **kw)
        return guarded
    return make


def _method_label(prefix):
    """requests Session.request(method, url): name the HTTP method so a non-GET is visible as such."""
    def make(orig):
        @functools.wraps(orig)
        def guarded(self, method=None, url=None, *a, **kw):
            if not _off():
                m = str(method).upper()
                _blocked(f"{prefix} {'non-GET ' if m != 'GET' else ''}{m} {url}")
            return orig(self, method, url, *a, **kw)
        return guarded
    return make


def _getaddrinfo(orig):
    @functools.wraps(orig)
    def guarded(host, *a, **kw):
        if host not in _LOOPBACK_NAMES and not _off():
            _blocked(f"socket.getaddrinfo({host!r})")
        return orig(host, *a, **kw)
    return guarded


def _httpx_send(label, real_transports):
    def make(orig):
        @functools.wraps(orig)
        def guarded(self, request, *a, **kw):
            if not _off():
                try:
                    tr = self._transport_for_url(request.url)
                except Exception:
                    tr = getattr(self, "_transport", None)
                if isinstance(tr, real_transports):
                    _blocked(f"{label} {request.method} {request.url}")
            return orig(self, request, *a, **kw)
        return guarded
    return make


def install() -> None:
    """Idempotent. Patches the network stack in this process."""
    for name in ("connect", "connect_ex", "sendto", "sendmsg"):
        if hasattr(socket.socket, name):
            _patch(socket.socket, name, _sock_method(name))
    _patch(socket, "create_connection", _always("socket.create_connection"))
    _patch(socket, "getaddrinfo", _getaddrinfo)

    import http.client
    _patch(http.client.HTTPConnection, "connect", _always("http.client.HTTPConnection.connect"))
    _patch(http.client.HTTPConnection, "request", _always("http.client.HTTPConnection.request"))

    import urllib.request
    _patch(urllib.request, "urlopen", _always("urllib.request.urlopen"))
    _patch(urllib.request.OpenerDirector, "open", _always("urllib.request.OpenerDirector.open"))

    import asyncio
    import asyncio.base_events
    import asyncio.streams
    _patch(asyncio.streams, "open_connection", _always("asyncio.open_connection"))
    if asyncio.open_connection is not asyncio.streams.open_connection:
        _patch(asyncio, "open_connection", _always("asyncio.open_connection"))
    _patch(asyncio.base_events.BaseEventLoop, "create_connection", _always("asyncio loop.create_connection"))

    try:
        import requests
    except ImportError:
        pass
    else:
        _patch(requests.Session, "request", _method_label("requests.Session.request"))
        _patch(requests.Session, "send", _always("requests.Session.send"))

    try:
        import httpx
    except ImportError:
        pass
    else:
        real = (httpx.HTTPTransport, httpx.AsyncHTTPTransport)
        _patch(httpx.Client, "send", _httpx_send("httpx.Client.send", real))
        _patch(httpx.AsyncClient, "send", _httpx_send("httpx.AsyncClient.send", real))
        _patch(httpx.HTTPTransport, "handle_request", _always("httpx.HTTPTransport.handle_request"))
        _patch(httpx.AsyncHTTPTransport, "handle_async_request", _always("httpx.AsyncHTTPTransport.handle_async_request"))

    try:
        import aiohttp
    except ImportError:
        pass
    else:
        _patch(aiohttp.ClientSession, "_request", _always("aiohttp.ClientSession._request"))


def uninstall() -> None:
    for (owner, name), orig in reversed(list(_ORIG.items())):
        setattr(owner, name, orig)
    _ORIG.clear()


def installed() -> bool:
    return bool(_ORIG)
