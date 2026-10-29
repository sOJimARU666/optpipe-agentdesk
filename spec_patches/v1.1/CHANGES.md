# Session schema v1.1: proposal for Doodle

Status: **PROPOSAL**. Doodle's locked spec in `../trading/spec/` is unchanged and is still canonical.
These files are a patched **copy** (ruling 5): `make_spec_patch.py` builds them from the locked
files, and `scripts/make_spec_patch.py --check` fails if they drift.

| file | what it is |
|---|---|
| `session.schema.json` | v1.1 schema: Doodle's v1 schema plus the changes below |
| `session.example.json` | Doodle's example with `open_interest` written as integers |
| `*.diff` | exact unified diffs against the locked v1 files (the full patch) |

Both schemas must pass, and the tests check this (`tests/test_rulings.py::test_both_schemas_pass`):

- the written `session-<date>.json` is **v1** and validates with Doodle's `validate_session.py` and his v1 schema;
- `session-<date>.v1_1.json` validates with the same validator bound to this v1.1 schema;
- any v1 record also validates against v1.1, so the change is backward compatible.

## Schema changes (`session.schema.json.diff`)

| # | path | v1 | v1.1 | ruling |
|---|---|---|---|---|
| 1 | `properties.schema_version` | `const: "1.0.0"` | `enum: ["1.0.0", "1.1.0"]` | (versioning) |
| 2 | `runs[].phase` | enum without `closed` | adds `"closed"` with a description: before 04:00 or after the 16:00 close (13:00 on early-close days) NY, or a weekend / NYSE holiday | 6 |
| 3 | `properties.premarket_scans` (new) | single `premarket_scan` | array of `#/properties/premarket_scan`, one per run_id; `premarket_scan` is kept as the latest so v1 readers still work | 4 |
| 4 | `properties.global_snapshots` (new) | single `global_snapshot` | array of `#/properties/global_snapshot`, one per run_id; `global_snapshot` is kept as the latest | 4 |
| 5 | `properties.trace_ref` (new, optional) | (none) | `{path: ^[^/].*\.trace\.json$ (relative), sha256: ^[0-9a-f]{64}$}`, additionalProperties false | Moodle (proposal only) |
| 6 | option contract `open_interest` | `$ref NumberField` | `$ref IntegerField` ("OCC open interest (contracts, integer)") | 5 |
| 7 | `$defs.IntegerField` (new) | (none) | Field with `value: integer\|null` and `conflicts[].value: integer` | 5 |
| 8 | ticket `limit_price` | `amount` only | optional `raw_amount` (number ≥ 0), `tick_size` (> 0), `rounding` (down\|up\|nearest\|none) | 3 |
| 9 | watcher `Condition` | `value` only | optional `raw_value`, `tick_size`, `rounding` | 3 |
| 10 | top-level `description` | (text) | appends "v1.1 PROPOSAL (Poodle, spec_patches/v1.1/CHANGES.md)" | (none) |

Not changed: Field and Source objects, all invariants (INV-01..09), risk review shape, approval shape.

## Example changes (`session.example.json.diff`)

- Eight contract `open_interest.value` entries go from float to integer (`472.0` → `472`, `773.0` → `773`, `747.0`, `324.0`, `428.0`, …).
- The R05 review `measured` / `per_leg[].measured` and its `reason` go from `472.0` / `747.0` to `472` / `747`.
- Nothing else changes; `schema_version` stays `"1.0.0"`, so the example stays a valid v1 record.

## Suggested fix in Doodle's generator (not applied, because the spec is locked)

At `build_example.py` line 200, `num(row[p + "Openinterest"])` parses OI with `float(...)`.
To get integer OI, cast at the source, e.g.

```python
oi = None if oi is None else int(oi)        # after the num(...) unpack, before oi_f = F(oi, ...) (~line 204)
```

**Why the v1 example passes today:** with Draft 2020-12 semantics, `jsonschema` treats `472.0` as an
integer (any number with a zero fractional part), so the int/float mismatch is invisible to the validator.
The type still leaks downstream: pydantic models generated from `NumberField` produce floats, and the
R05 reason string prints "OI 472.0". IntegerField plus the `int()` cast make it explicit.

## How Poodle's code uses it

- The internal record is v1.1. `records/compat.to_v1` writes v1 by dropping `premarket_scans`,
  `global_snapshots`, `trace_ref` and the raw tick fields, mapping `closed` to `afterhours` (the true phase stays in
  the run summary), and setting `schema_version` to 1.0.0.
- `--append` reads the v1.1 preview, so per-run history survives.
- The models are generated from both schemas (`scripts/gen_models.py --check`).
