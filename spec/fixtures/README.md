# fixtures/ (spec 1.1.5)

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
