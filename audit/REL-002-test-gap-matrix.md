# REL-002 — Property-based reliability test gap matrix (proposal)

Status: **historical proposal; P01 and P03 now implemented and independently executed**. Date: 2026-11-06.

Verified follow-up: P01 was committed at `2c8db3dd8fbfb78aeb93991fe69b32deee0becba`; P03 at `d4ad49ad1eae03cc41f144cb4a01bfb81d5fab36`. Sol's Work Pilot 001 independently ran the latter head's full offline application suite: **1509 passed, 4 skipped, exit 0**, including both files. See `audit/WORK-PILOT-001.md`. P02/P04/P05/P06 remain proposals; P07 remains deferred. The matrix below preserves the original design rather than claiming every candidate was implemented. Moodle's P03 mutation result remains team-reported, not independently reproduced in this pilot.
Repository: `sOJimARU666/optpipe-agentdesk`; target branch: `sol/phase1-reliability`.
Scope: offline market-data loader and its decision-time eligibility helpers. No production modifications, dependency changes, merges, network access, or trading.

## Source inventory

Inspected on the isolated branch: `tests/test_marketdata_loader.py`, `tests/test_marketdata_r7.py`, `tests/test_marketdata_review22.py`, and `src/optpipe/marketdata.py`. Existing examples already exercise malformed/nonfinite JSON, duplicate keys, encoding, control characters, path confinement, symlinks/hardlinks/races, stale/future timestamps, earnings conflicts, NO IDEA gating, and hashseed/timezone determinism. These are source-inspection findings, not newly executed results.

## Proposed property candidates (not implementation approval)

| ID | Candidate invariant | Existing overlap | Additional coverage and oracle | Priority |
|---|---|---|---|---|
| P01 | Reordered JSON object keys in an otherwise valid pack do not change acceptance, exposed values, or normalized error IDs | Parity/determinism examples | Generate key-order permutations of selected JSON documents; reserialize and rehash; compare normalized outputs with valid twin. Do not assume serialized bytes match. | High |
| P02 | Invalid numeric leaves cannot produce an accepted pack or usable option value | NaN/Inf/huge integer examples | Generate schema-relevant boundary classes (finite in-range, out-of-range, bool masquerading as int, negative, very large exponents). Derive expected acceptance from schema and field semantics, not blanket rejection. Rehash before load. | High |
| P03 | Advancing an injected decision clock cannot revive a quote already stale at an earlier clock, holding pack and policy constant | 20-minute boundary and decision-time stale examples | Generate aware clocks around threshold and verify monotonic rejection for quote staleness; distinguish pack validation from `no_idea_reasons` and avoid confounding future-pack status. | High |
| P04 | Any rejected pack exposes no usable snapshots or decision input | `test_errors_never_leave_data_behind`, `assert_closed` | Generate one invalid mutation at a time with valid manifest and assert no leaked `snapshots`, `index`, `catalysts`, or usable values as appropriate to `PackResult` contract. Confirm exact contract before implementation. | Medium |
| P05 | Equivalent aware timestamps representing the same instant yield the same validation and eligibility outcome | Timezone/hashseed determinism examples | Generate UTC-offset equivalents within accepted timestamp grammar, rehash, and compare outcomes; distinguish canonical-format restrictions from instant equivalence. | Medium |
| P06 | Unexpected mutations in a valid fixture never cause an uncaught exception or a permissive fallback | Crash and malformed fixture examples | Generate bounded byte/text mutations; assert structured error/exit code, but allow valid mutations to pass. Capture seeds and minimized examples. | Medium |
| P07 | A resumed decision must revalidate data freshness rather than trust a prior valid result | No direct end-to-end consumer coverage verified on isolated branch | **Defer** until merged PR #23 consumer paths are reconciled with branch baseline; requires stateful offline sequence tests and immutable fixture snapshots. | Deferred |

## Test-design guardrails

1. For semantic mutations, copy a known-valid committed fixture into `tmp_path`, modify a targeted field, then recompute `pack.sha256`. Otherwise a hash mismatch can mask the intended validation path.
2. Maintain an unmodified valid twin for each property and verify it passes before interpreting generated failures.
3. Record pinned spec version, exact source commit, Hypothesis version if introduced, generated example count, seed/reproduction instructions, and pytest summary.
4. Set bounded generation limits; avoid filesystem denial-of-service, excessive memory, uncontrolled deadlines, or live HTTP.
5. Use explicit schemas to decide whether each generated input is valid; a mutation is not automatically invalid.
6. Keep proposed MD-16+ app-side hardening distinct from spec 1.1.8 mandates.
7. Do not copy PR #23 test assumptions onto this branch without reconciling actual merge base and consumer code.
8. No test result is claimed by this document.

## Minimum acceptance package for first implementation

- One focused test file (prefer P01 and P03 first), no production edits.
- A small, justified dev-only Hypothesis dependency proposal, separately reviewed before changing `pyproject.toml`; alternatively a dependency-free deterministic generator prototype.
- At least one intentionally broken fixture/behavior that the test detects, with sanitized failure output.
- Offline pytest command, exit code, collection count, failures/skips, and CI URL at exact commit.
- Independent reviewer approval before any PR merge.

## Historical CI blocker — resolved

GitHub Actions run `42189869506` on documentation commit `62b645f35872d7de3a96a6d1606fcfc58d175eab` failed. The branch subsequently received the documentation-path hygiene correction `fd7f81284992c14a133fa3351e943e54ecf6c908`; run `42199799865` succeeded. Work Pilot 001 directly verified that success and the later head's run `42209539941`, and independently passed the current offline suite locally. The old run is not a current blocker. Its original traceback was not retrieved during this pilot; the exact original failure attribution is not a new local reproduction.

## Handoff decision

This is a documentation-only gap analysis, **not authorization to add Hypothesis or alter production code**. Request Soodle to commit this single document on `sol/phase1-reliability` only after checking the branch head and confirming no conflicting audit file exists. Report exact SHA, changed paths, and no-runtime-test claim. Continue CI failure triage separately.
