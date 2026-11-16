# REL-001 — Phase 1 reliability baseline

Status: **source inventory reviewed; execution evidence pending**. This document is not a claim that tests were executed or that any security gate has closed.

## Correction notes (2026-11-16)

The original text below is preserved unchanged as historical evidence of the 2026-11-06 inspection. Two statements in it are corrected here. Verified against GitHub (main `871b1b620dc4564a30d81723bb07a362be8156f4`) and the tree on 2026-11-16:

1. **PR #23 is merged, not draft.** PR #23 (`feat/oodle-pr-b`, "FEAT-OODLE-01 PR B") was merged into `main` on 2026-11-06T06:01:27Z as merge commit `1b1d7c4cabd0d68b38cd2fb02bc28421abb47b1b`, merged by `sOJimARU666`. Its final head was `6ec3cf30165f77ca84ab9dbf4139fac2fb0fc3cf` (33 changed files), not the `432d9fd1...` head (27 files) quoted in the section "PR #23 isolation and open blockers". The CHANGES REQUIRED state and the unverified R7 closure claims quoted there describe `432d9fd1...` only; they say nothing about the merged head. R6 / R7 / MD-18 / SEC-GATE-001 gate status is not changed by this note.
2. **`tests/test_marketdata_review22.py` has 20 named test definitions, not 19.** Verified by counting `def test_` in the file on both main `871b1b62` and this branch's pre-merge head `d4ad49ad`: 20 in each. The 36 (`test_marketdata_loader.py`) and 46 (`test_marketdata_r7.py`) counts re-verified unchanged. Like the other counts, 20 is a source-level count of definitions, not a pytest collection total (parametrised tests expand further).

Additional observation (not a correction of a stated claim): on main, `tests/test_pr_b_packs.py` has 12 and `tests/test_pr_b_sol_r7.py` has 9 `def test_` definitions, versus 11 and 8 quoted for the draft head `432d9fd1...`.

Repository: `sOJimARU666/optpipe-agentdesk`
Branch: `sol/phase1-reliability`
Baseline: `e5de176f33553b65567a3764f62a1f5181adef5a` (main / PR #22 merge at inspection)
Soodle-reported branch head after temporary write probe cleanup: `865e5329bb2336d3d71edbe13017db2f66c039f0`. Confirm head immediately before committing.

## Existing tests: overlap inventory

- `tests/test_marketdata_loader.py`: 36 test function definitions; reference parity, malformed packs, path confinement, timestamps, CLI.
- `tests/test_marketdata_r7.py`: 46 test function definitions; adversarial JSON/numerics, filesystem, time/earnings, deterministic behavior.
- `tests/test_marketdata_review22.py`: 19 test function definitions; root confinement, earnings consistency, critical fields, CLI.
- `tests/test_repo_hygiene.py`: current-tree hygiene checks; **not** a full Git history credential audit.

These are inspected source-level counts, not pytest collection totals or pass results. Recount against the actual branch and record differences.

## PR #23 isolation and open blockers

Soodle reports draft PR #23 at `432d9fd108f7c46569ad9a5325a6196a71050636` (27 changed files). It adds `tests/test_pr_b_packs.py` (11 test functions) and `tests/test_pr_b_sol_r7.py` (8 test functions). Moodle reportedly marked that head **CHANGES REQUIRED**, including a critical `--data-dir` AttributeError. Treat all PR #23 R7 closure claims as unverified until the fixed head passes independent review. Do not depend on or edit PR #23 from this branch.

## Phase 1 plan and non-duplication rules

1. REL-002: propose deterministic, bounded property-based tests for **new invariants** (e.g. mutate valid fixtures and recompute manifests so integrity checks cannot mask validation failures). Compare each property against existing example-based tests before adding. No production dependencies.
2. REL-003: prepare a Gitleaks full-history audit, separately from regex current-tree hygiene and GitHub secret scanning. Record version, scan scope, triage and sanitized evidence; never paste secret values.
3. REL-004: collect Ruff read-only baseline, initially `E4,E7,E9,F`, with tool version and exit status. Do not autoformat or modify pinned `spec/`.
4. REL-005: document exact commands, environment, commit SHAs, pass/fail/skip counts, false positives, and open risks. Open a **draft** PR for owner review only when evidence is ready.

## Acceptance gates

- Record exact branch head and PR #23 comparison before each edit.
- Run baseline pytest and report command, environment, exit code, and counts; do not infer passes from code inspection.
- Demonstrate that new property tests detect deliberate invalid examples and reproduce failures by seed.
- Record Gitleaks history coverage and findings disposition; a clean working tree is not proof of clean history.
- Record Ruff baseline and proposed changes separately from any CI enforcement decision.
- Keep R6 / SEC-GATE-001 open. Python-level network guarding is **not** OS-level egress isolation.
- No merge, deployment, broker access, live trades, spending, or production network changes.

## Current evidence and limitations

Source inventory reviewed; no fresh test execution has been performed by Sol. ChatGPT GitHub Contents API writes returned HTTP 403 for both linked identities; Soodle independently reports working write access through otppipe-agents. This handoff authorizes only the documentation file above.
