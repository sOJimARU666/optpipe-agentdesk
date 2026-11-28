# Replay Bridge 001 (paper only, decision support only)

`src/optpipe/research/replay_bridge.py` projects the frozen Lesson 001 v3 decision tape (SYNTHETIC, archive sha256
`159de19c0fa74be03a46da954af92f8f1f164a62e0c439f0a57aa8bb1c3947a3`) into offline research CSVs. It has no broker,
fill model, data fetch, optimizer, entries, exits or P&L, and no new dependency (standard library plus the repo's
own `optpipe.nyse_calendar`). Optpipe owns the decisions. The bridge copies them and can only lower a signal to
BLOCKED, never raise one. Nothing here is a trade instruction.

Origin: Sol's standalone prototype `Optpipe-Replay-Bridge-001.zip` (sha256 `dd6a0815...fb220`; `replay_bridge.py`
sha256 `8bb3ed3c...056f`), with Zoodle's test report (PASS with gaps) and Noodle's review (ACCEPT WITH CONDITIONS)
folded in, plus Doodle's PROPOSED price-freshness policy.

## Run

```sh
python -m optpipe.research.replay_bridge export path/to/Optpipe-Lesson-001-Portability-v3.zip NEW_OUTPUT_DIR
python -m optpipe.research.replay_bridge verify NEW_OUTPUT_DIR
```

`export` refuses any archive other than the pinned one, refuses an existing output folder (even an empty one), and
refuses to run without a complete price policy (`--price-policy`, default `config/replay_bridge_price_policy.json`).
Every refusal exits 2 and leaves no output folder.

Output folder (written atomically: built in a hidden temp folder beside the target, then one rename; on any error
or interrupt the temp folder is removed, so the files land together or not at all):

| File | Content |
|---|---|
| `decision_tape.csv` | Sol's 10 columns. Byte-identical to Sol's evidence tape for the frozen archive (sha256 `039ee588...8de24`). |
| `decision_tape_labeled.csv` | The same rows plus `upstream_decision`, `price_kind`, `price_status` (OK / BLOCKED), `price_reason_code`, `price_age_seconds`, `price_max_age_seconds` and the labels `paper_only`, `decision_support`, `fixture_set`, `fixture_set_sha256`. |
| `price_block_log.csv` | One record per blocked decision-time price (header only when none): Doodle's 13 columns plus the same 4 label columns. |
| `bridge_receipt.json` | Archive, decisions and CSV hashes; counts (final and upstream); `price_policy` and `price_blocks`; `code_sha256` (sha256 of the bridge source file), the exact `command`, `python` version and `generated_at_utc`. |
| `SHA256SUMS` | sha256 of the four files above. |

`verify` refuses a folder with a missing or extra file, a SHA256SUMS mismatch, a receipt whose hashes or counts do
not match the CSVs, or a CSV row without the paper-only / decision-support labels.

## The seven fixes

1. **Labels.** Every row of `decision_tape_labeled.csv` and `price_block_log.csv` carries `paper_only=PAPER_ONLY`,
   `decision_support=DECISION_SUPPORT_ONLY_NOT_AN_ENTRY_OR_FILL`, `fixture_set` and `fixture_set_sha256`.
   `decision_tape.csv` keeps Sol's 10 columns unchanged, because Doodle's freshness policy requires it to stay
   byte-identical (see Conflicts).
2. **Inner tampers.** All 33 of Zoodle's inner controls are re-created in `tests/test_replay_bridge.py` and all are
   caught (table below). The 8 that passed before are now tape-integrity refusals.
3. **Evidence.** `SHA256SUMS` in every output folder; the receipt records the code hash, the command and the time.
4. **Tests.** Network guard (bridge runs in a process with the repo guard on; the only guard hit is the probe),
   existing output folder refused (empty and non-empty, contents untouched), signal with a missing price.
5. **Atomic output** plus negative controls for a missing receipt, a mismatched receipt (with and without
   regenerated SHA256SUMS), a receipt from another run, a tampered CSV, a stripped label and an extra file; and on
   the input side, a missing receipt member and a receipt digest / count mismatch.
6. **Price status.** `price_status`, `price_reason_code` and `price_age_seconds` in the labeled CSV.
7. **Age ceiling.** Doodle's PROPOSED decision-time price policy (below), parameterised in
   `config/replay_bridge_price_policy.json`. A missing file, a missing key, a null, a negative or non-finite value
   fails closed (exit 2, nothing written). TODO(Doodle): the values are PROPOSED for spec 1.1.9 and pending
   Eugene's answers; Doodle owns them.

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
`fc50b0e1d803fbe9b3f376beb05a7a88a1cc4cec2bb76189f44f60b4f2547619`.

- Age = `decision_utc - as_of_utc` in exact decimal seconds, both converted to UTC first. Limits are inclusive.
- Defaults (adjustable, in the policy file): `bar_close_1m` 0 s, `quote_last` 900 s, future tolerance 0 s.
- Checks, first failure wins: PF-KIND, PF-STATUS, PF-TIME-MISSING, PF-TIME-NAIVE, PF-TIME-INVALID, PF-CALENDAR,
  PF-CLOSED, PF-FUTURE, PF-SESSION, PF-STALE, PF-VALUE, PF-BIDASK. `age_seconds` is recorded whenever both instants
  parse.
- No forward fill, no last-good substitution, no promotion. A SIGNAL row with a blocked price becomes BLOCKED with
  both signal flags 0. A price problem never aborts the export and never raises on a null.
- `price_kind` is `bar_close_1m` for the frozen Lesson 001 decisions (identified by their sha256 `659175c6...04f6`,
  the archive's own `decisions.json`); any other tape must declare `indicators.price.price_kind`, or every price
  is blocked `PF-KIND`.
- Frozen archive result: 391 rows, counts 375 / 15 / 1 / 0, `decision_tape.csv` byte-identical, one block
  (row 0, 09:30, `PF-STATUS`).
- Tests PF-01 to PF-38 (all TEST FIXTURE, SYNTHETIC) are in `tests/test_replay_bridge.py`.
- Not changed here: repo `MD-19-STALE-quote` in `marketdata.py` uses 20 min at decision time, while spec R01 says
  15 min. Doodle flagged the divergence; this PR leaves it alone.

## Conflicts between the fix list and Doodle's policy (the policy wins on freshness)

- Fix 1 asked for label columns on every CSV row; the policy requires `decision_tape.csv` to stay byte-identical.
  The labels are in `decision_tape_labeled.csv` and `price_block_log.csv`; `decision_tape.csv` has none.
- `price_block_log.csv` has Doodle's 13 columns followed by the 4 label columns.
- The earlier draft aborted the export when a signal had no usable price; the policy lowers that row to BLOCKED and
  logs it. A future-dated or invalid price is now a logged block instead of an abort.
- PF-34 says `price_kind` is set for the frozen archive. The bridge keys this on the frozen `decisions.json`
  sha256, so the offline suite can use the vendored copy in `tests/fixtures/lesson001_v3/` instead of the 4.5 MB
  archive. The CLI still accepts only the pinned archive.

## Fixture

`tests/fixtures/lesson001_v3/decisions.json` and `receipt.json` are byte copies of the two members of the frozen v3
archive (sha256 `659175c6...04f6` and `5e0d3ef5...1c91`). SYNTHETIC: fixed invented QQQ-like values.
