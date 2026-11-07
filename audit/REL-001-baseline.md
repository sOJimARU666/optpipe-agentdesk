# REL-001 — Phase 1 reliability baseline

Status: **historical source inventory; fresh execution evidence recorded in WORK-PILOT-001.md**. No security gate is closed by this document.

## Verified follow-up — 2026-11-06

Sol independently executed the offline application suite at `d4ad49ad1eae03cc41f144cb4a01bfb81d5fab36`: **1509 passed, 4 skipped, exit 0**. Spec pin, generated models, spec patch, network-guard self-check, and offline CLI run/validation also passed. See `audit/WORK-PILOT-001.md` and its machine-readable evidence for exact scope, versions, commands, and limits. The locally skipped cases remain opt-in; the pinned spec's separate full suite was not rerun locally.

The PR #23 paragraph below is historical: its consumer integration is now in `main`; current `main` was verified as `ae95c68cc10240fe75b41cc30272c463d960f5e3`. The reliability branch still diverges from it at `e5de176f33553b65567a3764f62a1f5181adef5a`; this audit does not synchronize branches. PR #31's current-main integration failure was independently reproduced in disposable worktrees. REL-003 and REL-004 remain unexecuted proposals; R6 / R7 / MD-18 / SEC-GATE-001 remain open.

The remaining sections preserve the original inventory and handoff context.

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
