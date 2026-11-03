"""Run only by tests/test_sol_g9_network_guard.py in a pytest subprocess; never collected by the main suite.
Each test here shows one property of the conftest hook. Targets are loopback port 9 only, so nothing leaves the box."""
import os
import socket
import subprocess
import sys

import pytest


def test_swallowed_in_process_violation_still_fails():
    try:
        socket.create_connection(("127.0.0.1", 9), timeout=1)
    except BaseException:       # code under test swallowing everything must not hide the call
        pass


def test_swallowed_subprocess_violation_still_fails():
    code = ("import urllib.request\n"
            "try:\n    urllib.request.urlopen('http://127.0.0.1:9/', timeout=1)\n"
            "except BaseException:\n    pass\n")
    assert subprocess.run([sys.executable, "-c", code], timeout=60).returncode == 0


@pytest.mark.live
def test_live_test_is_exempt():
    assert os.environ.get("OPTPIPE_NETGUARD") == "0"
    with pytest.raises(OSError):                       # the real socket layer answers: refused, not blocked
        socket.create_connection(("127.0.0.1", 9), timeout=1)
    r = subprocess.run([sys.executable, "-c", "import socket; print(hasattr(socket.socket.connect, '__wrapped__'))"],
                       capture_output=True, text=True, timeout=60)
    assert r.stdout.strip() == "False"


def test_clean_test_passes():
    assert 1 + 1 == 2
