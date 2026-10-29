#!/usr/bin/env python3
"""Export static dashboard snapshots to docs/dashboard/ (Milestone 2 deliverable).

  python scripts/export_dashboard.py [--out-dir OUT] [--dest docs/dashboard] [--no-png]

Without --out-dir it builds a fresh offline fixture session (premarket run + --append 09:40 ET run + a
replay `optpipe watch` over the hand-made fictional tape) in a temp dir, so the snapshots are reproducible. Pages are rendered by the same FastAPI app (export mode:
relative links, no HTMX round-trips, all reviews/steps inline in <details>). The timeline and today PNGs are taken with
playwright using an installed Chrome/Chromium (PLAYWRIGHT_CHROME or /usr/bin/google-chrome); skipped if absent.
Pages are rendered against the pinned spec/ (OPTIONS_SPEC_DIR, overridable). Absolute box paths are never written:
records use repo-relative paths, and the export refuses to write a page that still contains one.
Paper only: reads local files, writes HTML/PNG. No network.
"""
from __future__ import annotations

import argparse
import os
import re
import shutil
import sys
import tempfile
import warnings
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
# Snapshots are rendered against the pinned spec copy (spec/, identical to Doodle's release per sync_spec --check),
# so exported pages show repo-relative spec paths such as spec/risk_rules.yaml. Override with OPTIONS_SPEC_DIR.
os.environ.setdefault("OPTIONS_SPEC_DIR", str(ROOT / "spec"))
warnings.filterwarnings("ignore", category=DeprecationWarning)

from optpipe.cli import main as cli_main  # noqa: E402
from optpipe.dashboard.app import PAGES, create_app  # noqa: E402
from optpipe.dashboard.data import OutDir  # noqa: E402

ABS_PATH = re.compile(r"(?<![\w.~-])/(?:workspace|home|tmp|root|cursor|Users|var/folders)/[^\s\"'<]*")
CHROME_CANDIDATES = [os.environ.get("PLAYWRIGHT_CHROME", ""), "/usr/bin/google-chrome", "/usr/bin/chromium",
                     "/usr/bin/chromium-browser"]


def build_fixture_session(tmp: Path) -> Path:
    out = tmp / "out"
    import contextlib, io
    with contextlib.redirect_stdout(io.StringIO()):
        cli_main(["run", "--out", str(out)])
        cli_main(["run", "--out", str(out), "--append", "--as-of", "2026-10-13T09:40:00-04:00"])
        rc = cli_main(["watch", "--session", str(out / "session-2026-10-13.json"), "--out", str(out),
                       "--replay", str(ROOT / "tests" / "fixtures" / "2026-10-13" / "watch_tape.yaml")])
        if rc:
            raise SystemExit(f"optpipe watch failed (rc {rc})")
    return out


def export_html(out_dir: Path, dest: Path) -> dict[str, Path]:
    from fastapi.testclient import TestClient
    od = OutDir(out_dir)
    dates = od.dates()
    if not dates:
        raise SystemExit(f"no sessions in {out_dir}")
    c = TestClient(create_app(out_dir, export=True))
    dest.mkdir(parents=True, exist_ok=True)
    written = {}
    for page in PAGES:
        url = "/history" if page == "history" else f"/s/{dates[0]}/{page}"
        r = c.get(url)
        r.raise_for_status()
        html = r.text.replace(str(out_dir), "out")          # don't leak the temp path
        leak = ABS_PATH.search(html)
        if leak:
            raise SystemExit(f"{page}.html would contain an absolute path: {leak.group(0)!r}")
        p = dest / f"{page}.html"
        p.write_text(html)
        written[page] = p
    return written


def screenshot(html: Path, png: Path) -> bool:
    exe = next((p for p in CHROME_CANDIDATES if p and Path(p).exists()), None)
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("playwright not installed; skipping PNG (pip install -e .[docs])")
        return False
    with sync_playwright() as pw:
        b = pw.chromium.launch(executable_path=exe) if exe else pw.chromium.launch()
        pg = b.new_page(viewport={"width": 1400, "height": 900}, device_scale_factor=1)
        pg.goto(html.resolve().as_uri())
        pg.screenshot(path=str(png), full_page=True)
        b.close()
    return True


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out-dir", help="existing out/ to export (default: fresh fixture session)")
    ap.add_argument("--dest", default=str(ROOT / "docs" / "dashboard"))
    ap.add_argument("--no-png", action="store_true")
    a = ap.parse_args()
    dest = Path(a.dest)
    with tempfile.TemporaryDirectory() as td:
        os.environ.setdefault("OPTPIPE_STATE_DIR", str(Path(td) / "state"))   # keep IV history out of var/
        out = Path(a.out_dir) if a.out_dir else build_fixture_session(Path(td))
        written = export_html(out, dest)
        for p in written.values():
            print(f"wrote {p.relative_to(ROOT) if p.is_relative_to(ROOT) else p}")
        for page in ("timeline", "today"):
            png = dest / f"{page}.png"
            if not a.no_png and screenshot(written[page], png):
                print(f"wrote {png.relative_to(ROOT) if png.is_relative_to(ROOT) else png}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
