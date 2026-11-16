"""--live was removed in FEAT-OODLE-01 PR B. Pack quotes replace it; the HTTP LiveNasdaqSource stays for PR C.

The former opt-in live network test is gone: there is no CLI path that does a live GET. A pack-backed watch is
covered offline in tests/test_pr_b_packs.py (fixtures only, OPTIONS_SPEC_DIR=spec).
"""
from __future__ import annotations

import pytest


def test_watch_live_flag_is_gone(tmp_path, capsys):
    from optpipe.cli import main
    with pytest.raises(SystemExit) as ei:
        main(["watch", "--live", "--once", "--out", str(tmp_path)])
    assert ei.value.code == 2
    assert "unrecognized arguments: --live" in capsys.readouterr().err
    with pytest.raises(SystemExit) as ei:
        main(["watch", "--help"])
    assert ei.value.code == 0
    out = capsys.readouterr().out
    assert "--data-dir" in out
    assert "\n  --live" not in out and " [--live]" not in out
