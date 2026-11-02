"""FIX-SEC-001 item 7: the dashboard binds to literal loopback only (127.0.0.1 or ::1), never all interfaces.
uvicorn.run is monkeypatched, so nothing here opens a socket."""
import pytest
import uvicorn

from optpipe import cli


@pytest.fixture
def runs(monkeypatch):
    calls = []
    monkeypatch.setattr(uvicorn, "run", lambda app, **kw: calls.append(kw))
    return calls


def test_serve_default_host_is_127_0_0_1(runs, tmp_path):
    assert cli.main(["serve", "--out", str(tmp_path)]) == 0
    assert len(runs) == 1 and runs[0]["host"] == "127.0.0.1"


@pytest.mark.parametrize("host", ["0.0.0.0", "::", "", "192.168.1.5", "10.0.0.1", "example.com", "localhost"])
def test_serve_refuses_all_interfaces(runs, tmp_path, capsys, host):
    assert cli.main(["serve", "--out", str(tmp_path), "--host", host]) == 2
    assert runs == []                                    # uvicorn.run never called
    assert "refusing to bind" in capsys.readouterr().err


def test_serve_loopback_v6_ok(runs, tmp_path):
    assert cli.main(["serve", "--out", str(tmp_path), "--host", "::1"]) == 0
    assert runs[0]["host"] == "::1"


def test_loopback_set_is_literal_addresses_only():
    assert cli.LOOPBACK == {"127.0.0.1", "::1"}
