# Market data rules v1.1.7 (market_snapshot packs, PAPER ONLY)

Human-readable companion to `market_data_rules.yaml` (if the two disagree, the YAML wins). Schema:
`market_snapshot.schema.json`. Validator and CLI: `validate_market_snapshot.py`. Reference loader:
`market_data_loader.py`. Example pack: `fixtures/market_data/valid/2026-10-20/` (fictional EXAMPLE data, built by
`build_market_data_fixtures.py`). Tests: `test_market_snapshot_v1_1_7.py`.

Producer: Oodle (read-only market-data agent). Consumer: the app, which reads pack files only and never fetches
(Sol R6). A pack never invents data: a value nobody read is `null` with status `UNKNOWN`. Pack data is an idea
source only; R01-R13 in a session record still decide everything.

## Layout

```
market-data/<YYYY-MM-DD>/        the America/New_York date of pack_built_at
  pack.sha256                    per-date manifest, sha256sum format "<64 lowercase hex>  <name>", one line per file
  index.json                     kind pack_index: pack header + one entry per ticker (+ optional sources_terms)
  <TICKER>.json                  kind ticker_snapshot, one per index entry (name pattern ^[A-Z][A-Z0-9.-]{0,9}\.json$)
  summary.md                     optional human summary (UTF-8, no e-mail addresses)
```

Nothing else is allowed: no other names or extensions (`.py`, `.sh`, extra `.md` or `.json`), no sub-directories
(`__pycache__` included), no dotfiles, no symlinks (the date dir itself and its ancestors included), no FIFOs,
sockets or devices, no executable bit, nothing over 1 MiB. Every file except `pack.sha256` is listed in
`pack.sha256`, and the manifest lists nothing else. Write it with `LC_ALL=C sort` order (order is not checked).
The manifest is called `pack.sha256`, not `MANIFEST.sha256`. That name is a judgement call: the spec release
manifest command excludes every `MANIFEST.sha256`, and the fixture packs have to ship in the release.

## Document shape

Top level, in every file: `schema_version` ("1.1.7"), `kind`, `pack_id`, `pack_built_at` (ISO 8601 with a UTC
offset; naive is an error), `data_label` (EXAMPLE / TEST / MARKET). Ticker files add `ticker`, `watchlist`
(eugene / street / fixture), optional `candidate_status` (idea / candidate, default idea), `last_price`,
`option_liquidity` (`summary` and/or `contracts`), `iv`, `iv_rank`, `implied_move`, `earnings_date` and
`earnings_time` (BMO / AMC / unknown). `additionalProperties` is false everywhere, so account or personal fields
cannot be added.

Every data field is `{value, source, as_of, delayed, delay_minutes, status, note?}`:
`delay_minutes` is required when `delayed` is true and absent otherwise. `status` is one of OK / STALE / UNKNOWN /
UNCONFIRMED. Earnings fields also carry `sources: [{source, url, as_of}]`.

## Rules (MD-xx)

| id | rule | sub-ids |
|---|---|---|
| MD-01 | layout and file safety (see Layout) | MD-01-DIR (missing / not a dir), MD-01-UNREADABLE, MD-01-SYMLINK, MD-01-NOT-REGULAR, MD-01-NAME, MD-01-EXEC, MD-01-SIZE, MD-01-DATE (dir name = index.date = NY date of pack_built_at) |
| MD-02 | per-date manifest | MD-02-MISSING, MD-02-FORMAT, MD-02-PATH (`/`, `\`, `..`, absolute, dotfile), MD-02-DUPLICATE, MD-02-EXTRA (file not listed), MD-02-ABSENT (listed, not present), MD-02-INDEX (index entries vs ticker files), MD-02-HASH |
| MD-03 | UTF-8 JSON object, valid against the schema, `kind` matches the file name | MD-03-JSON, MD-03-SCHEMA |
| MD-04 | times have an offset, parse, and no as_of is after pack_built_at | MD-04-TIME-NAIVE, MD-04-TIME-INVALID, MD-04-FUTURE |
| MD-05 | a quote field marked OK is at most 20 minutes old | MD-05-STALE |
| MD-06 | null or missing value = UNKNOWN; UNKNOWN carries no value; a value needs a source and as_of | MD-06-UNKNOWN, MD-06-VALUE, MD-06-UNSOURCED |
| MD-07 | earnings OK needs >= 2 sources with distinct registrable domains, else UNCONFIRMED; UNCONFIRMED is only for earnings fields | MD-07-UNCONFIRMED, MD-07-STATUS |
| MD-08 | delayed true needs delay_minutes >= 1; delayed false has none | MD-08-DELAY |
| MD-09 | no e-mail address anywhere in a pack file, summary.md included (not even example.com) | MD-09-EMAIL |
| MD-10 | street tickers are ideas only: candidate_status must be idea (default) | MD-10-STREET-CANDIDATE |
| MD-11 | ticker files agree with the index (pack fields, ticker, watchlist, candidate_status, file name); tickers unique | MD-11-PACK, MD-11-TICKER, MD-11-DUPLICATE |
| MD-12 | optional `sources_terms` in index.json: if present, every source id used is listed with its terms basis | MD-12-SOURCE |
| MD-00 | a loader or validator crash is an error, never a pass | MD-00-CRASH |

Staleness: `staleness_limit_minutes: 20` is defined once, in `market_data_rules.yaml`, and the code reads it from
there. A quote field (last_price, iv, iv_rank, implied_move and the option-liquidity numbers) is STALE if and only
if `pack_built_at - as_of > 20 minutes`. Both times are compared as exact instants, so any UTC offset works. iv_rank
UNKNOWN (no free IV-rank source) is normal and is not an error.

## CLI

```
python validate_market_snapshot.py [PATH ...]    # no PATH: the example pack
```

A PATH that is a directory, a symlink or has no suffix is loaded as a date dir: all rules MD-00..MD-12 run through
the loader. Any other PATH is one JSON document, and only the document rules MD-03..MD-10 run.
Exit codes: 0 = all valid. 1 = rule violations. 2 = could not read or parse (MD-01-DIR, MD-01-UNREADABLE,
MD-03-JSON) or a crash (MD-00-CRASH). With several paths, the worst code wins.

## Loader API (`market_data_loader.py`)

`load_pack(date_dir) -> PackResult` never raises. It returns `ok`, `date`, `index`, `snapshots` (ticker -> doc),
`summary_md`, `errors` and `exit_code()`. It fails closed: on any error, `ok` is False, `index` and `summary_md` are
None, and `snapshots` is `{}`.
`field_usable(field)` is True only for status OK with a non-null value, and `usable_value(field)` returns the value
or None. The loader is read-only and makes no network calls. It lstat-checks every entry and never opens a rejected
entry. It opens files relative to the date dir's fd with O_NOFOLLOW and O_NONBLOCK, fstat-checks them, caps the size,
and hashes the same bytes it parses.

Session mapping (into session.schema.json `Field.status`): OK -> ok, STALE -> stale, UNKNOWN -> missing,
UNCONFIRMED -> missing. Only OK feeds R01-R13. The app still applies R01's own 15-minute quote freshness to the
session record.

## Judgement calls

1. Staleness is inclusive: exactly 20:00 old is OK, and 20:00 plus any fraction of a second is STALE. This is the
   same convention as R01 (exactly 15:00 is fresh) and W06.
2. Marking a field STALE early is allowed (conservative). Marking it OK when it is late is an error.
3. The two-source rule applies to both earnings_date and earnings_time. The registrable domain is the last two host
   labels with `www.` dropped, so `.co.uk`-style suffixes collapse to one domain. That fails closed.
4. Earnings fields are calendar facts, so they are never STALE under MD-05.
5. `ticker` is an identity, not a sourced field.
6. The executable bit is rejected even on an allowed name. A script can never sit in a date dir.
7. The per-date manifest is named `pack.sha256` (see Layout).
8. Symlinks, FIFOs, executable bits, oversized files and absolute manifest names are created at test time in
   tmp_path, not vendored. The vendored fixtures are plain 0644 files.
9. A non-conformant pack (no manifest, scripts, extra files) loads as an error with nothing usable. The loader never
   repairs or partly loads a pack.
