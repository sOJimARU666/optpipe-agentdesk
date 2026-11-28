# Replay Bridge 001 (paper only, decision support only)

`src/optpipe/research/replay_bridge.py` projects the frozen Lesson 001 v3 decision tape (SYNTHETIC, archive sha256
`159de19c0fa74be03a46da954af92f8f1f164a62e0c439f0a57aa8bb1c3947a3`) into offline research CSVs. It has no broker,
fill model, data fetch, optimizer, entries, exits or P&L, and no new dependency (standard library plus the repo's
own `optpipe.nyse_calendar`). Optpipe owns the decisions. The bridge copies them and can only lower a signal to
BLOCKED, never raise one. Nothing here is a trade instruction.

Origin: Sol's standalone prototype `Optpipe-Replay-Bridge-001.zip` (sha256 `dd6a0815...fb220`; `replay_bridge.py`
sha256 `8bb3ed3c...056f`), with Zoodle's test report (PASS with gaps) and Noodle's review (ACCEPT WITH CONDITIONS)
folded in, plus Doodle's PROPOSED price-freshness policy (revised 2026-11-28 with Eugene's answers, relayed by Soodle).

## Run

```sh
python -m optpipe.research.replay_bridge export path/to/Optpipe-Lesson-001-Portability-v3.zip NEW_OUTPUT_DIR
python -m optpipe.research.replay_bridge verify NEW_OUTPUT_DIR
python -m optpipe.research.replay_bridge verify --self-consistency-only NEW_OUTPUT_DIR
python -m optpipe.research.replay_bridge verify NEW_OUTPUT_DIR \
    --expect-archive-sha256 159de19c0fa74be03a46da954af92f8f1f164a62e0c439f0a57aa8bb1c3947a3 \
    --expect-decisions-sha256 659175c6eb8a31ea3bebb53d71a27d462557ec0b62aa2494efab2918e71804f6 \
    --expect-code-sha256 <sha256 of src/optpipe/research/replay_bridge.py> \
    --expect-policy-sha256 <sha256 of config/replay_bridge_price_policy.json>
```

`export` refuses any archive other than the pinned one, refuses an existing output folder (even an empty one), and
refuses to run without a complete price policy (`--price-policy`, default `config/replay_bridge_price_policy.json`).
A policy section written as a string (instead of an object) also exits 2, not a crash. Every refusal exits 2 and
leaves no output folder.

Output folder (written atomically: built in a hidden temp folder `.NAME.partial-*` beside the target, then one
rename). On error or SIGTERM the temp folder is removed, so the files land together or not at all. SIGKILL cannot
be caught and may leave a `.NAME.partial-*` folder; that folder is always invalid. `verify` refuses it, and
`export` of the real target ignores leftovers beside it. Hidden partial folders are never accepted as output.

| File | Content |
|---|---|
| `decision_tape.csv` | Sol's 10 columns. Byte-identical to Sol's evidence tape for the frozen archive (sha256 `039ee588...8de24`). |
| `decision_tape_labeled.csv` | The same rows plus `upstream_decision`, `price_kind`, `price_label` (BAR / REALTIME / DELAYED), `price_status` (OK / BLOCKED), `price_reason_code`, `price_age_seconds`, `price_delay_seconds`, `price_adjusted_age_seconds`, `price_max_age_seconds` and the labels `paper_only`, `decision_support`, `fixture_set`, `fixture_set_sha256`. |
| `price_block_log.csv` | One record per blocked decision-time price (header only when none): Doodle's 16 columns plus the same 4 label columns. |
| `price_accept_log.csv` | One record per accepted decision-time price (header only when none): Doodle's 14 columns plus the same 4 label columns. Frozen archive: 390 records, all `label=BAR`, age 0. |
| `bridge_receipt.json` | Archive, decisions and CSV hashes; counts (final and upstream); `price_policy` (id, status, file sha256, path, status note, numbers) and `price_blocks` / `price_accepts`; `code_sha256` (sha256 of the bridge source file), the exact `command`, `python` version and `generated_at_utc`. |
| `SHA256SUMS` | sha256 of the five files above. |

`verify` always recomputes what it can from the files and refuses a mismatch (row counts, per-action counts,
block and accept totals and breakdowns) even if SHA256SUMS was regenerated to match an edited receipt. It also
cross-checks `decision_tape.csv` against `decision_tape_labeled.csv` row by row on Sol's 10 columns, and checks
the sidecars against the plain-tape projection (accepted vs blocked price, accept/block partition, flags, and
log fields that repeat the tape). Sidecar columns that cannot be derived from the plain tape are listed as
DIAGNOSTIC in the CLI output and in `_verification.diagnostic_fields`; they must still agree between the
labeled CSV and the matching log row. If the receipt claims the official fixture set, the archive sha must
equal the repo pin `ARCHIVE_SHA`. If it records the frozen `decisions.json` sha, `decision_tape.csv` must
equal `FROZEN_CSV_SHA`. The recorded `code_sha256` is compared to the running source file. Hidden
`.*.partial-*` folders are refused; hidden leftovers beside a complete folder are ignored.

The expected policy identity comes from the repo configuration (`config/replay_bridge_price_policy.json`) or
from `--expect-policy-sha256`, never from the receipt. Approval status is never read from the receipt. The
"Policy status PROPOSED is not owner-approved" notice is printed when the *configured* policy file has that
status, not when the folder claims it.

The CLI `verify` command applies the repo pins by default (archive, frozen decisions, frozen tape, running
code, and the configured policy file). In pinned official mode it also recomputes `decision_tape_labeled.csv`,
`price_block_log.csv`, `price_accept_log.csv` and the receipt's `price_policy` fields from the vendored
pinned archive plus that configured policy, then compares the bytes, so an official folder that was edited
and fully re-signed is refused. A required pin that cannot be resolved, or a policy identity that does not
match the configured file, is refused. The output and the in-memory `_verification.checked` / `.skipped`
lists name exactly the pins that ran (including `official_sidecars` when the official projection was
compared). `--self-consistency-only` turns the repo pins and the official recompute off; the file-derived
checks above still run. Optional flags are an independent check against values you supply:

- `--expect-archive-sha256` — must equal the receipt `archive_sha256` and, when present, `labels.fixture_set_sha256`
- `--expect-decisions-sha256` — must equal the receipt `decisions_sha256`
- `--expect-code-sha256` — must equal the receipt `code_sha256`
- `--expect-policy-sha256` — must equal the receipt `price_policy.file_sha256`

Any pin that is given must match; a mismatch, or a missing receipt field, fails with exit 2 and writes nothing.
In pinned mode `--expect-policy-sha256` must equal the configured policy file. The Python `verify()` API defaults
to unpinned (file-derived checks only) so offline test-bundle folders can be checked; pass `pinned=True` to apply
the repo pins. `--price-policy` on `export` still accepts any path; `load_policy` records the `status` the file
declares. There is no approval flag and no way to mark a policy owner-approved. A folder exported from a test
zip (not the official archive) is labeled `fixture_set=test-fixture` and is refused by the pinned CLI default.

The receipt records `price_policy.file_sha256` and `price_policy.path` (repo-relative when the file is in the repo,
otherwise the basename, never an absolute box path) and always includes `price_policy.status_note`: a policy with
status PROPOSED is not owner-approved, and the receipt has no approval mechanism. `verify` prints that sentence
when the configured policy file is PROPOSED, even if a re-signed receipt claims APPROVED. `export` prints it
from the same configured file. Receipt fields `command` and `generated_at_utc` are produced at export time and
are not pinned (they differ between genuine official runs).

## The seven fixes

1. **Labels.** Every row of `decision_tape_labeled.csv` and `price_block_log.csv` carries `paper_only=PAPER_ONLY`,
   `decision_support=DECISION_SUPPORT_ONLY_NOT_AN_ENTRY_OR_FILL`, `fixture_set` and `fixture_set_sha256`.
   `decision_tape.csv` keeps Sol's 10 columns unchanged, because Doodle's freshness policy requires it to stay
   byte-identical (see Conflicts).
2. **Inner tampers.** All 33 of Zoodle's inner controls are re-created in `tests/test_replay_bridge.py` and all are
   caught (table below). The 8 that passed before are now tape-integrity refusals.
3. **Evidence.** `SHA256SUMS` in every output folder; the receipt records the code hash, the command, the time,
   the policy file sha256 and path, and a note that status PROPOSED is not owner-approved (no approval mechanism).
   CLI `verify` applies the repo pins by default; `--self-consistency-only` turns those pins off. File-derived
   checks (counts, tape cross-check, running code, default policy, official-fixture claims) always run.
4. **Tests.** Network guard (bridge runs in a process with the repo guard on; the only guard hit is the probe),
   existing output folder refused (empty and non-empty, contents untouched), signal with a missing price.
5. **Atomic output** plus negative controls for a missing receipt, a mismatched receipt (with and without
   regenerated SHA256SUMS), a receipt from another run, a tampered CSV, a stripped label and an extra file; and on
   the input side, a missing receipt member and a receipt digest / count mismatch.
6. **Price status.** `price_status`, `price_reason_code`, `price_label` and the age columns in the labeled CSV.
7. **Age ceiling.** Doodle's PROPOSED decision-time price policy (below), parameterised in
   `config/replay_bridge_price_policy.json`. A missing file, a missing key, a null, a negative or non-finite value
   fails closed (exit 2, nothing written). A policy object written as a string also exits 2 (not a crash).
   TODO(Doodle): the values are PROPOSED for spec 1.1.9; Eugene's answers
   were relayed by Soodle; Doodle owns them.

## Inner-tamper disposition (Zoodle's I01-I33)

"Refused" means the export aborts with exit 2 and no output. "Blocked" means the export completes, the price is
blanked and logged with the code, and the signal row is lowered to BLOCKED. No case lets a tampered signal through.

| Case | Before (Sol's bridge) | Now | Check |
|---|---|---|---|
| I01-I04, I06-I08 | refused | refused | receipt digest / count, JSON, clock order, duplicate |
| **I05** dropped row, receipt re-digested | **exported 390 rows** | refused | one row per minute, from the session open to the session close (`optpipe.nyse_calendar`) |
| I09 signal price `{"status": "missing"}` without source_id | refused | refused | price source_id must equal provenance |
| I10, I11 | refused | refused | price source_id |
| **I12** invented `iv_rank` indicator | **ignored, exported** | refused | the indicator set must be exactly the 8 Lesson 001 names; non-price indicators have exactly 4 keys |
| **I13** SIGNAL_BULL with vwap missing | **exported** | refused | a signal needs every non-price indicator `ok` |
| **I14** SIGNAL_BULL with range missing | **exported** | refused | same |
| **I15** SIGNAL_BULL with `bull_vwap=Fail` | **exported** | refused | a signal needs every common gate and its direction's gates `Pass` |
| **I16** ABSTAIN flipped to SIGNAL_BEAR | **exported** | refused | gates; `signal_used_before` must match the tape's signal history (one signal per session) |
| I17 BLOCKED row without price flipped to bull | refused | refused | gates |
| **I18** BLOCKED row with price flipped to bull | **exported** | refused | gates and non-price indicators |
| I19 exit FILL | refused | refused | exit must be `{action: BLOCKED, status: Unknown}` |
| **I20** exit status Known, action BLOCKED | **exported** | refused | same |
| I21-I25, I30-I33 | refused | refused | mode, provenance, receipt kind / fixture flag, action set, clock, schema, keys |
| I26 future price | refused | blocked `PF-FUTURE` | Doodle's policy: a price problem is logged and blocked, not a file-level abort |
| I27-I29 NaN / negative / garbage price | refused | blocked `PF-VALUE` | same |

Every row is also checked to have an explanation that starts with its own action and clock. Out of scope: the bridge
does not recompute decisions from bars. It proves the tape is internally consistent and copied faithfully, not that
Lesson 001's decisions are right.

## Decision-time price policy (PROPOSED, spec 1.1.9; pin 1.1.8 unchanged)

Source: Doodle's `trading/proposals/ReplayBridge-001-price-freshness.md`, sha256
`805f2681eca44e2cc45b586861da10be84aa3067817cdd4c000fc90076e3e246`. It replaces the first version (sha256
`fc50b0e1d803fbe9b3f376beb05a7a88a1cc4cec2bb76189f44f60b4f2547619`). The policy is still PROPOSED. Eugene's answers of
2026-11-28 14:39 Berlin reached us through Soodle, not from Eugene directly. Doodle owns the values.

- Age = `decision_utc - as_of_utc` in exact decimal seconds, both converted to UTC first. Limits are inclusive.
- Values (adjustable, in `config/replay_bridge_price_policy.json`, never hard-coded): `bar_close_1m` 0 s,
  `quote_last` 900 s, future tolerance 0 s, and for DELAYED quotes: max declared delay 900 s, raw-age ceiling
  1200 s, adjusted-age maximum 300 s.
- Checks, first failure wins: PF-KIND (unknown kind; then a bar with any delay field), PF-STATUS, PF-TIME-MISSING,
  PF-TIME-NAIVE, PF-TIME-INVALID, PF-CALENDAR, PF-CLOSED, PF-FUTURE, PF-SESSION, PF-DELAY-DECL (invalid
  declaration; then adjusted age < 0), PF-STALE, PF-VALUE, PF-BIDASK. `age_seconds` is recorded whenever both
  instants parse; `delay_seconds` and `adjusted_age_seconds` whenever they can be computed.
- **DELAYED class, `quote_last` only.** It applies only when the price explicitly declares `delayed: true` and an
  integer (not bool) `delay_minutes` from 1 to 15 (`delay_seconds` = minutes x 60, the MD-08 / MD-14 shape). A row
  that carries only `delay_seconds` is accepted only as an integer multiple of 60 from 60 to 900; both fields present
  and disagreeing, a non-finite or out-of-range delay, `delayed: false` with a delay field, or a non-boolean
  `delayed` is `PF-DELAY-DECL`. Adjusted age = raw age - delay_seconds: below 0 is `PF-DELAY-DECL`; over 300 s or
  raw age over 1200 s is `PF-STALE`. An undeclared quote (or `delayed: false` alone) is REALTIME, limited to
  900 s, with no delay allowance. A bar with `delayed` or any delay field is `PF-KIND`.
- A DELAYED price is never relabeled or promoted to REALTIME. Its label, delay, raw age and adjusted age appear
  in `price_accept_log.csv` / `price_block_log.csv` (`label`, `delay_seconds`, `age_seconds`,
  `adjusted_age_seconds`) and in the labeled companion CSV, never in `decision_tape.csv`. In the logs,
  `max_age_seconds` is the raw ceiling that applies (DELAYED: 1200).
- No forward fill, no last-good substitution, no promotion. A SIGNAL row with a blocked price becomes BLOCKED with
  both signal flags 0 and is logged. A price problem never aborts the export and never raises on a null.
- `price_kind` is `bar_close_1m` for the frozen Lesson 001 decisions (identified by their sha256 `659175c6...04f6`,
  the archive's own `decisions.json`); any other tape must declare `indicators.price.price_kind`, or every price
  is blocked `PF-KIND`.
- Frozen archive result: 391 rows, counts 375 / 15 / 1 / 0, `decision_tape.csv` byte-identical, one block
  (row 0, 09:30, `PF-STATUS`), 390 accepts all `label=BAR` at age 0. The receipt gains `price_accepts` and
  `price_blocks.by_label`.
- Tests PF-01 to PF-46 (all TEST FIXTURE, SYNTHETIC) are in `tests/test_replay_bridge.py`, plus extra DELAYED cases
  (bad declarations, adjustable policy, check order, export-level behavior).
- Not changed here: repo `MD-19-STALE-quote` in `marketdata.py` uses 20 min at decision time, while spec R01 says
  15 min. Doodle flagged the divergence and it stays unresolved; this PR leaves it alone.
- Not implemented: the doc's pack `sources_terms` cross-check (MD-14: the field's `delay_minutes` must be at least
  the source's declared N). The Lesson 001 tape carries no pack terms, so there is nothing to check yet.

## Verify identity pins and the PROPOSED policy (Sol, static review)

Sol noted, and this was checked against the code at 657391c (Verified), that `verify` originally compared an
output folder only to its own SHA256SUMS and receipt, so it had no independent trusted identity for the archive,
the code or the policy; and that `load_policy` records whatever `status` the file declares, while
`--price-policy` accepts any path. The second point is unchanged: there is still no approval mechanism, and
`--price-policy` still accepts any path. The first point is addressed in this commit: the CLI `verify` default
compares archive, decisions, tape, code and the default policy file to the repo pins; optional `--expect-*`
flags add caller-supplied pins; and even `--self-consistency-only` still recomputes counts from the files,
cross-checks the plain tape against the labeled CSV, compares the code hash to the running source, and compares
a configured policy file, never the receipt's path, and reports the pins that were actually checked or skipped.
In pinned official mode, the labeled CSV, both price logs and the receipt policy fields are recomputed from the
pinned archive plus that configured policy and compared byte-for-byte, so an official folder that is only
internally consistent after an edit-and-resign no longer passes. The Python `verify()` API stays unpinned by
default so test-bundle folders can still be checked.

## Zoodle baseline at f0be2bc0 (R13–R16, O3c, X2a/X2b/X2c, PP18, A6/A7)

Zoodle's report for head `f0be2bc0` (sha256 starts `cc776683`, ends `f97f`) is the baseline this commit folds in. Mapping (finding → test):

- **R13** edited receipt archive hash + regenerated SUMS → `test_r13_edited_archive_hash_is_refused_after_resign`
- **R14** edited receipt policy numbers + regenerated SUMS → `test_r14_edited_policy_numbers_are_refused_after_resign`
- **R15** edited per-action / block counts + regenerated SUMS (the doc already claimed a refuse) →
  `test_r15_edited_per_action_counts_are_refused_after_resign`
- **R16** edited receipt code hash + regenerated SUMS → `test_r16_edited_code_hash_is_refused_after_resign`
- **O3c** edited and re-signed plain `decision_tape.csv` → `test_o3c_plain_tape_is_cross_checked_against_labeled`
- **X2a** forged SIGNAL_BEAR in both CSVs, rehashed → `test_x2a_forged_action_in_both_csvs_is_refused`
- **X2b** unofficial folder relabelled as the official fixture, rehashed →
  `test_x2b_official_fixture_relabel_is_refused`
- **X2c** genuine other-archive repack, same frozen tape → `test_x2c_other_archive_same_tape_is_refused_against_the_pin`
  (plus `test_cli_verify_defaults_to_repo_pins`)
- **PP18** `bar_close_1m` written as the string `"0"` → `test_pp18_string_policy_section_exits_2`
- **A7** SIGTERM mid-write cleans the temp folder, exit 2 → `test_a7_sigterm_cleans_up_the_temp_folder`
- **A6** leftover `.*.partial-*` (SIGKILL cannot be caught) is refused by `verify` and by `write_output`, and
  ignored beside a real target → `test_a6_leftover_partial_folder_is_refused_and_ignored`


## Zoodle gate at 72078004 (S12, E10/S4/SD2, S8/S9, S1/S3/S5/S6, Pb1–Pb4)

Zoodle's report for head `72078004` (sha256 starts `c4160f0e`, ends `299d`) is a FAIL on official folders that
were edited and fully re-signed. Every named re-run item from `f0be2bc0` was already fixed; these new probes
are folded in here. Mapping (finding → test):

- **S12** receipt `price_policy.status` PROPOSED → APPROVED (the not-owner-approved notice must still come from
  the configured policy) → `test_s12_approved_status_is_refused_and_notice_comes_from_config`
- **E10 / S4 / SD2** a BAR row relabelled DELAYED in the labeled CSV, accept log and receipt →
  `test_e10_s4_sd2_bar_relabeled_delayed_is_refused`
- **S9** block reason PF-STATUS → PF-STALE in the labeled CSV → `test_s9_block_reason_changed_in_labeled_csv_is_refused`
- **S8** the same reason change in the block log, labeled CSV and receipt →
  `test_s8_block_reason_changed_in_log_and_receipt_is_refused`
- **S1 / SD1** labeled `price_age_seconds` edited → `test_s1_sd1_labeled_age_cell_edited_is_refused`
- **S3** labeled `price_kind` edited → `test_s3_price_kind_edited_is_refused`
- **S5 / SD4** block-log `as_of_raw` edited → `test_s5_sd4_as_of_timestamp_edited_is_refused`
- **S6 / SD7** accept-log `age_seconds` edited → `test_s6_sd7_accept_log_age_edited_is_refused`
- **Pb1–Pb3** receipt policy path/status/sha/numbers changed to an alternate identity →
  `test_pb1_alternate_policy_identity_is_refused_in_pinned_mode`
- **Pb4** genuine official-archive export made with an alternate loose ADOPTED policy →
  `test_pb4_official_export_with_alternate_policy_is_refused`

**S11** (`generated_at_utc` and `command` edited on an otherwise official folder) is run-specific metadata
produced at export time. It is not part of the official sidecar projection and is not pinned.

## Comparison and quote-evidence principles

- Sequence comparisons cover the full sequence and its length, never a prefix only (`zip(..., strict=True)`
  plus an explicit row-count check; a labeled CSV that is only a matching prefix of the tape is refused by
  `test_verify_compares_full_tape_not_a_prefix`).
- OHLCV with equal bid and ask (QSTrader-style) does not count as quote evidence. The accepted price is
  always `price.value`. A present bid/ask pair is only a PF-BIDASK validity check (missing, non-positive,
  or inverted), not a quote source, and a locked market (`bid == ask`) is not quote evidence.

## Conflicts between the fix list and Doodle's policy
 (the policy wins on freshness)

- Fix 1 asked for label columns on every CSV row; the policy requires `decision_tape.csv` to stay byte-identical.
  The labels are in `decision_tape_labeled.csv`, `price_block_log.csv` and `price_accept_log.csv`;
  `decision_tape.csv` has none. Consequence: a DELAYED accepted price is exported in `decision_tape.csv` like any other
  accepted price, and only the sidecars say DELAYED.
- The block and accept logs have Doodle's columns followed by the 4 label columns. The labeled companion CSV also
  carries `price_label`, `price_delay_seconds` and `price_adjusted_age_seconds` (an addition beyond the doc).
- The earlier draft aborted the export when a signal had no usable price; the policy lowers that row to BLOCKED and
  logs it. A future-dated or invalid price is now a logged block instead of an abort.
- PF-34 says `price_kind` is set for the frozen archive. The bridge keys this on the frozen `decisions.json`
  sha256, so the offline suite can use the vendored copy in `tests/fixtures/lesson001_v3/` instead of the 4.5 MB
  archive. The CLI still accepts only the pinned archive.
- The doc says the numbers live once in `market_data_rules.yaml` (proposed). This PR does not touch the spec pin,
  so they live in `config/replay_bridge_price_policy.json` until Doodle's 1.1.9 lands.

## Fixture

`tests/fixtures/lesson001_v3/decisions.json` and `receipt.json` are byte copies of the two members of the frozen v3
archive (sha256 `659175c6...04f6` and `5e0d3ef5...1c91`). SYNTHETIC: fixed invented QQQ-like values.
