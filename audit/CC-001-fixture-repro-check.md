# CC-001: fixture-workflow reproducibility and integrity check

Work and evidence record, 2026-11-21. Paper only. **closure_claim: none.** Local only: nothing pushed, no PR.
Status words: **Verified** = executed in this session; **Reported** = stated by others, not re-run here;
**Unverified** = not executed; **Blocked** = cannot proceed without access or a decision.

## Work record

| Field | Value |
|---|---|
| Task ID | CC-001, a **provisional** ID. No coordinator-recorded task or queue was reachable, so Claude Code chose it. The coordinator should confirm or renumber it. |
| Objective and user benefit | Make the README's 60-second demo a check that anyone can rerun and compare. One command runs `run`, `validate` and `watch --replay` in a TZ x PYTHONHASHSEED matrix and verifies the hash chain with optpipe's own functions. It prints a **fingerprint** of the outputs with only the wall-clock fields masked. The same fingerprint on another machine means the run was reproduced. Before this, the TZ x hashseed matrices covered only the market-data pack path (R7 item 7). The fixture demo had no golden output: `tests/fixtures/2026-10-13/session-2026-10-13.json` is the older v1 input record. |
| Implementation owner | Claude Code (provisional, pending the coordinator's record) |
| Independent verifier and reviewer | Proposed: Zoodle reproduces, Noodle reviews. **Not assigned or contacted**: this session has no channel to either. |
| Starting source identity | `7deb5caece83dbbebd40e31b168031d7375f4cfc` (`main`, the merge of PR #37), tree `b00e6b32413b818bdd942d9f1f8324acaefb8b0a`. The GitHub branch listing shows `main` at the same SHA. Working copy: this session's checkout on `claude/optpipe-integration-m3rzed`, created at `main` with a clean tree. The baseline ran in a separate detached worktree at the same SHA. |
| Allowed files and affected interfaces | New: `scripts/check_fixture_repro.py`, `tests/test_fixture_repro.py`, this record. Additive: one README section. **Unchanged**: `src/`, `spec/`, `.github/`, `config/`, `pyproject.toml`, the other scripts. The tool only reads or calls these existing interfaces: the `optpipe` console script (`run`, `validate`, `watch`), `records.store.verify_session_ref`, `watcher.runtime.verify_sidecar` / `validate_watch`, `spec_pin.pin_error`, `config.display_path`, and the env vars `OPTPIPE_STATE_DIR`, `OPTPIPE_CACHE_DIR`, `OPTIONS_SPEC_DIR`. No shared contract or spec change is proposed. |
| Dependencies | An installed package (`pip install -e '.[dev]'`), the pinned spec 1.1.8 (manifest `757f04d7...`), and a time zone database (the system one or `tzdata`). Git is optional: without it, the receipt identifies the source by `inputs_sha256`. |
| Acceptance criteria (proposed; Noodle to confirm) | A1: one command, exit 0 PASS / 1 FAIL / 2 cannot run. A2: every cell checks C0-C7 with the existing functions. A3: only the declared wall-clock fields are masked. A wall-clock-only change is not a difference; a content change with consistent hashes is. A4: a JSON receipt with source, spec, environment and per-cell results, and no absolute paths. A5: another machine can compare its run with `--expect`. A6: paper-only, hygiene, full suite and the CI step list all stay green. A7: the repo's `out/` and `var/` are never touched. |
| Target platforms | Linux: executed. macOS and Windows: designed for, **not run** (see limitations). |
| Candidate | Code commit `a7992350ff4338c5ec57a0e4f959818bceec780a`, tree `5c41afd019199ab1f69ebc4c3d646d07e3bcff25`, 4 files, +801 lines, including the draft of this record. A follow-up docs-only commit fills in this record and adjusts one README sentence; no code changes. |

## Commands actually run

All runs were in the repo root, using `.venv/bin/python` with `OPTIONS_SPEC_DIR=spec`. The CI step list ran with
`PYTHONPATH=tests/netguard` and `OPTPIPE_NETGUARD=1` from step 2 on, as in `ci.yml`. It deviated from CI in one way:
`OPTPIPE_STATE_DIR` and `OPTPIPE_CACHE_DIR` pointed at temp dirs, so the CLI fixture run did not write to the repo's `var/`.

| # | Command | Source | Result | Status |
|---|---|---|---|---|
| 0 | `pytest -p no:cacheprovider` in a detached worktree (baseline) | `7deb5ca` | 1629 passed, 4 skipped, exit 0, 338 s | Verified |
| 1 | `scripts/sync_spec.py --check --dest spec` | `a799235` | pinned 1.1.8, exit 0 | Verified |
| 2 | `tests/netguard/ci_check_blocked.py` | `a799235` | 3 of 3 attempts blocked, exit 0 | Verified |
| 3 | `pytest tests/test_paper_only.py` | `a799235` | 94 passed | Verified |
| 4 | `scripts/gen_models.py --check` | `a799235` | up to date, exit 0 | Verified |
| 5 | `scripts/make_spec_patch.py --check` | `a799235` | up to date, exit 0 | Verified |
| 6 | `pytest tests/r7_history_audit.py` | `a799235` | 8 passed | Verified |
| 7 | `pytest -p no:cacheprovider -rs` | `a799235` | **1654 passed, 4 skipped**, exit 0, 354 s. The +25 over the baseline is exactly the new module. | Verified |
| 8 | `pytest` inside `spec/` | `a799235` | 922 passed | Verified |
| 9-10 | `optpipe run --out <tmp>`, then `optpipe validate` on its record | `a799235` | OK as 1.1.8 (INV-01..17); 0 errors | Verified |
| 11 | `scripts/sync_spec.py --check` after the run | `a799235` | still pinned, exit 0; tree clean | Verified |
| 12 | `scripts/check_fixture_repro.py --receipt R --keep K` (6 cells) | `a799235`, clean | **PASS**, fingerprint below | Verified |
| 13 | Step 12 on 2 cells under the network guard | pre-commit tree | PASS, same fingerprint. The guard log has only step 2's three deliberate blocks. A child started the way the tool starts one does load `optpipe_netguard`, and the console-script shebang carries no `-I`/`-E`/`-S`. | Verified |
| 14 | Mutation run: 16 single-line mutants of the script against `tests/test_fixture_repro.py` | pre-commit script, sha256 `65583407...` | **16 of 16 killed**. The unmodified control passes, and the script's sha256 was checked after each restore. | Verified |

The 4 skips are 3 opt-in `live_spec` tests plus `tests/test_marketdata_r7.py:231` ("running as root: root-owned files are accepted"). The container runs as root; hosted CI does not. PR #37 reports 1630 passed, 3 skipped on CI for the same total (Reported).

## Environment and runtime

Linux x86_64 cloud container (kernel 6.18.44, glibc 2.39), running as root. CPython 3.13.18, pytest 9.2.0, pydantic 2.14.4,
PyYAML 6.0.4, jsonschema 4.26.0, lxml 6.1.3, fastapi 0.165.0. The `tzdata` package is not installed (system zoneinfo).
Locale encoding UTF-8 (UTF-8 mode on). All runs were native OS processes. Nothing ran on a browser, emulator, simulator,
Windows, macOS or physical device.

## Results and raw evidence

* **Linux reference fingerprint (spec 1.1.8, fixtures 2026-10-13)**:
  `42d76be8ab8bd78e814956dcdd867ddcdf3d72ed846965406cead6874a96557e`. It was the same in all 6 cells of step 12, in both cells of step 13, and in an earlier 6-cell run on the uncommitted tree.
  Per-file masked digests: record `8ab475e0...`, trace `c8c88ce5...`, markdown `91728c1d...`, watch `22cdfedd...`.
* **The matrix varied what it claims (C0).** Probe UTC offsets at a fixed instant were 0 / 7200 / -14400, matching what
  zoneinfo expects for UTC / Europe/Berlin / America/New_York. Seeds 0 and 1 gave `hash_randomization` 0 and 1 and different `hash()` values.
* **The raw outputs really differ.** All 12 raw record and trace files of a 6-cell run had distinct sha256 values, because of
  the wall-clock trace timings, yet every masked digest agreed. Each cell masks exactly 1 (record) + 10 (trace: 5 steps x 2)
  + 5 (Markdown lines) + 1 (watch) values. Before the tool existed, two hand runs (host defaults vs `TZ=America/New_York PYTHONHASHSEED=7`)
  differed only in `runs[*].steps[*].started_at` / `duration_ms`, the hashes over them and the Markdown "N ms" lines.
* **Receipt** at `a799235`: sha256 `385800927ff23f64151dee28b3a693286a295ebe9e1040138069df76e8caf132`, with `worktree_dirty_paths: []`,
  `inputs_sha256 f54e2ade...` (91 files) and 0 absolute paths.
* **Mutants killed** (step 14): over-masking record content; C7 ignoring CR, non-UTF-8 or a missing final newline; C2, C3,
  C4 or C6 forced true; C5 skipping `validate_watch`; the trace duration or Markdown timing left unmasked; C0 ignoring the TZ or
  the seed; R1 ignoring disagreement between cells; `--expect` ignored; fail-open on a missing output.

## Known failures and limitations

* **No Windows or macOS run** (Blocked: no such host here). On Windows the TZ axis is not exercised (no `time.tzset`), and the receipt
  says so. The expected Windows result is in BP-1/BP-2; the tests only *simulate* the bytes.
* A fingerprint mismatch on another machine points to the per-file digests in both receipts, not to the field that differs.
  Use `--keep` and diff the outputs to find it.
* R1 masks the hashes over wall-clock data and relies on C2-C6 to verify them in each cell. A broken chain is reported by
  C2-C6, not by R1. The tampered-copy tests cover each case.
* The end-to-end test adds about 25 s to the suite (3 cells).
* Attempts that failed or needed a correction, kept visible:
  * The first baseline attempt used a doubled `-q`, so it printed no summary line and no exit code. Step 0 replaces it.
  * Mutant M11 at first did not apply (a wrong search anchor). After the fix it was killed.
  * One of my test assertions about the C5 message was wrong. The test was fixed; the check was not changed.
  * README was edited for a few seconds during step 7 and then set aside. Only the hygiene scan reads README, and it passes on both versions. It was rerun at the final commit.
  * Two exploratory demo runs, made before the tool existed, wrote `var/state/fixtures/iv_history/` in the repo (git-ignored). That was removed.

## Delivery and evidence status

| | |
|---|---|
| Delivery status | **Implemented locally**: committed on the local branch `claude/optpipe-integration-m3rzed`. **Not pushed, no PR, not under review, not accepted.** |
| Evidence status | Verified on Linux in this container. **Unverified** on other platforms and by any other agent. **Blocked**: independent reproduction (Zoodle) and review (Noodle) need a channel and the coordinator's record. |
| Artifact location and recipient access | The commits exist only in this session's container. The patches, receipt, mutation log and CI-step summary are copied to `out/CC-001/` (git-ignored) in the same container. The container is temporary. **Recipients cannot reach any of this** until Eugene authorizes a push of the branch, or the patch files are moved another way. |
| Next action | Decision for Eugene and the coordinator: record CC-001 and decide whether this branch may be pushed for review. Then Zoodle runs `python scripts/check_fixture_repro.py --expect 42d76be8ab8bd78e814956dcdd867ddcdf3d72ed846965406cead6874a96557e --receipt r.json` on another machine and returns `r.json`, and Noodle reviews A1-A7. |

## Starting-point findings (inspected 2026-11-21)

* **Not found**: Lesson 001 and its CLI and integrity functions, the Role Charter, the task queue, and any Dot or Lesson 001
  portability work. I searched this repo's tracked files and the contents of all 38 remote branches, all commit messages,
  the issues (none), the titles of PRs #23-#37 and the bodies of #36 and #37, and the list of 7 repositories this
  session can see. The preferred candidate could not be identified (**Blocked**), so CC-001 is the next useful
  unclaimed task. It shares no files with any visible work. Whether it overlaps Dot's portability lane in purpose
  cannot be checked from here: the coordinator should confirm.
* **Kept separate**: PR #36 (`sol/phase1-reliability`, draft, do not merge; `audit/REL-00x` files and `tests/test_rel002_*.py`)
  and the deferred P07. Nothing of theirs was read into or changed by CC-001.
* **Owners seen in inspected evidence**: Poodle wrote the code for PR #37 ("Agent: Poodle"). Its merge message records "Moodle bounded PASS,
  Zoodle retest PASS, Noodle independent PASS WITH CONDITIONS" and Eugene's approval. Doodle owns `spec/`. Sol/Soodle relays
  the audit documents.
* **Discrepancies noted, not changed** (owner decisions):
  * AGENTS.md says spec 1.1.2, "764 passed" and "verified 2026-10-31". Inspected: spec 1.1.8, 1629 passed at `7deb5ca`.
  * The README demo and CI sections still say "1.1.2", and that CI is "not pushed anywhere (no remote)".
  * AGENTS.md says the repo is private; the GitHub repository listing reports it as **public**.

## Backlog proposals (not implemented; for the shared backlog)

| ID | Problem | Hypothesis | Expected benefit | Smallest useful experiment | Failure criteria | Dependencies | Effect on existing behavior |
|---|---|---|---|---|---|---|---|
| BP-1 | `PYTHONWARNDEFAULTENCODING=1` on the demo (run, validate, watch) shows **26 file reads/writes with no explicit encoding**: 24 in `src/optpipe` (including `records/store.py:98-110`, `watcher/runtime.py:847,853`) and 2 in `spec/` (Doodle's). The Markdown title contains U+00B7. | On a Windows cp1252 host the Markdown is written as cp1252 with CRLF line endings, so CC-001 C7 fails and the outputs differ from Linux. | One set of output bytes on every OS. | Run `check_fixture_repro.py` on a native Windows host, unchanged, and compare with the Linux fingerprint. | Windows PASSes with the Linux fingerprint, so no change is needed. | A Windows host; BP-2 first. Owners: Poodle (src), Doodle (spec), and Dot if this is the portability lane. | None until a fix is reviewed. A fix would change bytes only on non-UTF-8 hosts. |
| BP-2 | `tzdata` is not a declared dependency. With no time zone database (simulated here with `PYTHONTZPATH` set to an empty dir, **not a Windows run**), `optpipe run` exits **1** with a `ZoneInfoNotFoundError` traceback. | A fresh Windows install fails the same way, and its exit code looks like a rule failure. | Installs that work on Windows; crashes that read as crashes (exit 2). | Same Windows run as BP-1; or add `tzdata; sys_platform == "win32"` in a scratch venv. | Windows runs without `tzdata`. | `pyproject.toml` (a shared contract): Moodle/Poodle. | Adds a dependency on Windows only. |
| BP-3 | AGENTS.md and README state stale facts (above), and the visibility statement conflicts with GitHub. | Agents that read AGENTS.md first start from wrong test counts and the wrong spec version. | Correct starting assumptions in each session. | A docs-only diff, after the owners confirm the facts. | The owners prefer to keep the historical numbers. | Moodle (AGENTS.md); Eugene (visibility). | Docs only. |
| BP-4 | Output-changing PRs have no compact before/after evidence for the demo outputs. | Quoting the CC-001 fingerprint (and the per-file digests) before and after lets a reviewer see which outputs changed and reproduce that with `--expect`. | Cheaper independent reproduction for Zoodle and Noodle. | Use it on the next PR that touches the planner or records, and compare the review effort. | Reviewers find it noise. | CC-001 accepted. | None (evidence practice only). |
