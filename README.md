# optpipe: paper-only options research pipeline

**PAPER ONLY.** There is no broker integration and no order path. Every ticket is a *HYPOTHETICAL* paper
idea, every risk review sets `approval_required: true`, and `session.mode` is always `"paper"`.
`tests/test_paper_only.py` enforces this from the source code. It fails if the package imports a broker SDK, defines or calls
anything that looks like `place/submit/send/route/execute/cancel/modify_order`, makes a non-GET HTTP call, or
adds a broker/execution/orders package. It also fails if the dashboard gains a non-GET route, a `<form>` or an `hx-post`, and it fails if settings accept any `mode` other than `paper_only`, or if the
Watcher emits an active rule or a `force_close_paper` action.

This is a three-agent project for Eugene Ward:

| Agent | Role |
|---|---|
| **Moodle** | Architecture: stages, contracts, orchestrator and trace, config shape |
| **Doodle** | Finance logic and the **spec** (`/workspace/trading/spec/`, locked, the source of truth) |
| **Poodle** | Code: this repo |

## The spec is loaded, never copied

`spec_dir` in `config/settings.yaml` (or env `OPTIONS_SPEC_DIR`) points to Doodle's folder. Files are read
in place by `optpipe/spec.py`: `session.schema.json`, `risk_rules.yaml`, `risk_reference.py`,
`validate_session.py` and `session.example.json`. Nothing in this repo edits them.
`test_spec_is_not_vendored` fails if a copy appears here.
If the folder is missing, the Risk Officer raises `SpecMissing`. There is no silent fallback ruleset.

* `models/schema_v1.py` is **generated** from `session.schema.json` (`python scripts/gen_models.py`). The
  test `test_models_generated_from_current_schema` fails when the schema SHA-256 changes.
* The Risk Officer reads the rules, thresholds and config from `risk_rules.yaml`. Overrides go in
  `settings.yaml: risk_overrides` and are recorded in each review's `config_overrides`.

## Pipeline

```
Discovery -> Research -> Planner -> RiskOfficer -> Watcher      (one run; a session has 1..n runs)
     \______________ orchestrator.py: typed pydantic contracts + hand-off trace ______________/
```

Output per session date, under `out/` by default (`out/` is gitignored):

| File | Contents |
|---|---|
| `session-YYYY-MM-DD.json` | **Schema v1** record (the contract). Validated with pydantic **and** Doodle's `validate_session.py` before it is written. |
| `session-YYYY-MM-DD.v1_1.json` | Same session in the **v1.1 proposal** shape (per-run scans/snapshots, `closed` phase, raw tick values, `trace_ref`). Validated against `spec_patches/v1.1/session.schema.json`. |
| `session-YYYY-MM-DD.trace.json` (+ `.sha256`) | Hand-off trace for each run and stage: agent, owner, timings, input/output summaries, verdict and error. `session_ref` holds the SHA-256 of the v1 file. |
| `session-YYYY-MM-DD.md` | Human-readable rendering |

The CLI prints both SHA-256s. In the v1.1 preview, `trace_ref` = `{path, sha256}` of the trace file. The v1 file never
carries `trace_ref` (Moodle: it is a v1.1 proposal only).

How values are handled:

* **Sourced values.** Every sourced value is `{value, source_id, as_of, status}`.
* **Conflicts.** A conflicting value is `value: null` plus `conflicts[]`, and rules treat it as UNKNOWN.
* **Times.** Times are `{ny, berlin}` pairs. All market logic (phases, freshness, hours, DTE) uses America/New_York.
* **Verdicts.** The overall verdict is VETO if any rule vetoes or any *hard* rule is UNKNOWN.
* **Point in time.** A source fetched after a run's time is invisible to that run.

## Dashboard (Milestone 2)

A read-only FastAPI + Jinja + HTMX app over `out/`. It uses **GET routes only**, binds to **127.0.0.1**, and makes no network calls.

```bash
.venv/bin/optpipe serve --out out            # http://127.0.0.1:8765/ ; any non-loopback --host is refused
```

| Page | Shows |
|---|---|
| **Today** `/s/<date>/today?run=` | Run selector, the per-run global snapshot and premarket scan, candidates (quote, derived IV30, catalysts), the event calendar and coverage. Hovering a value shows its status, source, as_of and method. |
| **Tickets** `/s/<date>/tickets` | Each HYPOTHETICAL ticket with legs, the limit (raw → rounded tick), max loss/profit, breakevens, hold window and rules. The **Risk Officer** verdict comes with a rule-by-rule table (severity, result, reason, measured, threshold, per-leg). Earlier runs' reviews load through HTMX. |
| **Agent timeline** `/s/<date>/timeline` | From the trace, per run: the agent hand-off flow (Discovery → Research → Planner → RiskOfficer → Watcher) and a Gantt chart of the wall-clock steps. Clicking a bar shows that agent's inputs and outputs (HTMX). Also shown: trace SHA-256 checks (sidecar, v1.1 `trace_ref`, `session_ref`), validation status and run notes. |
| **History** `/history` | All sessions (runs, verdict counts, recommendation, integrity, raw files) plus the aggregated **idea log**. |

Static snapshots (rendered by the same app in export mode, with relative links and no HTMX) live in `docs/dashboard/`:
`today.html`, `tickets.html`, `timeline.html`, `history.html` and `timeline.png`. To regenerate them:

```bash
.venv/bin/pip install -e '.[dev,docs]'
.venv/bin/python scripts/export_dashboard.py      # fresh offline fixture session -> docs/dashboard/ ; PNG via playwright
```

The PNG uses an installed Chrome (`PLAYWRIGHT_CHROME`, or `/usr/bin/google-chrome`), so no browser download is needed.

## Sources: macro calendars, risk-free rate, IV

| Source | URL | Used for |
|---|---|---|
| Federal Reserve FOMC calendar | federalreserve.gov/monetarypolicy/fomccalendars.htm | FOMC events + coverage (R08) |
| BLS release schedules | bls.gov/schedule/news_release/{empsit,cpi,ppi}.htm | NFP / CPI / PPI events + coverage (R08) |
| U.S. Treasury Daily Treasury Bill Rates (CSV) | home.treasury.gov … `daily_treasury_bill_rates` | Risk-free rate r for Black-Scholes (13-week coupon equivalent, last business day **strictly before** the run, r = ln(1+y·91/365)/(91/365)) |

* **Read only and cached.** Fetching is GET only through `HttpFetcher`. `CachedFetcher` keeps responses in `var/cache` (TTL 12 h) and falls back to a stale copy on errors. Tests are offline (`tests/fixtures/sources/`).
* **BLS blocks automated clients (HTTP 403 "Access Denied").** When BLS is unavailable, only FOMC is used, the run notes say so, and NFP/CPI/PPI coverage stays missing. R08 is then UNKNOWN, never a false PASS.
* **Fixture replay is HAND-MADE.** `tests/fixtures/2026-10-13/macro_calendars.yaml` is a hand-made replay snapshot built from real 2026-10-29 captures. It is not an actual 2026-10-13 fetch, and every source says so.
* **Coverage.** `checked_through` = min(last date the calendar lists, as_of + 120 d).
* **IV (`method: derived`).** Each contract's IV is a Black-Scholes inversion of the chain mid (European, no dividends, with American early exercise ignored and noted). IV30 is the ATM IV per expiry (the call/put mean, linear in strike at spot), interpolated or flat-extrapolated to 30 days. It is stored as one daily point per underlying in `var/state/iv_history/<SYM>.jsonl`. R10 stays a soft UNKNOWN until there are 20 points.

## Schema v1.1 proposal (Doodle's ruling 5)

The proposal lives in `spec_patches/v1.1/`. It is a **copy** of the spec schema and example with the changes listed in
`spec_patches/v1.1/CHANGES.md`: `open_interest` → integer, the `closed` phase, per-run `premarket_scans`/`global_snapshots`,
raw/tick/rounding fields and `trace_ref`. `scripts/make_spec_patch.py` regenerates it from the locked spec; `--check` fails on drift.
Both schemas pass. The spec folder itself is untouched.

## CI (local only)

`.github/workflows/ci.yml` runs pytest, Doodle's spec tests, `gen_models.py --check`, `make_spec_patch.py --check` and the
paper-only scan. It is **not pushed anywhere** (no remote). It needs the spec at `OPTIONS_SPEC_DIR`. The spec is not public, so the
workflow expects it to be provided (a checkout step is left as a TODO for Eugene).

## Modules

| Module | What | Design owner | Code |
|---|---|---|---|
| `agent.py` | Generic `Agent[In, Out]` stage base (typed in/out, summaries for the trace) | Moodle | Poodle |
| `orchestrator.py` | Runs the 5 stages, assembles/merges the session record (multi-run), validates v1 + v1.1, writes the trace | Moodle | Poodle |
| `cli.py`, `__main__.py` | `optpipe run / validate / show / spec-check / serve` | Moodle | Poodle |
| `config.py`, `config/settings.yaml` | Settings: mode (paper_only only), sources, IV, watchlist/sectors/broker/trade-style placeholders, overrides | Moodle (shape) / Eugene (values) | Poodle |
| `spec.py` | Locates and loads Doodle's spec read-only | Moodle | Poodle |
| `models/schema_v1.py`, `models/schema_v1_1.py` | Generated from `session.schema.json` / `spec_patches/v1.1/session.schema.json` | Doodle (schema) | generated |
| `models/contracts.py`, `models/common.py` | Stage I/O contracts, manual inputs, `HYPOTHETICAL_LABEL` | Moodle | Poodle |
| `models/session.py` | Hand-off trace models | Moodle | Poodle |
| `models/legacy.py` | Reader for Doodle's v0 hand-written record | Doodle | Poodle |
| `discovery/` | Finviz screens, manual candidates, global snapshot → candidates | Doodle (criteria) | Poodle |
| `research/agent.py` | Quotes and option chains (Nasdaq), events, macro calendars, coverage, earnings, IV | Doodle | Poodle |
| `research/iv.py` | Black-Scholes price/implied vol, ATM IV, IV30 interpolation, `IvHistoryStore` | Doodle | Poodle |
| `planner/`, `planner/ticks.py` | Defined-risk verticals; tick rounding (penny-pilot list configurable; limit down, stop up, target down) | Doodle | Poodle |
| `risk/engine.py`, `risk/ruleset.py` | **Risk Officer**: a pure function `evaluate(input, ruleset, overrides)` over the spec's rules | Doodle | Poodle |
| `risk/adapter.py`, `risk/review_input.py` | Session record → rule inputs (point-in-time visibility) | Doodle | Poodle |
| `risk/agent.py` | Risk Officer stage wrapper | Moodle | Poodle |
| `watcher/` | **STUB** (Milestone 3): defines inactive watch rules for non-vetoed tickets; no loop, never force-closes | Doodle | Poodle |
| `records/` | Store (JSON/v1.1/MD/trace + SHA-256), v1 ↔ v1.1 compat, idea log, data-quality index, Markdown | Doodle (semantics) | Poodle |
| `providers/finviz.py`, `nasdaq.py` | Parsers + providers (fixtures or experimental live read-only GET) | Doodle | Poodle |
| `providers/macro.py`, `treasury.py` | Fed FOMC / BLS calendar parsers and replay; Treasury bill-rate parser | Doodle | Poodle |
| `providers/fetchers.py`, `cache.py` | `FixtureFetcher` / `HttpFetcher` (GET only, the only network code) / `CachedFetcher` | Moodle | Poodle |
| `providers/global_snapshot.py` | Curated record (fixtures) / unavailable (live) | Doodle | Poodle |
| `providers/eodhd.py` | **STUB** (`NotImplementedError`) | n/a | Poodle |
| `dashboard/` | Read-only FastAPI dashboard (`data.py` loader + integrity checks, `app.py` GET routes, `templates/`, vendored htmx 2.0.4) | Moodle | Poodle |
| `scripts/gen_models.py` | Regenerates the models from the schemas (`--check`) | Moodle | Poodle |
| `scripts/make_spec_patch.py` | Builds `spec_patches/v1.1/` from the locked spec (`--check`) | Doodle (content) | Poodle |
| `scripts/export_dashboard.py` | Static snapshots + timeline PNG → `docs/dashboard/` | Moodle | Poodle |

## Run

```bash
python3 -m venv .venv && .venv/bin/pip install -e '.[dev]'
.venv/bin/optpipe spec-check
.venv/bin/optpipe run --fixtures tests/fixtures/2026-10-13                  # offline replay of 2026-10-13
.venv/bin/optpipe run --fixtures tests/fixtures/2026-10-13 --append --as-of 2026-10-13T09:40:00-04:00
.venv/bin/optpipe validate out/session-2026-10-13.json                     # Doodle's validator, exit 1 on errors
.venv/bin/optpipe serve --out out                                          # dashboard on 127.0.0.1:8765
.venv/bin/pytest                                                           # our tests (offline)
cd /workspace/trading/spec && .venv/bin/python -m pytest -q                # Doodle's tests
```

`--live` swaps in `HttpFetcher` (read-only GETs to Finviz, Nasdaq, Fed, BLS and Treasury). It is **experimental and untested in CI**.
The global snapshot, single-name events and earnings have no live source and remain `missing`.

## Live vs fixtures

| Piece | Status |
|---|---|
| Finviz screens, Nasdaq quote/option chain | Parsers tested on saved responses. Live = experimental GET. |
| FOMC (Fed) | Parser tested on a real capture (fetched 2026-10-29). Live = GET. |
| NFP/CPI/PPI (BLS) | Parser tested on captures. **Live GET is blocked (403)**, so coverage stays missing and R08 is UNKNOWN. The fixture uses a HAND-MADE replay. |
| Risk-free rate (Treasury) | Parser tested on the 2026 CSV. Live = GET. |
| Implied volatility | Derived from chain mids (Black-Scholes). R10 is UNKNOWN until 20 daily points exist. |
| Global snapshot, earnings reports, single-name events | Curated `session_inputs.yaml` / fixtures only |
| EODHD, watcher loop | Stubs (watcher = Milestone 3) |

## Placeholders (pending Eugene)

* **Spec placeholders:** `per_trade_max_loss_usd` 500, `max_underlying_risk_usd` 1000, `max_total_open_risk_usd` 2000 and `max_open_positions` 5.
* **Unverified calendar:** the NYSE 2026 holidays/early closes in the spec are not verified against nyse.com.
* **Settings placeholders:** watchlist, sectors, broker name and trade style.

## Known differences vs Doodle's example (spec mismatches)

1. **Trace location.** The hand-off trace is not part of schema v1, so it is written to a separate `*.trace.json`. It is referenced by SHA-256 from the v1.1 preview only.
2. **Review ids.** They are `rv-<run>-<ticket>`, unique and stable within a session (ruling 2). The example uses `rv-premarket`. The schema allows both.
3. **Planner alternatives and rounding.**
   * The planner builds extra alternative tickets per candidate.
   * Ticks follow ruling 3. For JPM (penny pilot) the stop is 1.5125 → **1.52** (UP, 0.01) and the target is 6.05 (DOWN, 0.05). The example has 1.50 / 6.00 (hand-rounded).
4. **Per-run scans.** In v1, `premarket_scan` and `global_snapshot` are single sections (the first run's). The v1.1 preview has them per run (ruling 4).
5. **Open interest** is an integer (ruling 5). The v1 example has floats, and both validate.
6. **Missing count.** `data_quality.missing` is large because per-contract greeks are missing.
7. **Run phases (ruling 6).** These are premarket 04:00–08:30, preopen < 09:30, open < 09:45, then intraday until the close (16:00, or 13:00 on early closes). Anything else, including weekends and NYSE holidays, is `closed`. v1 has no `closed` phase, so the v1 file writes `afterhours` and its summary says "closed".
8. **R08 differs from the example.** With real Fed/BLS calendars, R08 can PASS or FLAG. Doodle's example has UNKNOWN (no coverage). The R08/R09 window is clipped at the earliest leg expiry.
