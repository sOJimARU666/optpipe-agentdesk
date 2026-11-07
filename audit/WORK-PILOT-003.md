# SOL AI LAB — WORK PILOT 003

Date: 2026-11-06. Repository: `sOJimARU666/optpipe-agentdesk`. Publication target: `sol/phase1-reliability`. **closure_claim: none**.

## Capability gained and enterprise value

Optpipe now has a reusable, dependency-free audit that examines Python source at an exact Git commit, records every input blob and SHA256, and returns distinct results for review candidates and incomplete execution. It can run offline without importing or executing the audited source. A separate archive-based verifier checks the inventory and compilation independently of the primary enumeration code.

This gives autonomous work sessions a reproducible fallback when the preferred linter is unavailable. The evidence can be reviewed or replayed from one audit-only patch, without repeating environment discovery or asking the owner to coordinate each command. **It is a limited static check, not Ruff, and does not close REL-004.** No application behavior or security control was changed.

## Reconciled baseline

Direct GitHub reads at the start of this pilot established:

| Item | Verified state |
|---|---|
| Reliability remote head | `d4ad49ad1eae03cc41f144cb4a01bfb81d5fab36` |
| Reliability source tree | `451b6ffd2f281efe2f4717db164b414280b68aca` |
| Main | `ae95c68cc10240fe75b41cc30272c463d960f5e3` |
| Main source tree | `c4af5232c75bbb566482285469772f35dc872eae` |
| Reliability CI | Run `42209539941`, successful at `d4ad49a` |
| PR #31 | Open; unchanged head `26d7aa57666c55df4f77fea82c5aae9d832d1788` |
| PR #33 | Open; unchanged head `bd4d63f4cc18cf03915a04b1e8f0cad6e9f60c49`; runs `42249900529` and `42249883086` successful |
| Pilot 001 | Local commit `532e0f2130737fba45ee4865d2da4cad52568b67`; sent relay `1a1b2108ce60c4f3` |
| Pilot 002 | Local commit `5d62b0326b0a768ad2b2675cab9d3764d181cbd5`; sent relay `1a1b266d207b7042` |

The target branch's remote `audit/` listing contains only the original REL-001, REL-002 and RES-002 documents. Neither pilot's new files were present there. The checked Gmail results had no newer Soodle publication or review receipt. This establishes absence from the checked target branch, not absence from every possible private workspace. Publication and independent team review of both earlier pilots remain unconfirmed.

PR #31/#33 implementation and receipt work remain with their existing owners. This pilot neither replays their integration tests nor changes their files. Pilot 001's 1509 passed / 4 skipped and Pilot 002's history results remain **prior executed evidence**, not tests rerun by Pilot 003. GitHub CI status above is a direct platform observation; it is not a new local execution.

## Tool availability and decision

Inspected `AGENTS.md`, `README.md`, `pyproject.toml`, `.github/workflows/ci.yml`, the repository's safety tests, and available Python environments. Ruff, Pyflakes, Flake8, Pylint, mypy and Bandit were absent from PATH and unavailable as modules in the system interpreter, existing project environment and primary runtime. There is no Ruff configuration, dependency declaration or CI step in the checked repository configuration.

**Ruff E4/E7/E9/F baseline: NOT_RUN.** No dependency was installed or downloaded, no CI policy changed, and no permission expansion was attempted. The fallback uses CPython 3.12.14 and Git 2.52.0 already available. Full tool versions and command receipts are in `REL-004-verification.json`.

## Executed bounded static checks

The primary scanner reads Git blobs at the supplied full SHA, never the working copy. Scope is every tracked `.py` file under `src/`, `tests/` and `scripts/`, including the three generated schema modules. It excludes `spec/`, audit scripts themselves, non-Python files, dependencies and untracked changes. Generated models and pinned spec files were not edited.

Checks performed:

1. CPython compilation and compiler warnings, without executing the compiled code.
2. Python 3.11 grammar parsing using CPython 3.12's `feature_version`; this is not a Python 3.11 runtime test.
3. Global-name candidates using `symtable`, known module bindings and builtins.
4. Repeated direct function/class names in module and class bodies, including overwritten test names.
5. Duplicate evaluable literal dictionary keys, including equal values of different literal types.
6. Nonempty-tuple assertions, which can pass without checking the intended condition.

| Frozen source | Python files | Source / tests / scripts | Bytes | Candidates | AST analyzed |
|---|---:|---|---:|---:|---:|
| Reliability `d4ad49a` | 116 | 58 / 53 / 5 | 952,641 | 0 | 116 |
| Main `ae95c68` | 121 | 61 / 55 / 5 | 1,083,915 | 0 | 121 |

These are two revision snapshots with overlapping files, not 237 unique files. Both reports explicitly record one global-name coverage limitation: `src/optpipe/models/__init__.py` uses a star import. Its compilation and other applicable checks still run. No suppression or source exception was added to make a finding disappear.

The scanner uses the offline Git precautions established in Pilot 002: replacement objects and lazy fetching disabled, no shell or fetch, 60-second command timeout. It requires a full commit SHA and rejects missing commits, empty scopes, non-regular Python entries and submodules in the selected roots. Bounds are 2,000 Python files, 2 MiB per file and 128 MiB total. Exit 0 means zero candidates within the reported checks and limitations; exit 1 means candidates need review; exit 2 means incomplete execution. No automatic fixes occur.

## Independent verification and controls

The separate verifier obtains the same source via `git archive`, independently recomputes Git blob IDs and SHA256 for every Python member, and compares the complete inventory to the primary `ls-tree`/`cat-file` result. It then invokes `py_compile` and `tabnanny`. Temporary bytecode is removed and nothing is written under `spec/`.

| Executed check | Result |
|---|---|
| Reliability archive inventory comparison | Exact match for all 116 files |
| Main archive inventory comparison | Exact match for all 121 files |
| Independent compilation and indentation | Zero diagnostics for both snapshots |
| Synthetic controls | **15 passed** |
| Existing hygiene and paper-only suite | **101 passed**, one dependency warning, 7.45 seconds pytest time |
| Runtime network-guard self-check | Socket, urllib GET and requests GET blocked as expected; PASS |
| Pinned spec verification | 1.1.8 intact, 191 files plus pinned historical material |

The controls exercise compilation failures, warning capture, broken tuple assertions, equal literal dictionary keys, overwritten test/class methods, nested missing globals, legitimate closures/imports/builtins, star-import coverage reporting, newer grammar rejection, source encoding and non-execution, dirty/untracked-file isolation, missing revisions and size limits, Python symlinks, all three CLI exit codes, and independent-verifier rejection of a tampered inventory or noncompiling source. Positive and negative controls are synthetic; no credentials or market data were created.

This is independent enumeration and a separate compilation entry point, using the **same CPython engine**. It is not a different parser, a Ruff run or a second-agent review. All local executions here were performed by Sol. Existing Moodle/Noodle review remains pending; no redundant parallel review was started.

During development, the two added verifier controls exposed a portability defect in the new audit helper: passing absent root directories to `git archive` failed on a minimal synthetic repository. The verifier now selects the existing scope roots before archiving. The final 15 controls pass, including the two that originally errored. The failure and successful rerun are retained in the execution record. This was a defect in the new audit helper, not a defect found in Optpipe application code.

The full application and full pinned-spec suites were not rerun for this audit-only delta. Relevant safety tests and pin verification were executed. The static reports are deterministic on the recorded interpreter and script bytes; test elapsed times are not deterministic.

## Finding classification and actual impact

| Finding | Severity / reproducibility | Operational impact and disposition |
|---|---|---|
| No code candidates under the listed rules | Scoped result reproduced through two source-enumeration paths | No production fix justified by this result; it does not prove the absence of defects. |
| Ruff unavailable | Coverage blocker; verified in all three available Python environments | Required Ruff baseline remains open. Use an already approved executable if one exists elsewhere; installation requires a separate decision. |
| Star import in model exports | Informational coverage limit, reproduced in both revisions | Global-name analysis is not claimed for that one module; no source change proposed. |
| AnyIO pytest-plugin deprecation warning | Low; observed during the 101-test run with AnyIO 4.14.2 and pytest 9.2.0 | The plugin imports the deprecated `CallSpec2` alias. Warning announces removal in pytest 10; current tests pass. This is an environment compatibility warning, not a confirmed current application defect. No dependency changed. |

Uncovered areas include unused imports/variables, local-variable data flow, execution ordering, dynamic globals/imports, type checking, runtime API compatibility, most Ruff rules, security taint analysis, non-Python code and semantic finance correctness. Duplicate-definition candidates may be intentional overloads or property setters; any future hit requires source review. The fallback cannot substitute for maintained static-analysis tooling or the application test suite.

## Reproduction and evidence

Run from the repository root with Git and Python already available. Use CPython 3.12.14 for byte-identical JSON receipts. Obtain the two frozen commits through the existing authorized repository channel if absent; the scanner itself never fetches.

```sh
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s audit -p 'test_rel004_stdlib_baseline.py' -v
python3 audit/rel004_stdlib_baseline.py --repo . --revision d4ad49ad1eae03cc41f144cb4a01bfb81d5fab36
python3 audit/rel004_stdlib_baseline.py --repo . --revision ae95c68cc10240fe75b41cc30272c463d960f5e3
python3 audit/rel004_verify_archive.py --repo . --baseline audit/REL-004-reliability-results.json
python3 audit/rel004_verify_archive.py --repo . --baseline audit/REL-004-main-results.json
sha256sum -c audit/work-pilot-003-SHA256SUMS
```

Exact executed command arrays, environment overrides, exit codes, durations and raw stream digests are in `REL-004-verification.json`. `REL-004-execution.log` preserves output with the absolute checkout prefix replaced by `.` for repository hygiene; complete scanner output is stored in the two JSON files. The existing test interpreter was reused via a relative path from the isolated worktree. On another checkout, use its already installed project interpreter for the safety commands; do not install anything based on this report.

| Artifact | SHA256 |
|---|---|
| Reliability inventory | `c765cffcca3903c839b6c4cd5107ce275a8dc0a7b577b9380c34a796a2bbdcbe` |
| Main inventory | `d379a40e14dba7d87a21cc4eed56d2f2db6f3e92a7b982737470048d0b973182` |
| Reliability result JSON | `86d6e9c776b2a9a1a52bfa91b6ef4953f1b39a024a6135a4b8432afbddc994f0` |
| Main result JSON | `67c2a22822def47091978c527cfc8ee46fee315e3c1ae7e13b8239f3bf55e33c` |

The checksum manifest covers all package files except itself. Commit and patch hashes are recorded in the handoff receipt, avoiding self-referential report hashes.

## Publication and next action

This delta is based directly on remote `d4ad49a` and adds only new REL-004/Pilot 003 files under `audit/`. It does not replace or duplicate either earlier pilot's files. The last actual GitHub write probe, during Pilot 001, returned HTTP 403 (`Resource not accessible by integration`); this pilot uses the established Soodle relay and does not repeat that failed probe or change permissions.

Next executable action: Soodle checks for concurrent publication, applies this audit-only patch to `sol/phase1-reliability`, and returns the resulting remote SHA and CI URL through the existing relay. The ordinary Moodle/Noodle review can use the included reproduction commands; no additional review layer is requested. The earlier pilot patches remain their own pending queue entries. A verified, already approved Ruff executable would enable the outstanding read-only E4/E7/E9/F baseline; otherwise that coverage remains blocked without installation approval.

No merge, deployment, live market request, brokerage action, spending, dependency installation or security-policy change was performed or authorized by this report. REL-003's broader scanner coverage and REL-004's Ruff gate remain open. The one-hour session has a separate scheduled closeout; this audit does not extend its authorization.
