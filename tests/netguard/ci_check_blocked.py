"""CI check (SOL-ACCEPT-001 netguard follow-up): prove the network guard is really on in THIS process environment.

Run as `python tests/netguard/ci_check_blocked.py` with PYTHONPATH=tests/netguard and OPTPIPE_NETGUARD=1, the env
.github/workflows/ci.yml sets for every step after Install. It does not install the guard itself: the guard must
already be in place from tests/netguard/sitecustomize.py at interpreter start-up, exactly as for the CLI and the
scripts/*.py the other CI steps run.

Each attempt below must raise the guard's NetworkBlocked. Exit 0 only when all of them do. Exit 1 when any attempt
reaches the real network stack (refused, timed out, or connected) or fails for another reason, and when the guard
is not loaded at all. Targets are loopback 127.0.0.1:9 (discard) only, so nothing can leave the runner.
PAPER ONLY: GET only, no broker."""
from __future__ import annotations

import socket
import sys
import urllib.request

TARGET = ("127.0.0.1", 9)
URL = "http://127.0.0.1:9/"


def _socket():
    socket.create_connection(TARGET, timeout=2).close()


def _urllib():
    urllib.request.urlopen(URL, timeout=2).close()


def _requests():
    import requests
    requests.get(URL, timeout=2).close()


ATTEMPTS = {"socket.create_connection": _socket, "urllib GET": _urllib, "requests GET": _requests}


def main() -> int:
    guard = sys.modules.get("optpipe_netguard")
    if guard is None or not guard.installed():
        print("FAIL: the network guard is not loaded at start-up (OPTPIPE_NETGUARD=1 and PYTHONPATH=tests/netguard "
              "are needed); every attempt below would reach the network stack")
        rc = 1
    else:
        rc = 0
    blocked_type = getattr(guard, "NetworkBlocked", None)
    for name, fn in ATTEMPTS.items():
        try:
            fn()
        except BaseException as e:                     # NetworkBlocked is a BaseException on purpose
            if blocked_type is not None and isinstance(e, blocked_type):
                print(f"ok: {name} blocked by the network guard ({e})")
                continue
            print(f"FAIL: {name} was not blocked by the guard: it was attempted and raised {type(e).__name__}: {e}")
        else:
            print(f"FAIL: {name} was not blocked by the guard: it connected")
        rc = 1
    print("netguard CI check:", "PASS" if rc == 0 else "FAIL")
    return rc


if __name__ == "__main__":
    sys.exit(main())
