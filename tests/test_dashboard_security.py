"""Local HTTP authority checks use direct ASGI calls: no listener, browser or network."""
import anyio
import pytest

from optpipe.dashboard import create_app


def _request(app, headers, path="/healthz"):
    messages = []

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def capture(message):
        messages.append(message)

    async def invoke():
        await app({
            "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
            "method": "GET", "scheme": "http", "path": path,
            "raw_path": path.encode("ascii"), "query_string": b"", "root_path": "",
            "headers": headers, "server": ("127.0.0.1", 8765),
            "client": ("127.0.0.1", 54321),
        }, receive, capture)

    anyio.run(invoke)
    status = next(m["status"] for m in messages if m["type"] == "http.response.start")
    body = b"".join(m.get("body", b"") for m in messages if m["type"] == "http.response.body")
    return status, body


@pytest.mark.parametrize("authority", [
    b"127.0.0.1", b"127.0.0.1:8765", b"[::1]", b"[::1]:8765",
    b"localhost", b"LOCALHOST:8765", b"127.0.0.1:1", b"[::1]:65535",
])
def test_local_authority_reaches_normal_routes(tmp_path, authority):
    app = create_app(tmp_path)
    assert _request(app, [(b"host", authority)])[0] == 200
    status, body = _request(app, [(b"host", authority)], "/")
    assert status == 200 and b"No sessions yet" in body


INVALID_HEADERS = [
    pytest.param([], id="missing-host"),
    pytest.param([(b"host", b"")], id="empty-host"),
    pytest.param([(b"host", b"example.com")], id="foreign-host"),
    pytest.param([(b"host", b"testserver")], id="no-test-client-exception"),
    pytest.param([(b"host", b"localhost.example.com")], id="hostname-suffix"),
    pytest.param([(b"host", b"localhost.")], id="noncanonical-trailing-dot"),
    pytest.param([(b"host", b"127.0.0.1.example.com")], id="address-suffix"),
    pytest.param([(b"host", b"127.0.0.1@example.com")], id="userinfo"),
    pytest.param([(b"host", b"127.0.0.1/anything")], id="path-in-host"),
    pytest.param([(b"host", b"127.0.0.1,example.com")], id="combined-hosts"),
    pytest.param([(b"host", b"127.0.0.1"), (b"host", b"example.com")], id="duplicate-hosts"),
    pytest.param([(b"Host", b"127.0.0.1"), (b"HOST", b"127.0.0.1")], id="duplicate-same-host"),
    pytest.param([(b"host", b" 127.0.0.1")], id="leading-whitespace"),
    pytest.param([(b"host", b"127.0.0.1\t")], id="trailing-whitespace"),
    pytest.param([(b"host", b"localhost\x00")], id="nul-byte"),
    pytest.param([(b"host", b"local\xffhost")], id="nonascii-byte"),
    pytest.param([(b"host", b"127.0.0.1:")], id="empty-port"),
    pytest.param([(b"host", b"127.0.0.1:0")], id="zero-port"),
    pytest.param([(b"host", b"127.0.0.1:65536")], id="port-overflow"),
    pytest.param([(b"host", b"127.0.0.1:-1")], id="negative-port"),
    pytest.param([(b"host", b"localhost:http")], id="nonnumeric-port"),
    pytest.param([(b"host", b"::1")], id="unbracketed-ipv6"),
    pytest.param([(b"host", b"[::1")], id="missing-ipv6-bracket"),
    pytest.param([(b"host", b"[::1]:8765:80")], id="extra-port"),
    pytest.param([(b"host", b"[::ffff:127.0.0.1]")], id="mapped-address-alias"),
    pytest.param([(b"host", b"2130706433")], id="integer-address-alias"),
    pytest.param([(b"host", b"127.1")], id="short-address-alias"),
    pytest.param([(b"host", b"127.0.0.2")], id="different-loopback-address"),
]


@pytest.mark.parametrize("headers", INVALID_HEADERS)
def test_invalid_authority_is_rejected_before_session_file_read(tmp_path, monkeypatch, headers):
    app = create_app(tmp_path)
    reads = []
    sentinel = b"LOCAL_RECORD_SENTINEL"
    monkeypatch.setattr(app.state.out, "read_contained", lambda name: reads.append(name) or sentinel)
    status, body = _request(app, headers, "/files/session-2026-10-13.json")
    assert status == 400
    assert reads == [] and sentinel not in body
    assert body == b"Invalid Host header"


@pytest.mark.parametrize("path", ["/", "/healthz", "/history", "/static/htmx.min.js"])
def test_host_check_covers_html_health_and_static_routes(tmp_path, monkeypatch, path):
    app = create_app(tmp_path)
    reads = []
    monkeypatch.setattr(app.state.out, "dates", lambda: reads.append("dates") or [])
    status, body = _request(app, [(b"host", b"rebind.example.com")], path)
    assert status == 400 and body == b"Invalid Host header"
    assert reads == []


def test_forwarding_headers_do_not_authorize_an_untrusted_host(tmp_path, monkeypatch):
    app = create_app(tmp_path)
    reads = []
    monkeypatch.setattr(app.state.out, "read_contained", lambda name: reads.append(name) or b"LOCAL_RECORD")
    headers = [(b"host", b"rebind.example.com"), (b"x-forwarded-host", b"127.0.0.1"),
               (b"forwarded", b"host=localhost;for=127.0.0.1")]
    assert _request(app, headers, "/files/session-2026-10-13.json")[0] == 400
    assert reads == []


def test_forwarding_headers_do_not_replace_the_accepted_host(tmp_path):
    app = create_app(tmp_path)
    headers = [(b"host", b"127.0.0.1:8765"), (b"x-forwarded-host", b"example.com"),
               (b"forwarded", b"host=example.com")]
    assert _request(app, headers)[0] == 200
