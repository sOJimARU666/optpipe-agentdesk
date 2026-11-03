"""SOL-ACCEPT-001 G9 follow-up: the runtime network guard covers the CI steps outside pytest too.

- .github/workflows/ci.yml turns the guard on (PYTHONPATH=tests/netguard first, OPTPIPE_NETGUARD=1) for every step
  after Install, and only after it, and has its own step running tests/netguard/ci_check_blocked.py.
- ci_check_blocked.py exits 0 only when a socket connect, a urllib GET and a requests GET all raise the guard's
  NetworkBlocked, and 1 when the guard is off (OPTPIPE_NETGUARD unset, or tests/netguard not on PYTHONPATH), when an
  attempt reaches the network stack, or when one connects.
Targets are loopback 127.0.0.1:9 only. PAPER ONLY."""
from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
import types
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
NETGUARD = ROOT / "tests" / "netguard"
CHECK = NETGUARD / "ci_check_blocked.py"
CI = ROOT / ".github" / "workflows" / "ci.yml"
ATTEMPTS = ("socket.create_connection", "urllib GET", "requests GET")


def _steps():
    return yaml.safe_load(CI.read_text())["jobs"]["test"]["steps"]


def _text(step) -> str:
    return " ".join(str(step.get(k, "")) for k in ("uses", "run", "env", "with"))


def _install_index(steps) -> int:
    return next(i for i, s in enumerate(steps) if s.get("name") == "Install")


def test_ci_turns_the_guard_on_for_every_step_after_install():
    steps = _steps()
    i = _install_index(steps)
    guard = steps[i + 1]
    run = guard.get("run", "")
    assert 'PYTHONPATH=$GITHUB_WORKSPACE/tests/netguard${PYTHONPATH:+:$PYTHONPATH}" >> "$GITHUB_ENV"' in run, (
        f"step after Install must put tests/netguard first on PYTHONPATH for all later steps, keeping the old one: {guard}")
    assert 'OPTPIPE_NETGUARD=1" >> "$GITHUB_ENV"' in run, f"step after Install must set OPTPIPE_NETGUARD=1: {guard}"
    later = steps[i + 2:]
    assert later, "no steps after the guard"
    for s in later:                         # nothing later switches it off or replaces PYTHONPATH
        env = s.get("env") or {}
        assert "OPTPIPE_NETGUARD" not in env and "PYTHONPATH" not in env, f"step {s.get('name')!r} overrides the guard env"
        assert "OPTPIPE_NETGUARD=0" not in _text(s) and "GITHUB_ENV" not in _text(s), s.get("name")
        assert "pip install" not in _text(s), f"step {s.get('name')!r} runs pip under the guard (install in Install)"


def test_ci_guard_is_not_on_before_install():
    steps = _steps()
    i = _install_index(steps)
    for s in steps[: i + 1]:
        assert "netguard" not in _text(s).lower() and "OPTPIPE_NETGUARD" not in _text(s), s.get("name")
    assert not {"PYTHONPATH", "OPTPIPE_NETGUARD"} & set(yaml.safe_load(CI.read_text()).get("env") or {})
    assert not {"PYTHONPATH", "OPTPIPE_NETGUARD"} & set(yaml.safe_load(CI.read_text())["jobs"]["test"].get("env") or {})


def test_ci_has_a_step_proving_a_connection_is_blocked():
    steps = _steps()
    i = _install_index(steps)
    names = [s for s in steps[i + 2:] if "tests/netguard/ci_check_blocked.py" in s.get("run", "")]
    assert names, "no CI step runs tests/netguard/ci_check_blocked.py under the guard env"
    assert not names[0].get("continue-on-error"), "the check step must fail the job"


def _run(env) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(CHECK)], env=env, cwd=ROOT, capture_output=True, text=True, timeout=120)


@pytest.mark.netguard_expected
def test_ci_check_passes_under_the_guard_env():
    """The env every subprocess of the suite gets (tests/conftest.py: OPTPIPE_NETGUARD=1, tests/netguard on
    PYTHONPATH), which is the env ci.yml gives every step after Install: every attempt is blocked, exit 0."""
    r = _run(dict(os.environ))
    assert r.returncode == 0, r.stdout + r.stderr
    for name in ATTEMPTS:
        assert f"ok: {name} blocked by the network guard" in r.stdout, r.stdout


def _without_guard(how: str) -> dict:
    env = dict(os.environ)
    if how == "OPTPIPE_NETGUARD_unset":
        env.pop("OPTPIPE_NETGUARD", None)
    else:
        keep = [p for p in env.get("PYTHONPATH", "").split(os.pathsep) if p and Path(p).resolve() != NETGUARD]
        env["PYTHONPATH"] = os.pathsep.join(keep)
    return env


@pytest.mark.parametrize("how", ["OPTPIPE_NETGUARD_unset", "guard_not_on_path"])
def test_ci_check_fails_without_the_guard(how):
    """Negative control: with the guard off, the check sees real (refused, loopback) attempts and exits 1."""
    r = _run(_without_guard(how))
    assert r.returncode == 1, r.stdout + r.stderr
    assert "the network guard is not loaded" in r.stdout
    for name in ATTEMPTS:
        assert f"FAIL: {name} was not blocked by the guard" in r.stdout, r.stdout


def _load_check():
    spec = importlib.util.spec_from_file_location("ci_check_blocked_under_test", CHECK)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class _Blocked(BaseException):
    pass


@pytest.mark.parametrize("outcome", ["connects", "refused", "blocked"])
def test_ci_check_exit_code_per_attempt_outcome(monkeypatch, outcome):
    """In-process, no socket: a fake loaded guard and fake attempts. Exit 0 only when every attempt is blocked;
    an attempt that connects or reaches the network stack (refused) makes the check exit 1."""
    mod = _load_check()
    monkeypatch.setitem(sys.modules, "optpipe_netguard",
                        types.SimpleNamespace(installed=lambda: True, NetworkBlocked=_Blocked))

    def blocked():
        raise _Blocked("network guard: blocked")

    def other():
        if outcome == "connects":
            return None
        if outcome == "refused":
            raise ConnectionRefusedError(111, "Connection refused")
        raise _Blocked("network guard: blocked")

    monkeypatch.setattr(mod, "ATTEMPTS", {"a": blocked, "b": other})
    assert mod.main() == (0 if outcome == "blocked" else 1)
