# REL-002 — Hypothesis property execution notes (2026-11-16)

Status: **EXECUTED offline; 0 property failures.** Not independently accepted until Moodle (runtime) and Noodle (static) review it. This does not close R6, R7, MD-18 or SEC-GATE-001, and no production code was changed.

## What this is, and what it is not

* `audit/REL-002-test-gap-matrix.md` (Sol, 2026-11-06) is prose: P01-P07 were **proposals**. The branch held only two example-based tests (`tests/test_rel002_json_key_order.py`, `tests/test_rel002_clock_monotonicity.py`); neither uses Hypothesis. So real Hypothesis execution had not been established before this run.
* `tests/test_rel002_hypothesis_properties.py` is **new work written on 2026-11-16** from the documented P01-P06 proposals, under a **new SHA**. It is **not** the on-hold REL-002 v2 patch (sha256 `8a50f3577925a8de2570f115f5f273103a24f7fad981d7582059a91489d21dfe`); that patch was not available and was not reconstructed. The two earlier example tests are unchanged and still run.
* P07 (resumed decision must revalidate freshness) stays **deferred** as the matrix says: it needs stateful consumer-path tests reconciled with the merged PR #23 code, which is outside this bounded set.

## Environment (isolated; nothing installed anywhere else)

| Item | Value |
|---|---|
| Venv | the existing isolated test venv created for Research Desk 014 review (Python 3.13.5, pip 25.1.1, pytest 9.2.0) |
| New packages | `hypothesis==6.170.15`, `ruff==0.17.5`, and Hypothesis's one new dependency `sortedcontainers==2.4.0` (pip freeze 49 -> 52 lines; a diff of the before/after freezes shows only these three additions) |
| Guard | The earlier install used a constraints file excluding these two packages (`hypothesis<0`, `ruff<0`). It applies only at install time and is not part of the venv, so it was simply not passed. No file was edited. |
| Isolation | tests ran non-root as uid 1000 under `unshare --map-user=1000 --net` (no network interface up), `OPTPIPE_NETGUARD=1`, `PYTHONDONTWRITEBYTECODE=1`, `-p no:cacheprovider`. Hypothesis example database disabled. Network was used only for the `pip install` itself. |
| Dependencies declared in the repo | **unchanged.** `pyproject.toml` still has no Hypothesis. Adding it is a separate reviewed decision. |

## Property set

| Test(s) | Proposal | Valid control | Rejecting case |
|---|---|---|---|
| `test_p01_*` | P01 key order | unmodified valid pack loads, 4 snapshots exposed | the same invalid value (`last_price.value = -1`) is refused with identical error ids under any key permutation; random permutation of all six JSON files + honest rehash keeps errors, index, snapshots, catalysts, summary and exit code identical |
| `test_p02_*` | P02 numeric leaves | `101.25` accepted | oracle derived from the schema, **hard-coded** (not read from `src`): non-finite tokens, overflow, ints beyond +/-2**53 -> `MD-16-NONFINITE`; <= 0, bools, strings, arrays, objects -> `MD-03-SCHEMA`; null with status OK -> `MD-06-UNKNOWN`; finite positive accepted; every refusal fails closed |
| `test_p03_*` | P03 clock monotone | fresh at as_of+1 min; exactly 20 min is not stale | exact oracle (stale iff strictly older than 20 min, future iff negative age) over random clocks in random offsets, stale never revives as the clock advances, and the reverse direction does not hold |
| `test_p04_*` | P04 rejected pack exposes nothing | valid pack exposes index, catalysts, summary, 4 snapshots | six single-fault mutation families over every applicable file (tamper without rehash, drop file, truncate, duplicate key, BOM, bad status): refused, expected error id present, no snapshots/catalysts/summary, no usable contracts, `NO IDEA` |
| `test_p05_*` | P05 equivalent instants | naive clock refused; same text plus an offset accepted | same instant in any UTC offset (-12h..+14h) for the decision clock and for the file `as_of` (including `Z`) gives the same validity and error ids; naive file `as_of` refused at load, and a naive `as_of` in a hand-built result is refused at decision time |
| `test_p06_*` | P06 byte mutations | unmodified pack loads | flip/delete/insert/truncate/duplicate-slice of any of 7 files then honest rehash: never raises, never `MD-00-CRASH`, `ok` iff no errors, accepted only if still exposing all four snapshots, otherwise fails closed with exit code 1 or 2; truncated JSON refused with `MD-03-JSON` |

16 tests. Generation is bounded (`max_examples` 25 per property by default, `deadline=None`, `derandomize=True`). `OPTPIPE_HYP_EXPLORE=1` plus `--hypothesis-seed=N` explores with random seeds; `OPTPIPE_HYP_EXAMPLES` changes the count. If Hypothesis is not installed (as in hosted CI, which installs only `.[dev]`) the module **skips itself**; `OPTPIPE_REQUIRE_HYPOTHESIS=1` turns a missing import into a failure. This means hosted CI does not execute these properties unless the dependency is added later.

## Results

| Run | Command (inside `unshare --map-user=1000 --net`) | Result |
|---|---|---|
| Default, deterministic | `python -m pytest tests/test_rel002_hypothesis_properties.py -p no:cacheprovider` | 16 passed, 0 failed, 0 skipped |
| Exploration x3 | `OPTPIPE_HYP_EXPLORE=1 OPTPIPE_HYP_EXAMPLES=300 ... --hypothesis-seed=<seed>` for seeds `20261116`, `7`, `424242` | 16 passed each (48 test executions), 0 failed, 0 skipped |
| Full offline regression at `d254263` | the CI step list, see below | `pytest` **1611 passed, 3 skipped, 0 failed** (the 3 skips are the opt-in `live_spec` tests); spec suite **911 passed**; paper-only 94 passed; `sync_spec --check`, netguard blocked-connection check, `gen_models --check`, `make_spec_patch --check`, `optpipe run`, `optpipe validate`, final spec-pin check all exit 0; working tree clean after the run |
| Baseline before the new test file (`ed8151f`) | same | `pytest` 1595 passed, 3 skipped; spec suite 911 passed (the +16 are this file) |

There are no Node tests in this repository (the only tracked JavaScript is the vendored `htmx.min.js`), so none were run. Not run: jsdom or a browser, hosted CI at the exact head (see the pull request), the `live_spec` tests, anything networked.

**Property failures: none.** No finding about the loader came out of the properties.

## Negative controls (mutants of `src/optpipe/marketdata.py` in scratch copies; the repo's `src/` was never modified)

Each row is one deliberate bug in a scratch copy of `src/`. The properties must fail against it. Result for the unmodified copy: 16 passed.

| Mutant | Deliberate bug | Result | Failing tests |
|---|---|---|---|
| M0 | none (control) | pass | none |
| M1 | quote staleness disabled | killed | P03 (2) |
| M2 | integer range limit raised from 2**53 to 2**200 | killed | P02 |
| M3 | rejected pack keeps its data | killed | P01 reject, P02 (2), P04, P05 naive file timestamp, P06 (2) |
| M4 | duplicate JSON keys allowed | killed | P04 |
| M5 | naive timestamp treated as UTC at decision time | killed | P05 decision-time naive `as_of` |
| M6 | stale quote revives after 3x the limit | killed | P03 monotone |

Two test defects were found this way and fixed before the final run: (a) an early P02 oracle read `MAX_SAFE_INT` from the code under test, so mutant M2 survived; it is now hard-coded to 2**53. (b) the first set had no test that reached the decision-time timestamp check, so M5 survived; the P05 decision-time test was added. The scripts and per-mutant logs are in the evidence bundle.

## Observations (not property failures, not triaged, no fix attempted)

1. Error text embeds the decision clock as rendered (e.g. `... after the clock 08:59:59-04:00`), so the same instant in another offset yields the same ids but not the same message bytes. The P05 tests therefore compare normalised ids, as the matrix says.
2. The loader accepts an `as_of` offset written without a colon (`...-0400`), which Python 3.13's `datetime.fromisoformat` tolerates. Whether the spec grammar should allow that is a question for the spec owner.
3. A control byte (NUL) appended to `summary.md` with an honest rehash is accepted, while control characters in JSON strings are refused (`MD-16-PATTERN`). `summary.md` appears to be treated as free text; whether that is intended was not established here.
4. The gap matrix's "Known blocker" (CI run `42189869506` failing on `62b645f3`) was not investigated; the offline suite passes at this head.

## Not done

* No `pyproject.toml` or CI change. No production code change. No merge, deployment, live data or broker access.
* P07 is deferred, and the REL-002 v2 patch was not reconstructed.
