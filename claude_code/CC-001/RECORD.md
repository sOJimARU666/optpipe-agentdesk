# CC-001: fixture-workflow reproducibility and integrity check

Work and evidence record, 2026-11-21. Paper only. **closure_claim: none.**
Status words: **Verified** = executed or observed in this session; **Reported** = stated by others, not re-run here;
**Unverified** = not executed; **Blocked** = cannot proceed without access or a decision.
Delivery words: proposed, implemented locally, **under review**, accepted.

## Work record

| Field | Value |
|---|---|
| Task ID | CC-001, a **provisional** ID. No coordinator-recorded task or queue was reachable, so Claude Code chose it. The coordinator should confirm or renumber it. |
| Objective and user benefit | Make the README's 60-second demo a check that anyone can rerun and compare. One command runs `run`, `validate` and `watch --replay` in a TZ x PYTHONHASHSEED matrix and verifies the hash chain with optpipe's own functions. It prints a **fingerprint** of the outputs with only the wall-clock fields masked. The same fingerprint on another machine means the run was reproduced. Before this, the TZ x hashseed matrices covered only the market-data pack path (R7 item 7). The fixture demo had no golden output: `tests/fixtures/2026-10-13/session-2026-10-13.json` is the older v1 input record. |
| Implementation owner | Claude Code (provisional, pending the coordinator's record) |
| Independent verifier and reviewer | Zoodle (tests on the PR head) and Noodle (review). Moodle reports the merge rule as: Zoodle's tests on the head, Noodle's review and green CI, then **Soodle** merges. This is Reported (Moodle's message of 18:05 UTC, relayed by Eugene). AGENTS.md says only Moodle merges, so that line is stale. Moodle offered to ask the room to start both checks. |
| Starting source identity | `7deb5caece83dbbebd40e31b168031d7375f4cfc` (`main`, the merge of PR #37), tree `b00e6b32413b818bdd942d9f1f8324acaefb8b0a`. The GitHub branch listing shows `main` at the same SHA. Working copy: this session's checkout on `claude/optpipe-integration-m3rzed`, created at `main` with a clean tree. Baselines ran in separate detached worktrees at the same SHA. |
| Allowed files and affected interfaces | **Only `claude_code/`** (Eugene's instruction, 2026-11-21): `claude_code/README.md`, `claude_code/conftest.py`, `claude_code/test_folder_rules.py`, `claude_code/CC-001/{check_fixture_repro.py, test_fixture_repro.py, RECORD.md}`. Every other path is byte-identical to `main` `7deb5ca`, including the root `README.md`, `scripts/`, `tests/`, `audit/`, `src/`, `spec/`, `.github/`, `config/` and `pyproject.toml`. The main suite and CI do not collect the folder. The tool only reads or calls these existing interfaces: the `optpipe` console script (`run`, `validate`, `watch`), `records.store.verify_session_ref`, `watcher.runtime.verify_sidecar` / `validate_watch`, `spec_pin.pin_error`, `config.display_path`, and the env vars `OPTPIPE_STATE_DIR`, `OPTPIPE_CACHE_DIR`, `OPTIONS_SPEC_DIR`. The folder tests load the shared scanner from `tests/test_paper_only.py` and the guard from `tests/netguard/`, read-only. No shared contract or spec change is proposed. |
| Dependencies | An installed package (`pip install -e '.[dev]'`), the pinned spec 1.1.8 (manifest `757f04d7...`), and a time zone database (the system one or `tzdata`). Git is optional: without it, the receipt identifies the source by `inputs_sha256`. |
| Acceptance criteria (proposed; Noodle to confirm) | A1: one command, exit 0 PASS / 1 FAIL / 2 cannot run. A2: every cell checks C0-C7 with the existing functions. A3: only the declared wall-clock fields are masked. A wall-clock-only change is not a difference; a content change with consistent hashes is. A4: a JSON receipt with source, spec, environment and per-cell results, and no absolute paths. A5: another machine can compare its run with `--expect`. A6: paper-only, hygiene, full suite and the CI step list all stay green. A7: the repo's `out/` and `var/` are never touched. A8 (added with the folder move): nothing outside `claude_code/` differs from `main`, and the main suite's counts equal `main`'s. |
| Target platforms | Linux: executed. macOS and Windows: designed for, **not run** (see limitations). |
| Candidate | **Code: `ae247a7f749932740da218b5796693bfc1138f9f`**, tree `b5bc7c9082561611298aff17025112ddfa5e3f2a`: 6 files under `claude_code/`, +1045 lines. The PR head is one docs-only commit on top of it that changes only this file; Zoodle tests that head. History: `a799235` (first version, in `scripts/` and `tests/`) -> `d0fe504` (record) -> `ae247a7` (moved into `claude_code/`, logic unchanged). |

## Commands actually run

All runs were in the repo root, using `.venv/bin/python` with `OPTIONS_SPEC_DIR=spec`. The CI step list ran with
`PYTHONPATH=tests/netguard` and `OPTPIPE_NETGUARD=1` from step 2 on, as in `ci.yml`. It deviated from CI in one way:
`OPTPIPE_STATE_DIR` and `OPTPIPE_CACHE_DIR` pointed at temp dirs, so the CLI fixture run did not write to the repo's `var/`.

### At the current candidate `ae247a7` (local)

| # | Command | Result | Status |
|---|---|---|---|
| 1 | `scripts/sync_spec.py --check --dest spec` | pinned 1.1.8, exit 0 | Verified |
| 2 | `tests/netguard/ci_check_blocked.py` | 3 of 3 attempts blocked, exit 0 | Verified |
| 3 | `pytest tests/test_paper_only.py` | 94 passed | Verified |
| 4 | `scripts/gen_models.py --check` | up to date, exit 0 | Verified |
| 5 | `scripts/make_spec_patch.py --check` | up to date, exit 0 | Verified |
| 6 | `pytest tests/r7_history_audit.py` | 8 passed | Verified |
| 7 | `pytest -p no:cacheprovider -rs` (main suite) | **1629 passed, 4 skipped**, exit 0, 303 s. Identical to `main`'s baseline: the folder adds nothing to the main suite. | Verified |
| 8 | `pytest` inside `spec/` | 922 passed, exit 0 | Verified |
| 9-10 | `optpipe run --out <tmp>`, then `optpipe validate` on its record | OK as 1.1.8 (INV-01..17); 0 errors | Verified |
| 11 | `scripts/sync_spec.py --check` after the run | still pinned, exit 0 | Verified |
| 12 | `claude_code/CC-001/check_fixture_repro.py --receipt R --keep K` (6 cells) | **PASS**, 6 of 6 cells C0-C7 OK, fingerprint `42d76be8...` (below); receipt source `ae247a7`, `worktree_dirty_paths: []` | Verified |
| 13 | `pytest -p no:cacheprovider claude_code` (the folder's own tests) | **28 passed** (25 CC-001 + 3 folder rules), exit 0 | Verified |
| 14 | 16-mutant run against the moved tests (script sha256 `c9d49ce2...`) | **16 of 16 killed**. The unmodified control passes, and the script's sha256 was checked after each restore. | Verified |
| 15 | `pytest tests/test_paper_only.py tests/test_repo_hygiene.py claude_code/test_folder_rules.py` (one session) | 104 passed: the two conftests coexist | Verified |
| 16 | `git diff --stat 7deb5ca..ae247a7 -- . ':(exclude)claude_code'` | empty | Verified |

The 4 skips are 3 opt-in `live_spec` tests plus `tests/test_marketdata_r7.py:231` ("running as root: root-owned files are accepted").
The container runs as root; hosted CI does not.

### Hosted CI on `d0fe504` (before the folder move), observed on GitHub

| Check | Conclusion | Time (UTC) |
|---|---|---|
| `ci` / `test`, push run #162 | success | 18:02:48-18:17:06 |
| `ci` / `test`, pull_request run #163 (PR #38) | success | 18:03:27-18:17:48 |
| CodeQL; `Analyze (python)`; `Analyze (actions)` | success | 18:03:28-18:04:15 |
| `github-advanced-security` (code-scanning AI findings on PR #38) | success | 18:03:38-18:05:04 |

The new head re-runs these. They are not recorded here, because this file cannot name its own commit.

### Earlier, at `7deb5ca` and `a799235` (kept for the record)

| What | Source | Result | Status |
|---|---|---|---|
| Main suite in a detached worktree (baseline) | `7deb5ca` | 1629 passed, 4 skipped, exit 0 | Verified |
| CI step list, all 12 steps | `a799235` | all exit 0. The main suite was 1654 passed, 4 skipped (+25: the module was then in `tests/`). Spec suite 922. | Verified |
| 6-cell matrix | `a799235`, `d0fe504` | PASS, fingerprint below | Verified |
| 2 cells under the network guard | pre-commit | PASS, same fingerprint. The guard log has only the self-check's 3 blocks. The console-script shebang has no `-I`/`-E`/`-S`, and the guard loads in children. | Verified |
| 16-mutant run against the tests in `tests/` | pre-commit | 16 of 16 killed | Verified |
| `git am` of the two patches onto `7deb5ca` | `a799235`, `d0fe504` | rebuilt tree `169ad609`, equal to `d0fe504`'s | Verified |

## Environment and runtime

Linux x86_64 cloud container (kernel 6.18.44, glibc 2.39), running as root. CPython 3.13.18, pytest 9.2.0, pydantic 2.14.4,
PyYAML 6.0.4, jsonschema 4.26.0, lxml 6.1.3, fastapi 0.165.0. The `tzdata` package is not installed (system zoneinfo).
Locale encoding UTF-8 (UTF-8 mode on). All runs were native OS processes. Nothing ran on a browser, emulator, simulator,
Windows, macOS or physical device. Hosted CI is GitHub's `ubuntu-latest` with Python 3.13.

## Results and raw evidence

* **Linux reference fingerprint (spec 1.1.8, fixtures 2026-10-13)**:
  `42d76be8ab8bd78e814956dcdd867ddcdf3d72ed846965406cead6874a96557e`. Every cell of every matrix run gave it, at `a799235`,
  `d0fe504` and `ae247a7`. Per-file masked digests: record `8ab475e0...`, trace `c8c88ce5...`, markdown `91728c1d...`, watch `22cdfedd...`.
* **The matrix varied what it claims (C0).** Probe UTC offsets at a fixed instant were 0 / 7200 / -14400, matching what
  zoneinfo expects for UTC / Europe/Berlin / America/New_York. Seeds 0 and 1 gave `hash_randomization` 0 and 1 and different `hash()` values.
* **The raw outputs really differ.** All 12 raw record and trace files of a 6-cell run had distinct sha256 values, because of
  the wall-clock trace timings, yet every masked digest agreed. Each cell masks exactly 1 (record) + 10 (trace: 5 steps x 2)
  + 5 (Markdown lines) + 1 (watch) values.
* **Mutants killed**: over-masking record content; C7 ignoring CR, non-UTF-8 or a missing final newline; C2, C3, C4 or C6
  forced true; C5 skipping `validate_watch`; the trace duration or Markdown timing left unmasked; C0 ignoring the TZ or the
  seed; R1 ignoring disagreement between cells; `--expect` ignored; fail-open on a missing output.

## Known failures and limitations

* **No Windows or macOS run** (Blocked: no such host here). On Windows the TZ axis is not exercised (no `time.tzset`), and the receipt
  says so. The expected Windows result is in BP-1/BP-2; the tests only *simulate* the bytes.
* **Hosted CI does not run the folder's tests.** That is by design (the folder is kept apart), so these tests run only when invoked
  (`pytest claude_code`). Zoodle's reproduction should include that command.
* `claude_code/test_folder_rules.py` applies the shared paper-only scanner to every other `.py` in the folder, test files
  included, but not to itself: it must name the rules, and `tests/test_paper_only.py` is not scanned by itself either.
* A fingerprint mismatch on another machine points to the per-file digests in both receipts, not to the field that differs.
  Use `--keep` and diff the outputs to find it.
* R1 masks the hashes over wall-clock data and relies on C2-C6 to verify them in each cell. A broken chain is reported by
  C2-C6, not by R1. The tampered-copy tests cover each case.
* Attempts that failed or needed a correction, kept visible:
  * The first baseline attempt used a doubled `-q` (no summary line, no exit code); a worktree baseline replaced it.
  * Mutant M11 at first did not apply (a wrong search anchor). After the fix it was killed, in both locations.
  * One of my test assertions about the C5 message was wrong. The test was fixed; the check was not changed.
  * README was edited for a few seconds during an evidence run, then set aside. Only the hygiene scan reads README.
  * Two exploratory demo runs wrote `var/state/fixtures/iv_history/` in the repo (git-ignored). That was removed.
  * Two pushes failed with HTTP 403 (no GitHub App access). The third push, after Eugene fixed access, succeeded.
  * After the move, the shared scanner flagged `pol.ORDER_NAME` in `test_folder_rules.py` (it matches the order-name rule).
    The harness file was then excluded from its own scan, as above.
  * The step script for the `ae247a7` run printed the *previous* run's last log lines next to the new exit codes (a `sed` that
    missed one occurrence). The exit codes were right, and the results above were read from the new logs.
  * A scheduled check-in that named the old file location was cancelled before it ran.
  * This file was rewritten at 18:25:19 UTC, during the last seconds of step 12 and before step 13. The receipt had
    captured a clean tree at 18:24:41, and neither step reads this file. Both were re-run on the clean PR head.

## Delivery and evidence status

| | |
|---|---|
| Delivery status | **Under review.** Pushed to `origin/claude/optpipe-integration-m3rzed` with Eugene's authorization. PR #38 was opened by the `sOJimARU666` account at 18:03:23 UTC, not by Claude Code: base `main` `7deb5ca`, not a draft. **Not accepted, not merged.** |
| Evidence status | Verified on Linux in this container, and hosted CI green on `d0fe504`. **Unverified** on the new head's hosted CI, on other platforms, and by any other agent. Waiting on: Zoodle's tests on the PR head, Noodle's review, the coordinator's record of CC-001. |
| Artifact location and recipient access | GitHub: branch `claude/optpipe-integration-m3rzed` and PR #38. The receipts, mutation logs and CI-step summaries stay in this session's container (`out/CC-001/`, git-ignored) and are not needed to reproduce: every result above can be rerun from the branch. |
| Next action | Zoodle, on the PR head: `python claude_code/CC-001/check_fixture_repro.py --expect 42d76be8ab8bd78e814956dcdd867ddcdf3d72ed846965406cead6874a96557e --receipt r.json` and `python -m pytest claude_code`, then returns `r.json`. Noodle reviews A1-A8. Then Soodle merges, per Moodle's rule. |

## Starting-point findings (inspected 2026-11-21)

* **Not found**: Lesson 001 and its CLI and integrity functions, the Role Charter, the task queue, and any Dot or Lesson 001
  portability work. I searched this repo's tracked files and the contents of all 38 remote branches, all commit messages,
  the issues (none), the titles of PRs #23-#37 and the bodies of #36 and #37, and the list of 7 repositories this
  session can see. The preferred candidate could not be identified (**Blocked**), so CC-001 is the next useful unclaimed
  task. Whether it overlaps Dot's portability lane in purpose cannot be checked from here: the coordinator should confirm.
* **Kept separate**: PR #36 (`sol/phase1-reliability`, draft) and the deferred P07, and the RD016 `/api/current` fix, which Moodle
  confirms PR #38 is not (Reported).
* **Owners seen in inspected evidence**: Poodle wrote the code for PR #37. Its merge message records "Moodle bounded PASS, Zoodle
  retest PASS, Noodle independent PASS WITH CONDITIONS" and Eugene's approval. Doodle owns `spec/`.
* **Discrepancies noted, not changed** (owner decisions):
  * AGENTS.md says spec 1.1.2, "764 passed", and that only Moodle merges. Inspected: spec 1.1.8 and 1629 passed; Moodle reports that Soodle merges.
  * The README demo and CI sections still say "1.1.2".
  * AGENTS.md says the repo is private; the GitHub repository listing reports it as **public**.

## Backlog proposals (not implemented; for the shared backlog)

| ID | Problem | Hypothesis | Expected benefit | Smallest useful experiment | Failure criteria | Dependencies | Effect on existing behavior |
|---|---|---|---|---|---|---|---|
| BP-1 | `PYTHONWARNDEFAULTENCODING=1` on the demo (run, validate, watch) shows **26 file reads/writes with no explicit encoding**: 24 in `src/optpipe` (including `records/store.py:98-110`, `watcher/runtime.py:847,853`) and 2 in `spec/` (Doodle's). The Markdown title contains U+00B7. | On a Windows cp1252 host the Markdown is written as cp1252 with CRLF line endings, so CC-001 C7 fails and the outputs differ from Linux. | One set of output bytes on every OS. | Run `check_fixture_repro.py` on a native Windows host, unchanged, and compare with the Linux fingerprint. | Windows PASSes with the Linux fingerprint, so no change is needed. | A Windows host; BP-2 first. Owners: Poodle (src), Doodle (spec), and Dot if this is the portability lane. | None until a fix is reviewed. A fix would change bytes only on non-UTF-8 hosts. |
| BP-2 | `tzdata` is not a declared dependency. With no time zone database (simulated here with `PYTHONTZPATH` set to an empty dir, **not a Windows run**), `optpipe run` exits **1** with a `ZoneInfoNotFoundError` traceback. | A fresh Windows install fails the same way, and its exit code looks like a rule failure. | Installs that work on Windows; crashes that read as crashes (exit 2). | Same Windows run as BP-1; or add `tzdata; sys_platform == "win32"` in a scratch venv. | Windows runs without `tzdata`. | `pyproject.toml` (a shared contract): Moodle/Poodle. | Adds a dependency on Windows only. |
| BP-3 | AGENTS.md and README state stale facts (above). | **Duplicate**: Moodle already lists "CHANGELOG/AGENTS.md wording" as an open Slice 1 follow-up (Reported). Fold these facts into that item instead of opening a new one. | Correct starting assumptions in each session. | Add the facts above to Moodle's item. | - | Moodle (AGENTS.md); Eugene (visibility). | Docs only. |
| BP-4 | Output-changing PRs have no compact before/after evidence for the demo outputs. | Quoting the CC-001 fingerprint (and the per-file digests) before and after lets a reviewer see which outputs changed and reproduce that with `--expect`. | Cheaper independent reproduction for Zoodle and Noodle. | Use it on the next PR that touches the planner or records, and compare the review effort. | Reviewers find it noise. | CC-001 accepted. | None (evidence practice only). |
