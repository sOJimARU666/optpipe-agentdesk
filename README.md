# optpipe: paper-only options research pipeline

**PAPER ONLY.** There is no broker integration and no order path. Every ticket is a *HYPOTHETICAL* paper
idea, every risk review sets `approval_required: true`, and `session.mode` is always `"paper"`.
`tests/test_paper_only.py` enforces this from the source code. It fails if the package imports a broker SDK, defines or calls
anything that looks like `place/submit/send/route/execute/cancel/modify_order`, makes a non-GET HTTP call, or
adds a broker/execution/orders package. It also fails if settings accept any `mode` other than `paper_only`, or if the
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

Output per session date, under `out/` by default:

| File | Contents |
|---|---|
| `session-YYYY-MM-DD.json` | Schema v1 record. It is validated with pydantic **and** Doodle's `validate_session.py` before it is written. |
| `session-YYYY-MM-DD.md` | Human-readable rendering |
| `session-YYYY-MM-DD.trace.json` | Hand-off trace for each run and stage: agent, owner, timings, input/output summaries, verdict and error |

How values are handled:

* **Sourced values.** Every sourced value is `{value, source_id, as_of, status}`.
* **Conflicts.** A conflicting value is `value: null` plus `conflicts[]`, and rules treat it as UNKNOWN.
* **Times.** Times are `{ny, berlin}` pairs. All market logic (phases, freshness, hours, DTE) uses America/New_York.
* **Verdicts.** The overall verdict is VETO if any rule vetoes or any *hard* rule is UNKNOWN.
* **Point in time.** A source fetched after a run's time is invisible to that run.

## Modules

| Module | What | Design owner | Code |
|---|---|---|---|
| `agent.py` | Generic `Agent[In, Out]` stage base (typed in/out, summaries for the trace) | Moodle | Poodle |
| `orchestrator.py` | Runs the 5 stages, assembles/merges the session record (multi-run), validates, writes the trace | Moodle | Poodle |
| `cli.py`, `__main__.py` | `optpipe run / validate / show / spec-check` | Moodle | Poodle |
| `config.py`, `config/settings.yaml` | Settings: mode (paper_only only), watchlist/sectors/broker/trade style placeholders, overrides | Moodle (shape) / Eugene (values) | Poodle |
| `spec.py` | Locates and loads Doodle's spec read-only | Moodle | Poodle |
| `models/schema_v1.py` | Generated from `session.schema.json` | Doodle (schema) | generated |
| `models/contracts.py`, `models/common.py` | Stage I/O contracts, manual inputs, `HYPOTHETICAL_LABEL` | Moodle | Poodle |
| `models/session.py` | Hand-off trace models | Moodle | Poodle |
| `models/legacy.py` | Reader for Doodle's v0 hand-written record | Doodle | Poodle |
| `discovery/` | Finviz screens, manual candidates, global snapshot → candidates | Doodle (criteria) | Poodle |
| `research/` | Quotes and option chains (Nasdaq), events, coverage, earnings | Doodle | Poodle |
| `planner/` | Defined-risk verticals: expiry pick, strikes, limit, size, stop/target, hold window | Doodle | Poodle |
| `risk/engine.py`, `risk/ruleset.py` | **Risk Officer**: a pure function `evaluate(input, ruleset, overrides)` over the spec's rules | Doodle | Poodle |
| `risk/adapter.py`, `risk/review_input.py` | Session record → rule inputs (point-in-time visibility) | Doodle | Poodle |
| `risk/agent.py` | Risk Officer stage wrapper | Moodle | Poodle |
| `watcher/` | **STUB**: defines inactive watch rules for non-vetoed tickets; no loop, never force-closes | Doodle | Poodle |
| `records/` | Store (JSON/MD/trace), idea log transitions, data-quality index, Markdown, sourced-field helpers | Doodle (semantics) | Poodle |
| `providers/finviz.py`, `nasdaq.py` | Parsers + providers (fixtures or experimental live read-only GET) | Doodle | Poodle |
| `providers/fetchers.py` | `FixtureFetcher` (offline) / `HttpFetcher` (GET only, the only network code) | Moodle | Poodle |
| `providers/global_snapshot.py` | Curated record (fixtures) / unavailable (live) | Doodle | Poodle |
| `providers/eodhd.py` | **STUB** (`NotImplementedError`) | n/a | Poodle |
| `dashboard/` | **STUB** (empty) | Moodle | n/a |
| `scripts/gen_models.py` | Regenerates `schema_v1.py` from the spec schema | Moodle | Poodle |

## Run

```bash
python3 -m venv .venv && .venv/bin/pip install -e '.[dev]'
.venv/bin/optpipe spec-check
.venv/bin/optpipe run --fixtures tests/fixtures/2026-10-13                  # offline replay of 2026-10-13
.venv/bin/optpipe run --fixtures tests/fixtures/2026-10-13 --append --as-of 2026-10-13T09:40:00-04:00
.venv/bin/optpipe validate out/session-2026-10-13.json                     # Doodle's validator, exit 1 on errors
.venv/bin/pytest                                                           # our tests (spec-dependent ones skip if the spec is absent)
cd /workspace/trading/spec && .venv/bin/python -m pytest -q                # Doodle's tests
```

`--live` swaps in `HttpFetcher` (read-only GETs to Finviz/Nasdaq). It is **experimental and untested in CI**.
The global snapshot, events, earnings and macro coverage have no live source and remain `missing`.

## Live vs fixtures

| Piece | Status |
|---|---|
| Finviz screens, Nasdaq quote/option chain | Parsers tested on saved responses. Live = experimental GET. |
| Global snapshot, earnings reports, events, coverage | Curated `session_inputs.yaml` / fixtures only |
| FOMC/NFP calendar | **No source.** Coverage stays `missing`, so R08 flags. |
| Implied volatility | Nasdaq chain has none, so R10 flags. |
| EODHD, dashboard, watcher loop | Stubs |

## Placeholders (pending Eugene)

* **Spec placeholders:** `per_trade_max_loss_usd` 500, `max_underlying_risk_usd` 1000, `max_total_open_risk_usd` 2000 and `max_open_positions` 5.
* **Unverified calendar:** the NYSE 2026 holidays/early closes in the spec are not verified against nyse.com.
* **Settings placeholders:** watchlist, sectors, broker name and trade style.

## Known differences vs Doodle's example (spec mismatches)

1. **Trace location.** The hand-off trace is not part of schema v1, so it is written to a separate `*.trace.json`.
2. **Review ids.** They are `rv-<run>-<ticket>`, where the example uses `rv-premarket`. The schema allows both.
3. **Planner alternatives and rounding.**
   * The planner builds extra alternative tickets per candidate.
   * Stop/target for the JPM bull call spread are 1.51 / 6.05 (computed). The example has 1.50 / 6.00 (hand-rounded).
4. **Single-section scans.** `premarket_scan` and `global_snapshot` are single sections, so later runs keep the first run's.
5. **Open-interest numbers.** Measured OI is int here and float in the example. Values are equal.
6. **Missing count.** `data_quality.missing` is large (about 1,170) because per-contract greeks and IV are missing.
7. **Run phase boundaries are Poodle's assumption.**
   * premarket < 08:30 ≤ preopen < 09:30 ≤ open < 09:45 ≤ intraday < close (NY).
   * Non-trading days count as afterhours.
