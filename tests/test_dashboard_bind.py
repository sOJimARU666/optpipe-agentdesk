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


@pytest.mark.parametrize("host", ["0.0.0.0", "::", "[::]", "", " ", "*", "0", "::0", "0:0:0:0:0:0:0:0", "[::1]", " ::1",
                                  "::ffff:127.0.0.1", "127.0.0.2", "192.168.1.5", "10.0.0.1", "example.com", "localhost"])
def test_serve_refuses_all_interfaces(runs, tmp_path, capsys, host):
    assert cli.main(["serve", "--out", str(tmp_path), "--host", host]) == 2
    assert runs == []                                    # uvicorn.run never called
    assert "refusing to bind" in capsys.readouterr().err


def test_serve_loopback_v6_ok(runs, tmp_path):
    assert cli.main(["serve", "--out", str(tmp_path), "--host", "::1"]) == 0
    assert runs[0]["host"] == "::1"


def test_loopback_set_is_literal_addresses_only():
    assert cli.LOOPBACK == {"127.0.0.1", "::1"}


# SOL-ACCEPT-001 G11 (regression; the behaviour was already correct at 1d077a74 and 021aed2). A host taken from the
# environment must never reach uvicorn: `optpipe serve` passes an explicit loopback host, and no uds / fd bind.
ENV_HOSTS = ["UVICORN_HOST", "OPTPIPE_HOST", "HOST"]


@pytest.mark.parametrize("env", ENV_HOSTS + ["all_three"])
@pytest.mark.parametrize("value", ["0.0.0.0", "::"])
def test_serve_ignores_env_host(runs, tmp_path, monkeypatch, env, value):
    for name in (ENV_HOSTS if env == "all_three" else [env]):
        monkeypatch.setenv(name, value)
    assert cli.main(["serve", "--out", str(tmp_path)]) == 0
    assert len(runs) == 1 and runs[0]["host"] == "127.0.0.1"
    assert not {"uds", "fd"} & set(runs[0])                          # no unix-socket / inherited-fd bind either


@pytest.mark.parametrize("env", ENV_HOSTS)
def test_serve_env_host_does_not_unlock_a_refused_host(runs, tmp_path, capsys, monkeypatch, env):
    """An env var naming loopback does not make an explicit --host 0.0.0.0 acceptable."""
    monkeypatch.setenv(env, "127.0.0.1")
    assert cli.main(["serve", "--out", str(tmp_path), "--host", "0.0.0.0"]) == 2
    assert runs == [] and "refusing to bind" in capsys.readouterr().err
