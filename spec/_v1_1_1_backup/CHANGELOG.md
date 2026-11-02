# Changelog

## 1.1.1 (2026-10-29)
`schema_version` accepts `1.0.0 | 1.1.0 | 1.1.1`. 1.1.1 has the same shape as 1.1.0; the new checks below apply to 1.1.1 records only, and 1.0.0 / 1.1.0 records validate exactly as before. The 1.1.0 spec is frozen in `_v1_1_0_backup/`.
- **INV-13 credit rounding (1.1.1):** a credit vertical (bull put / bear call) rounds the min-credit limit **up**, stop conditions **down** and target conditions **up**. Debit tickets are unchanged: limit down, stop up, target down. The directions are listed in `config.tick_rules.directions` and in the schema's `x-tick-rules`.
- **INV-14 vertical consistency (1.1.1):** for the four verticals, structure, price, stop and target bounds, and max_loss / max_profit / breakevens are computed from the legs at the limit (worst acceptable) price. Arithmetic is exact Decimal with a 0.01 tolerance on $ amounts. The stored values must match. See `risk_rules.md` → "Vertical consistency".
- New `verticals.py` holds the shared vertical math, used by the validator and by `risk_reference.payoff_max_loss`.
- **R04 (ruleset 1.1.1):** for a vertical whose legs match the strategy, R04 (and R11's ticket risk) uses the INV-14 computed max loss plus fees. Other structures keep the expiry-payoff scan. With the default fees of 0 the numbers are identical to 1.1.0.
- `risk_rules.md`: new "Tick rounding" and "Vertical consistency" sections; R04 row updated.
- The example is regenerated as 1.1.1. Its JPM bull call debit spread already satisfied INV-14 (max loss 310, max profit 690, breakeven 343.10), so no values changed.
- Tests: `test_verticals_v1_1_1.py` (90): pass and fail cases for every INV-14 clause on all four verticals, structure and stored-field checks, credit rounding through `validate()`, version gating, and R04.
- `conftest.py` also ignores `_v1_1_0_backup/`. `VERSION` is 1.1.1 and `MANIFEST.sha256` is regenerated.
- Open: INV-14 does not check the stop/target comparison operators (e.g. a credit stop should be `gte`). 1.1.0 records still force stop up / target down on credit tickets, which is wrong for credit spreads; use 1.1.1 for credit tickets.

## 1.1.0 (2026-10-29)
Backward compatible: every valid 1.0.0 record is still valid (tested against `_v1_backup/session.example.json` and the pipeline's v1 `session-2026-10-13.json`). The pre-merge spec is in `_v1_backup/`.

### Session schema (`session.schema.json`)
- `schema_version` is now an enum, `"1.0.0" | "1.1.0"`. This follows the existing three-part convention; it was `const "1.0.0"`.
- `legs[].contract.open_interest` is now an **IntegerField** (contracts), with a new `$defs.IntegerField`. 472.5 is rejected. `build_example.py` casts with `int(oi)`.
- Optional top-level `trace_ref {path, sha256}` (v1.1 only). `path` is a relative `*.trace.json` with no `..`; `sha256` is 64 lowercase hex characters.
- `runs[].phase` gains `closed`: before 04:00 or at/after 16:00 NY (13:00 on early closes), weekends and NYSE holidays. `x-market-hours.extended_open = 04:00`.
- New invariants:
  - INV-10: a run's phase must match the NY clock. `closed` is only allowed in 1.1.0. In 1.1.0, runs in the closed window MUST be `closed`; `afterhours` there stays valid only in 1.0.0 records.
  - INV-11: `trace_ref` is only allowed in 1.1.0. When validating a file: the path stays inside the record's directory, the file exists, and the sha256 matches.
- `validate_session.py` takes several files and checks INV-10/11 using the `risk_reference.Calendar`.

### Risk rules (`risk_rules.yaml` ruleset 1.1.0)
- R11 adds a sector cap (`max_sector_risk_usd`, `sector_map`). It is UNKNOWN if the cap is set and an underlying has no sector.
- R12_DEFINED_RISK_STRUCTURE (hard): defined-risk structures from `allowed_structures` only, legs must match the declared strategy, and a naked short is a VETO.
- R13_HOLD_PERIOD (hard): the hold and the DTE must be within [hold_min_days, hold_max_days], and the time stop must be no later than the earliest expiry close.
- `profiles.demo` is labelled "DEMO - fictional scenario, values relayed by agent Moodle, not confirmed by Eugene directly". Placeholder defaults (500 / 1000 / 2000 / 5) are unchanged.
- 23 new test cases (78 in total).

### Watcher (new)
- `watcher_rules.yaml`, `watcher_rules.md`, `watcher_reference.py` and `test_watcher_reference.py` add the alert-only W01–W07, polling, dedupe, escalation, cooldown and shadow EOD summary.

### Other
- `conftest.py` makes pytest ignore `_v1_backup/`.
- `bls_release_sources.md` documents the BLS calendar source.

- `VERSION` (contains `1.1.0`) and `MANIFEST.sha256` (`sha256sum` of every spec file except `.venv/`, `_v1_backup/`, `__pycache__/`, `.pytest_cache/` and the manifest itself).

### Merged from `options-pipeline/spec_patches/v1.1` (Eugene's decision, 2026-10-29 20:33 CET; 1.1.0 records only, 1.0.0 unchanged)
- `premarket_scans[]` and `global_snapshots[]`: one entry per run, same item shape as `premarket_scan` / `global_snapshot`. The singular objects stay required for v1 readers.
  - INV-12: the arrays are only allowed in 1.1.0. Within each array, `run_id` is unique and must reference an existing `runs[].run_id`. If the singular object is present, its `run_id` must be one of the array entries.
- Raw tick fields: `limit_price.raw_amount` and `Condition.raw_value`, each with `tick_size` and `rounding` (`down | up | nearest | none`). They apply to ticket entry/exit/stop/target rules and to watcher conditions, and are all-or-nothing (`dependentRequired`).
  - INV-13: the raw fields are only allowed in 1.1.0.
  - Tick rule (`risk_rules.yaml config.tick_rules`, schema `x-tick-rules`): `tick_size` must equal the rule's tick for the ticket's underlying at the raw price. Penny-pilot names use 0.01 below $3 and 0.05 at or above; other names use 0.05 / 0.10.
  - The rounded value must equal `round(raw, tick_size, rounding)`, compared exactly in Decimal so float noise such as 3.1006249999999995 is tolerated.
  - Required directions: a debit limit rounds down, stop conditions round up, target conditions round down. `none` means rounded == raw.
  - The penny-pilot list `[SPY, QQQ, IWM, JPM]` is a PLACEHOLDER copied from the pipeline config. Verify it against the Cboe/OCC list.
  - Open item: credit limits and entry/exit/watcher conditions are only checked for consistency; no direction is forced on them.
- The patch's "PROPOSAL" wording was not carried into any description.
- Example: adds `premarket_scans` / `global_snapshots` (run-premarket) and raw fields on the JPM ticket.
  - Limit 3.100625 → 3.10 (down, 0.05).
  - Stop 1.5125 → **1.52** (up, 0.01; was 1.50).
  - Target 6.05 → **6.05** (down, 0.05; was 6.00).
  - The `w-value` watcher was updated to match.
- `trace_ref` (above) stays merged. **Doodle accepted it as spec owner**, which supersedes the pipeline CHANGES.md label "Moodle (proposal only)".
- Tests: `test_schema_v1_1_ticks.py` (pipeline tick vectors, INV-12/13 positive and negative cases, a 1.0.0 record rejected when it carries 1.1.0-only fields).
- The pipeline's 1.1.0 preview (`session-2026-10-13.v1_1.json`, regenerated read-only into /tmp) now validates.
