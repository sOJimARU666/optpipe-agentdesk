# fixtures/ (spec 1.1.8)

Small, vendored historical session records used by the back-compat tests (`test_invariants_v1_1_3.py`,
`test_release_v1_1_4.py`, `test_release_v1_1_5.py`). Tests resolve this directory relative to their own file
(`SPEC / "fixtures"`), so the spec runs the same from any checkout name or location. A missing fixture makes the tests FAIL, never skip.

| file | schema_version | origin |
|---|---|---|
| `session-2026-10-13.json` | 1.0.0 | the pipeline's 2026-10-13 session record (v1 shape), trimmed |
| `session-2026-10-13.v1_1.json` | 1.1.0 | the pipeline's 1.1.0 preview of the same session, trimmed; `trace_ref` points at the trace below |
| `session-2026-10-13.trace.json` | (trace, not a record) | trace of the 1.1.0 preview, trimmed; its sha256 is in the preview's `trace_ref` |
| `spec_patches_v1_1.session.example.json` | 1.0.0 | the pipeline's `spec_patches/v1.1` example (integer open_interest), copied as is |

Trimming (done once, outside the spec release): each option-chain slice keeps only the contracts that something
else in the record points at (legs, fills, field paths), JSON pointers into removed contracts are renumbered,
absolute box paths are cut to bare file names, files are written as compact JSON. No emails, no absolute paths.
Every record validates (schema + invariants, `trace_ref` hash included) under the current validator and under every
frozen validator in `_v1_1_2_backup/`, `_v1_1_3_backup/` and `_v1_1_4_backup/`. Never edit these by hand; never point a test at a
location outside this spec directory.

## market_data/ (spec 1.1.8)

Fictional EXAMPLE market-data packs (schema_version 1.1.8) for `test_market_snapshot_v1_1_8.py`. They are generated
deterministically by `build_market_data_fixtures.py`, and a test asserts the vendored bytes equal the builder output.
Regenerate them with `python build_market_data_fixtures.py`; never edit them by hand. Each case is `<case>/2026-10-20/`
(the date dir) and is a pack under the `market_data_rules.yaml` layout. Every file is a plain 0644 file with no symlinks.

| case | loader / CLI exit | ids | what is wrong |
|---|---|---|---|
| `valid` | 0 | - | nothing: the 1.1.8 EXAMPLE pack (JPM, WDC, XMPLA, XMPLB; as_of_basis on every price/chain field, declared delays, robots_only_no_terms, sources_not_used, catalysts.json with ticker_context) |
| `stale` | 1 | MD-05-STALE | JPM last_price OK at 20:01 old |
| `missing_value` | 1 | MD-06-UNKNOWN | JPM last_price null but OK |
| `malformed_json` | 2 | MD-03-JSON | WDC.json truncated (its hash matches) |
| `tampered_hash` | 1 | MD-02-HASH | JPM.json changed after the manifest was written |
| `path_traversal` | 1 | MD-02-PATH | manifest lists `../outside.json` (the file exists one level up) |
| `missing_manifest` | 1 | MD-02-MISSING, MD-01-NAME | no pack.sha256, plus a script and an extra .md |
| `missing_basis` | 1 | MD-13-MISSING | JPM last_price without as_of_basis |
| `undeclared_delay` | 1 | MD-14-DELAY | JPM's first contract bid says delayed false (the 1.1.7 example's bug) though its source is declared delayed 15 |
| `earnings_same_origin` | 1 | MD-07-UNCONFIRMED | JPM earnings_date OK, but the second source is the issuer's release re-published on a wire (origin issuer, JPM: one origin) |
| `catalysts_bad` | 1 | MD-15-UNCONFIRMED, MD-15-UNKNOWN, MD-15-TIME-NAIVE | catalysts.json: JPM earnings CONFIRMED on IR + a wire copy of the release (one origin); a CONFIRMED WDC investor_day without a date; a naive FOMC event_time |
| `catalysts_in_1_1_7` | 1 | MD-01-NAME, MD-02-EXTRA | a 1.1.7 pack carrying catalysts.json |

## market_data_v1_1_7/ (spec 1.1.7 packs)

The 1.1.7 EXAMPLE packs (cases `valid`, `stale`, `missing_value`, `malformed_json`, `tampered_hash`, `path_traversal`,
`missing_manifest`; same ids as in spec 1.1.7) for `test_market_snapshot_v1_1_7.py` and the frozen back-compat tests in
`test_release_v1_1_8.py`. Built by `build_market_data_fixtures_v1_1_7.py`; byte-identical to `fixtures/market_data/` of
the 1.1.7 release (`_v1_1_7_backup/`), which a test asserts. Never edit by hand.

Symlinked files, a symlinked date dir or ancestor, FIFOs, executable bits, oversized files and absolute or backslash
manifest names are created at test time in tmp_path.
