# REL-004 — Ruff baseline (2026-11-16)

Status: **EXECUTED, report only.** Findings are **not** dispositioned, nothing was auto-fixed, and no lint policy or CI gate is proposed or changed here. This baseline does not close R6, R7, MD-18 or SEC-GATE-001, and it is not independently accepted until Moodle (runtime) and Noodle (static) review it.

## Identity and environment

| Item | Value |
|---|---|
| Source | merge commit `ed8151f2771a874f474521600267ff3eb2de073c` (tree `bfe89f41e899d2584f8a3e6b3114edc464ecdad0`) = `sol/phase1-reliability` + main `871b1b620dc4564a30d81723bb07a362be8156f4` |
| Tool | Ruff 0.17.5, installed from PyPI into the existing isolated test venv only (see REL-002 execution notes) |
| Config | The repo has **no** Ruff configuration (`pyproject.toml` has no `[tool.ruff]`, no `ruff.toml`). Runs used `--isolated` so no outside config could apply. |
| Scope | `src/` and `tests/` only. `spec/` (pinned copy of Doodle's spec) and `scripts/` are out of scope. |
| Flags | `--no-cache` (nothing written into the repo); **no** `--fix` and no `--unsafe-fixes`. |

## Runs

| Run | Command | Exit | Findings | Files with findings |
|---|---|---|---|---|
| A: Ruff 0.17.5 default rule set | `ruff check src tests --no-cache --isolated` | 1 | **576** (`src/` 409 in 49 files, `tests/` 167 in 52 files) | 101 |
| B: REL-001 initial scope `E4,E7,E9,F` | `ruff check src tests --no-cache --isolated --select E4,E7,E9,F` | 1 | **192** (`src/` 133 in 21 files, `tests/` 59 in 21 files) | 42 |

Exit code 1 means "violations found", not a crash. Run A reports Ruff's own count of 490 auto-fixable (38 more with unsafe fixes); none were applied. Run B reports 27 fixable (5 more unsafe). **No E9 (syntax/IO error) findings** appeared in either run, so every file parsed.

Run A uses whatever Ruff 0.17.5 enables by default; that rule set is much wider than the E4/E7/E9/F scope that REL-001 named, which is why Run B is kept separately. Run A is dominated by `UP045` (use `X | None`, 296 findings) and `I001` (import sorting): style modernisation, not defects.

## Counts by rule

### Run B (`E4,E7,E9,F`, the REL-001 scope)

| Rule | Total | `src/` | `tests/` |
|---|---|---|---|
| `E702` | 79 | 44 | 35 |
| `E741` | 57 | 50 | 7 |
| `F401` | 25 | 10 | 15 |
| `E402` | 24 | 24 | 0 |
| `E731` | 5 | 5 | 0 |
| `E401` | 2 | 0 | 2 |

### Run A (Ruff 0.17.5 defaults)

| Rule | Total | `src/` | `tests/` |
|---|---|---|---|
| `UP045` | 296 | 296 | 0 |
| `I001` | 84 | 28 | 56 |
| `RUF100` | 34 | 10 | 24 |
| `F401` | 25 | 10 | 15 |
| `PLW1510` | 23 | 0 | 23 |
| `ISC004` | 17 | 8 | 9 |
| `UP017` | 14 | 9 | 5 |
| `FURB167` | 12 | 3 | 9 |
| `BLE001` | 11 | 6 | 5 |
| `UP037` | 8 | 8 | 0 |
| `UP035` | 7 | 7 | 0 |
| `RUF059` | 6 | 1 | 5 |
| `B023` | 5 | 5 | 0 |
| `FURB162` | 4 | 4 | 0 |
| `PIE810` | 3 | 3 | 0 |
| `B017` | 3 | 0 | 3 |
| `C408` | 3 | 0 | 3 |
| `RUF007` | 2 | 2 | 0 |
| `RUF022` | 2 | 1 | 1 |
| `RUF046` | 2 | 2 | 0 |
| `RET501` | 2 | 0 | 2 |
| `S110` | 2 | 0 | 2 |
| `SIM117` | 2 | 0 | 2 |
| `S112` | 1 | 1 | 0 |
| `SIM102` | 1 | 1 | 0 |
| `UP033` | 1 | 1 | 0 |
| `UP006` | 1 | 1 | 0 |
| `PIE808` | 1 | 1 | 0 |
| `FURB188` | 1 | 1 | 0 |
| `PLR0402` | 1 | 0 | 1 |
| `B018` | 1 | 0 | 1 |
| `UP031` | 1 | 0 | 1 |

## Reading this

* Rules that can indicate a real defect rather than style and deserve first review (not triaged yet): `F401` unused import (25), `B023` function definition in loop that captures the loop variable (5, all `src/`), `B017` `assert_raises(Exception)` (3), `PLW1510` `subprocess.run` without explicit `check=` (23), `BLE001` blind `except Exception` (11, not triaged; handlers already marked `# noqa: BLE001` are not counted), `S110`/`S112` try-except-pass/continue (3), `E402` import not at top (24).
* `RUF100` (34) flags `# noqa` directives that suppress nothing under this rule set (the rule is not enabled or not triggered): a side effect of linting a repository that has no Ruff configuration of its own, not a code problem.
* Nothing here is a claim about correctness or security. No finding was dispositioned, suppressed or fixed.

## Not done / limits

* No Ruff configuration was proposed or added; choosing a rule set and whether to enforce it in CI is a separate policy decision for the owner.
* `ruff format` was not run. Formatting was not assessed.
* The machine-readable reports are in the evidence bundle (`ruff-default-isolated.json`, `ruff-select-E4E7E9F.json` and the `*-statistics.txt` files), kept outside the repository because they contain file paths and line numbers that would go stale on the next commit.
