# SOL AI LAB — WORK PILOT 002

Date: 2026-11-06 (Europe/Berlin). Repository: `sOJimARU666/optpipe-agentdesk`. Target: `sol/phase1-reliability`. **closure_claim: none**.

## Result and scope

**Completed a bounded offline history audit using the repository's existing credential-detection regex: zero matches across 114 commits and 853 unique file blobs.** A second run in a separate local mirror produced byte-identical results. Eleven synthetic-control tests passed, including a fake credential added and then deleted from history. No dependency was installed.

This completes the stated existing-rule history task. It does **not** establish that the repository is secret-free or close REL-003's broader scanner requirement. Gitleaks, Betterleaks, TruffleHog, detect-secrets and Ruff were not found on PATH or in the project environment. Installing them is outside this pilot's authorization. The broader REL-003 scanner pass and REL-004 Ruff baseline remain open.

## Reconciliation before execution

| Item | Verified state |
|---|---|
| Reliability remote head | `d4ad49ad1eae03cc41f144cb4a01bfb81d5fab36` |
| Main | `ae95c68cc10240fe75b41cc30272c463d960f5e3` |
| Pilot 001 | Local `532e0f2130737fba45ee4865d2da4cad52568b67`; publication not observed. Existing patch relay message `1a1b2108ce60c4f3`; no new Soodle reply found in the checked Gmail results. |
| PR #31 | Open draft, unchanged head `26d7aa57666c55df4f77fea82c5aae9d832d1788`. No repeated integration work. |
| PR #33 | Open/unmerged receipt-refresh work at `bd4d63f4cc18cf03915a04b1e8f0cad6e9f60c49`. GitHub reported success for runs `42249900529`, `42249897699`, `42249883086`. Left with the existing team. |
| Reliability CI | Run `42209539941` still successful at the recorded branch head. P01/P03 were not reimplemented. |
| P03 negative control | Prior Moodle result remains team-reported; not repeated or relabeled as this pilot's execution. |

Pilot 001's 1509 passed / 4 skipped full-suite result is prior evidence. Pilot 002 did not repeat that suite. Current GitHub statuses above were directly read; PR #33's author-reported local counts were not adopted as independent test results.

## Frozen history inventory

The acquisition used read-only Git remote operations before scanning. No repository settings, remote refs or network policy were changed. Three advertised objects absent from the clone were fetched into local audit refs: PR #1 head and PR #31/#33 merge refs. Omitting those would have left the scan incomplete.

| Inventory measure | Executed value |
|---|---:|
| Advertised rows | 70 |
| Branch heads / pull refs / HEAD aliases / tags | 34 / 35 / 1 / 0 |
| Unique root SHAs | 37 |
| Reachable commits | 114 |
| Unique file blobs | 853 |
| Tree objects | 571 |
| Total reachable objects | 1538 |
| File-blob bytes | 17,211,070 |
| File blobs plus commit-object bytes scanned | 17,316,849 |
| Largest blob | 469,759 bytes |
| Non-UTF-8 blobs / NUL-containing blobs scanned | 7 / 7 |
| Skipped objects / submodule entries | 0 / 0 |
| Detector matches | 0 |

The clone was non-shallow. The before, after-fetch and final advertised-ref inventories matched. The scan consumes frozen SHA roots, so local Pilot 001 commits and local synthetic experiments cannot silently expand the measured scope. Every scoped commit SHA is recorded in the result JSON, and every advertised SHA/ref pair is in `REL-003-ref-snapshot.txt`.

Scope-file SHA256: `771c358723fd5955c529a39e44a9f7d1e31c42043ada1d1bdfeb755782837766`.

Commit-set SHA256: `cf98c095b9df6c6c4f910bddcd337d9f2228e911ad55877cb9dd57a3be6ad641`.

Blob-set SHA256: `1938c2b4cdb9e9e8ab37597a96da74ebbf4fe7b4ebb5bce7bc4c93c424ec1bda`.

## Detector and safeguards

The scanner extracts only the `SECRET` regex literal from `tests/test_repo_hygiene.py` at the frozen reliability commit; it never executes that source file. Its SHA256 pin is `213ae0df179f41758bad47482dcd0739b919c12a1bc76d8514602ec5678db108`. Pattern SHA256: `51b25649e6f0c51ad87afb5514808bcf2e0da693f2caf82a2b71136dc81df055`.

The existing pattern covers selected GitHub, AWS access-ID, sk-prefix, Slack, private-key-header, JWT-shaped and assigned-credential forms. This is a reuse of existing coverage, not a new comprehensive rule library. No path exclusions or allowlists were applied. Archived spec files, tests, deleted historical files and binary blobs were included. Commit objects, file paths and ref names were also checked. Binary content is decoded with surrogate escape to preserve ASCII matches; compressed or encoded payloads are not expanded.

The helper emits rule IDs, object SHAs, sanitized paths and locations if a match occurs; never matched values or surrounding source. No credential was used or validated against any service. Synthetic values were created only in disposable test repositories and deleted with those repositories.

The helper is offline: it disables Git lazy fetching and replacement objects, never runs fetch itself, and refuses shallow or missing histories. Bounds are 20,000 objects, 2 MiB per scanned object, 128 MiB total content, and 60 seconds per Git command. Exceeding a bound returns incomplete status, not a clean result. Exit codes are 0 for a completed scoped scan with no matches, 1 for matches needing triage, and 2 for incomplete execution.

## Executed verification

| Check | Result | Exit |
|---|---|---:|
| Primary frozen-scope scan | 0 matches, no skips | 0 |
| Object-graph blobs vs union of every commit's full file tree | 853 vs 853; identical set digest | 0 |
| Separate mirror created with `--mirror --no-local` | Byte-identical scan JSON; no alternate object store | 0 |
| Git `fsck --full --no-reflogs --no-dangling` in mirror | No output/errors | 0 |
| Synthetic controls | 11 tests passed | 0 |
| Final exact reproduction using the packaged ref snapshot | Byte-identical scan JSON | 0 |
| Existing repository hygiene and paper-only checks | 101 passed in 6.70 s | 0 |
| Pinned spec check | Version 1.1.8 intact | 0 |

The 11 controls cover deleted credentials; disconnected branch coverage; binary/non-UTF-8 data; commit-message detection; path/ref redaction; missing objects; size bounds; a real local depth-one clone; invalid/empty ref snapshots; synthetic positives for all nine detector categories; and the source-rule pin.

The separately recorded negative control committed a known fake fixture at `61c7cf6cbdb7be218d1d0f1b2bdb211dd8354fca` and removed it at `69c9ad2b2f511910006a4a2d244ceab0ab0f477f`. **History scan: 1 match; current-tip scan: 0 matches.** The returned historical location points to the earlier commit's `removed.txt`. No fake value appears in the receipt.

Scan JSON SHA256: `5497220bac60b7696072964768598b58b2df4276f6b67ef71abda73d2e992f07`.

These checks were executed by Sol in this session. The separate mirror and independent inventory strengthen reproducibility but are not a second human/agent review. Moodle/Noodle review remains pending.

## Reproduction

Use a full clone containing every object referenced by the packaged snapshot. If any object is absent, the helper fails incomplete; obtain missing approved refs before the offline scan. Existing Git and Python 3.11+ are sufficient; this execution used Git 2.52.0 and Python 3.12.14. Executable and script digests are recorded in `REL-003-verification.json` and the result JSON.

```sh
python3 audit/rel003_history_scan.py --repo . --refs audit/REL-003-ref-snapshot.txt
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s audit -p 'test_rel003_history_scan.py' -v
sha256sum -c audit/work-pilot-002-SHA256SUMS
```

Acquisition commands and all executed check commands are preserved in `REL-003-verification.json`. The acquisition includes `git ls-remote origin`, `git fetch --no-tags origin`, and the three explicit pull-ref fetches. The repeated scan itself used no network. The project test environment retained `OPTPIPE_NETGUARD=1` and the existing conftest hooks. No dependency installation, test exclusions, security-policy changes or full-suite rerun was needed for this audit-only change.

## Limits, publication, and next executable action

The bounded result cannot detect credential classes outside the inherited regex, encoded/compressed secrets, external LFS payloads, inaccessible/unadvertised refs, unreachable commits from deleted/rewritten history, or reflogs. It does not replace credential revocation or a maintained scanner. Zero matches means only zero matches under these stated rules and scope. REL-003, REL-004, R6, R7, MD-18 and SEC-GATE-001 are not closed.

The last GitHub write attempt in this session, during Pilot 001, returned HTTP 403, `Resource not accessible by integration`. Pilot 002 does not repeat that failed probe or change permissions. The audit-only delta is prepared independently from remote head `d4ad49a`, for the existing Soodle relay. It touches no Pilot 001 or PR #31/#33 files. Publication remains pending until a new remote SHA is verified.

Next: Soodle applies this delta to `sol/phase1-reliability` after checking for concurrent audit work. Moodle/Noodle run the three reproduction commands and return scope/result hashes and review findings. Soodle can also check whether an already approved, provenance-verified full-history scanner exists in their runner. If none exists, selecting/installing a scanner remains a separate approval decision; no installation is authorized by this handoff. Ruff is likewise deferred until an approved executable is available. No merge, deployment, spending, brokerage action or live trade is authorized.

The package contains this report, the scanner and synthetic tests, frozen refs, scan JSON, verification JSON, execution log and SHA256 manifest, all under `audit/`. The manifest covers every member except itself; the local commit and patch hashes are supplied separately in the relay to avoid self-referential hashing.
