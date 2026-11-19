# REL-003 — Gitleaks full-history baseline (2026-11-16)

Status: **EXECUTED, 0 findings under the limits below.** A zero-finding scan is not proof that no secret was ever committed, is not independently accepted until Moodle (runtime) and Noodle (static) review it, and does not close R6, R7, MD-18 or SEC-GATE-001. It is separate from `tests/test_repo_hygiene.py` (a current-tree regex check) and from GitHub secret scanning (not queried).

## Identity and environment

| Item | Value |
|---|---|
| Source scanned | merge commit `ed8151f2771a874f474521600267ff3eb2de073c` (tree `bfe89f41e899d2584f8a3e6b3114edc464ecdad0`) = `sol/phase1-reliability` + main `871b1b620dc4564a30d81723bb07a362be8156f4` |
| Clone | full (not shallow), freshly cloned 2026-11-16, plus every `refs/pull/*/head` fetched (read-only) so merged and closed PR heads are covered. 122 commits are reachable from all refs (`git rev-list --all`). |
| Tool | Gitleaks 8.30.1 (linux x64). Binary sha256 `88f91962aa2f93ac6ab281d553b9e125f5197bbbce38f9f2437f7299c32e5509`. |
| Provenance | The binary was **not installed**. It was already on the machine in an earlier reviewer packet (REL-003 receipt of the 2026-11-06 review) and was copied unchanged into the evidence bundle and given an execute bit. Its sha256 equals the value recorded in that receipt; this run did **not** re-download the official release archive to re-verify it against the upstream checksums. That caveat stays open. |
| Rules | Gitleaks built-in default configuration (no `--config`; the repo has no `.gitleaks.toml` and none was added; no allowlist, ignore file or baseline was used). |

## Commands and results

| Scan | Command | Exit | Findings |
|---|---|---|---|
| Full history, all refs | `gitleaks detect --source . --redact --no-banner --report-format json --report-path <report> --log-opts='--full-history --all'` | 0 | **0** (by rule: none; by file: none) |
| Working tree at the scanned commit | `gitleaks detect --source . --no-git --redact --no-banner --report-format json --report-path <report>` | 0 | **0** |
| Canary (positive control, disposable git repo outside this repository) | same history command against a throwaway repo holding one randomly generated AWS-access-key-shaped string | 1 | **1**, rule `aws-access-token`, file `canary.txt` |

Gitleaks reported **81 commits scanned** and `no leaks found` for the history scan. The canary proves the binary, rule set and `--redact` reporting path detect a secret of that shape; it does not show that every rule family would. The canary value was random, never written to a log, and the throwaway repository was deleted.

## Output handling

`--redact` was used on every run. Reports are empty JSON arrays (`[]`), so no match text, secret or fingerprint exists to redact or print. No secret values appear in this document.

## Coverage limits

* Gitleaks scans diff hunks (`git log -p -U0`), not a full checkout of every historical blob, and uses the default archive-traversal depth.
* The `81 commits scanned` figure is Gitleaks' own counter. Gitleaks omits commits that add no lines, and this run did not independently reconcile 81 against the 122 reachable commits. An earlier packet's reconciliation (72 scanned of 109 refs-local commits) used the same tool and was for a smaller ref set.
* Only refs on GitHub at clone time plus PR heads were scanned. Anything unreachable from those refs (force-pushed-away history, deleted forks, unfetched refs) is outside this result, and so is data in issues, PR comments, Actions logs and release assets.
* The binary's upstream provenance was not re-verified this run (see above).
* The scan covers the commit that merged main into the reliability branch. Later commits on the branch (documentation and one test file) were reviewed by the current-tree check `tests/test_repo_hygiene.py`, inside the offline regression, but were not scanned by Gitleaks history mode before this note was written.

## Disposition

No repository findings, so there is nothing to classify as a true or false positive and no allowlist is needed. No rule, config, workflow or CI change is proposed here. Whether to run Gitleaks in CI is a separate policy decision for the owner.
