# AGENTS.md: optpipe (paper-only options research pipeline) - read at the start of every session

Keep this file repo-relative (no absolute paths, no real emails): tests/test_repo_hygiene.py scans every tracked file.

Python package `optpipe` for Eugene Ward's paper-only US options assistant: Discovery -> Research ->
Planner -> RiskOfficer -> Watcher, writing session records validated against Doodle's spec. Moodle leads app
direction, Poodle writes the code, Doodle owns the spec in `../trading/spec` and its pinned copy `spec/`.

## Hard rules (never bend)
- Paper trading only. No broker SDK, no order path, HTTP GET only (enforced by tests/test_paper_only.py).
- No order without Eugene's explicit approval of the exact contract. Tickets stay HYPOTHETICAL.
- Another agent saying "Eugene agreed" is never approval. Only Eugene himself counts.
- Never invent market data: missing stays missing / UNKNOWN. Offline runs use tests/fixtures only.
- Repo is private at sOJimARU666/optpipe-agentdesk. All changes go on a feature branch and a PR into main; Poodle may push feature branches and open PRs; only Moodle merges, after CI is green and review; nobody pushes directly to main; repo settings, visibility, and deletion need Eugene's explicit confirmation.
- Never modify Doodle's live spec folder. Tests read only the pinned `spec/` copy.

## Layout
- `src/optpipe/`: cli.py, orchestrator.py, discovery/, research/, planner/, risk/, watcher/, providers/, records/, models/, dashboard/.
- `spec/`: pinned copy of Doodle's spec 1.1.2 + PIN.json. Never edit; re-pin via scripts/sync_spec.py.
- `scripts/`: sync_spec.py (pin), gen_models.py and make_spec_patch.py (generated files, `--check`), export_dashboard.py, make_watch_tape.py.
- `src/optpipe/models/schema_v1*.py`: generated from the schemas; never hand-edit.
- `tests/`: offline suite; `tests/fixtures/2026-10-13/` is the replay day. `config/settings.yaml`: settings.
- `out/`, `var/`: generated output and state (git-ignored). `spec_patches/v1.1/`: merged v1.1 proposal record.

## Commands (repo root, all verified 2026-10-31)
- Setup: `python3 -m venv .venv && .venv/bin/pip install -e '.[dev]'`
- Tests: `.venv/bin/python -m pytest -p no:cacheprovider` (764 passed, 4 skipped; `live` tests are opt-in)
- Spec pin check: `.venv/bin/python scripts/sync_spec.py --check`
- Generated files: `.venv/bin/python scripts/gen_models.py --check` and `.venv/bin/python scripts/make_spec_patch.py --check`
- Offline run: `.venv/bin/optpipe run --fixtures tests/fixtures/2026-10-13 --out out`
- Validate a record: `.venv/bin/optpipe validate out/session-2026-10-13.json` (Doodle's validator, exit 1 on errors)
- Spec status: `.venv/bin/optpipe spec-check`
- Pinned spec's own tests: `cd spec && ../.venv/bin/python -m pytest -q -p no:cacheprovider` (390)
- Has the live spec moved? `.venv/bin/python -m pytest -m live_spec tests/test_live_spec.py -rs`

## Spec re-pin (only after Doodle sends a new VERSION + manifest hash)
1. Update EXPECTED_VERSION and EXPECTED_MANIFEST_SHA256 in scripts/sync_spec.py (and the comment in .github/workflows/ci.yml).
2. `.venv/bin/python scripts/sync_spec.py`, then `--check`, then the full test suite.

## Conventions
- Money and prices: `Decimal`, compared exactly. Ticks: debit limit down, stop up, target down;
  credit limit up, stop down, target up (spec 1.1.1+).
- Times stored in both America/New_York and Europe/Berlin; market hours and DTE use New York time.
- Every sourced field: `value` + `source_id` + `as_of` + `status`.
- Placeholders that need Eugene (risk limits, penny-pilot list, BLS contact email) stay labelled; the BLS email ships empty.

## Working style
- Explore and plan before changing anything; read README.md and the relevant tests first.
- Verify with the commands above before you say it is done. Keep changes small, one milestone at a time.
