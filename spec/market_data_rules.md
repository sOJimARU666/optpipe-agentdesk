# Market data rules v1.1.8 (market_snapshot packs, PAPER ONLY)

Human-readable companion to `market_data_rules.yaml` (if the two disagree, the YAML wins). Schema:
`market_snapshot.schema.json`. Validator and CLI: `validate_market_snapshot.py`. Reference loader:
`market_data_loader.py`. Example pack: `fixtures/market_data/valid/2026-10-20/` (spec 1.1.8, fictional EXAMPLE data,
built by `build_market_data_fixtures.py`). The 1.1.7 example and cases are in `fixtures/market_data_v1_1_7/` (built by
`build_market_data_fixtures_v1_1_7.py`, byte-identical to spec 1.1.7). Tests: `test_market_snapshot_v1_1_7.py` (1.1.7
packs) and `test_market_snapshot_v1_1_8.py` (1.1.8 packs).

Spec 1.1.8 accepts packs with `schema_version` "1.1.7" or "1.1.8". A pack uses one version in every file. A 1.1.7
pack validates exactly as it did in spec 1.1.7: same checks, same error strings (proven in `test_release_v1_1_8.py`
against the frozen 1.1.7 validator and loader). The 1.1.8 additions are listed in "Spec 1.1.8" below.

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
  catalysts.json                 1.1.8 packs only, optional: kind catalyst_log (catalysts + ticker_context, MD-15)
```

Nothing else is allowed: no other names or extensions (`.py`, `.sh`, extra `.md` or `.json`), no sub-directories
(`__pycache__` included), no dotfiles, no symlinks (the date dir itself and its ancestors included), no FIFOs,
sockets or devices, no executable bit, nothing over 1 MiB. Every file except `pack.sha256` is listed in
`pack.sha256`, and the manifest lists nothing else. Write it with `LC_ALL=C sort` order (order is not checked).
The manifest is called `pack.sha256`, not `MANIFEST.sha256`. That name is a judgement call: the spec release
manifest command excludes every `MANIFEST.sha256`, and the fixture packs have to ship in the release.

## Document shape

Top level, in every file: `schema_version` ("1.1.7" or "1.1.8"), `kind`, `pack_id`, `pack_built_at` (ISO 8601 with a UTC
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
| MD-01 | layout and file safety (see Layout; catalysts.json only in a 1.1.8 pack) | MD-01-DIR (missing / not a dir), MD-01-UNREADABLE, MD-01-SYMLINK, MD-01-NOT-REGULAR, MD-01-NAME, MD-01-EXEC, MD-01-SIZE, MD-01-DATE (dir name = index.date = NY date of pack_built_at) |
| MD-02 | per-date manifest | MD-02-MISSING, MD-02-FORMAT, MD-02-PATH (`/`, `\`, `..`, absolute, dotfile), MD-02-DUPLICATE, MD-02-EXTRA (file not listed), MD-02-ABSENT (listed, not present), MD-02-INDEX (index entries vs ticker files), MD-02-HASH |
| MD-03 | UTF-8 JSON object, valid against the schema, `kind` matches the file name | MD-03-JSON, MD-03-SCHEMA |
| MD-04 | times have an offset, parse, and no as_of is after pack_built_at (1.1.8: also sources[].as_of, and analyst action dates vs the pack date) | MD-04-TIME-NAIVE, MD-04-TIME-INVALID, MD-04-FUTURE |
| MD-05 | a quote field marked OK is at most 20 minutes old | MD-05-STALE |
| MD-06 | null or missing value = UNKNOWN; UNKNOWN carries no value; a value needs a source and as_of | MD-06-UNKNOWN, MD-06-VALUE, MD-06-UNSOURCED |
| MD-07 | earnings OK needs >= 2 independent sources, else UNCONFIRMED (1.1.7 packs: distinct registrable domains; 1.1.8 packs: distinct (origin, origin_entity)); UNCONFIRMED is only for earnings fields; 1.1.8: an issuer channel must be origin issuer, and an issuer's origin_entity is the ticker | MD-07-UNCONFIRMED, MD-07-STATUS, MD-07-ORIGIN |
| MD-08 | delayed true needs delay_minutes >= 1; delayed false has none | MD-08-DELAY |
| MD-09 | no e-mail address anywhere in a pack file, summary.md included (not even example.com) | MD-09-EMAIL |
| MD-10 | street tickers are ideas only: candidate_status must be idea (default) | MD-10-STREET-CANDIDATE |
| MD-11 | ticker files (and 1.1.8 catalysts.json, date included) agree with the index (pack fields, ticker, watchlist, candidate_status, file name); tickers unique | MD-11-PACK, MD-11-TICKER, MD-11-DUPLICATE |
| MD-12 | optional `sources_terms` in index.json: if present, every source id used (1.1.8: catalysts.json too) is listed with its terms basis; 1.1.8: a `sources_not_used` name is not also a `sources_terms` source | MD-12-SOURCE, MD-12-CONFLICT |
| MD-13 | 1.1.8: price and option-chain fields with a value state `as_of_basis`; `fetch_minus_delay` needs delayed true | MD-13-MISSING, MD-13-DELAY |
| MD-14 | 1.1.8: a source declared delayed N minutes in `sources_terms` makes every field from it delayed with delay_minutes >= N | MD-14-DELAY |
| MD-15 | 1.1.8 catalysts.json: dates, statuses, sources and ticker_context | MD-15-UNKNOWN, MD-15-VALUE, MD-15-UNSOURCED, MD-15-UNCONFIRMED, MD-15-TIME-NAIVE, MD-15-TIME-INVALID, MD-15-DATE, MD-15-DUPLICATE |
| MD-00 | a loader or validator crash is an error, never a pass | MD-00-CRASH |

Staleness: `staleness_limit_minutes: 20` is defined once, in `market_data_rules.yaml`, and the code reads it from
there. A quote field (last_price, iv, iv_rank, implied_move and the option-liquidity numbers) is STALE if and only
if `pack_built_at - as_of > 20 minutes`. Both times are compared as exact instants, so any UTC offset works. iv_rank
UNKNOWN (no free IV-rank source) is normal and is not an error.

**MD-05 is not R01.** The pack's STALE limit is 20 minutes, measured at `pack_built_at` (the time the pack was
actually built). R01_QUOTE_FRESHNESS is 15 minutes, measured at decision time in the session record. A quote can pass
MD-05 (OK in the pack) and fail R01 later (VETO), by design: a pack is not a decision, and R01 decides.

## CLI

```
python validate_market_snapshot.py [PATH ...]    # no PATH: the example pack
```

A PATH that is a directory, a symlink or has no suffix is loaded as a date dir: all rules MD-00..MD-15 run through
the loader. Any other PATH is one JSON document, and only the document rules (MD-03..MD-10, MD-13, MD-15 and the
document parts of MD-07 / MD-12) run.
Exit codes: 0 = all valid. 1 = rule violations. 2 = could not read or parse (MD-01-DIR, MD-01-UNREADABLE,
MD-03-JSON) or a crash (MD-00-CRASH). With several paths, the worst code wins.

## Loader API (`market_data_loader.py`)

`load_pack(date_dir) -> PackResult` never raises. It returns `ok`, `date`, `index`, `snapshots` (ticker -> doc),
`summary_md`, `catalysts` (1.1.8 catalysts.json, or None), `errors` and `exit_code()`. It fails closed: on any
error, `ok` is False, `index`, `summary_md` and `catalysts` are None, and `snapshots` is `{}`.
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

## Spec 1.1.8 (packs with schema_version "1.1.8")

Every file of a 1.1.8 pack says `"schema_version": "1.1.8"`. Everything above still applies, with these additions.
The new fields are never required of a 1.1.7 pack. A 1.1.7 document that carries one still fails MD-03-SCHEMA,
exactly as in spec 1.1.7 (exact back-compat wins; to use the new fields, emit the pack as 1.1.8).

1. **Source basis `robots_only_no_terms`** (`sources_terms[].basis`): the site has no terms page and only robots.txt
   permits access (Finviz is the example). Such an entry has no `terms_url` (MD-03-SCHEMA if it does).
2. **`sources_not_used`** (optional, index.json): `[{name, domain, reason, note?}]`, reason one of `terms_forbid`,
   `login_required`, `paid`, `contact_email_required`, `other` (`other` needs a note). Names and domains only: no
   e-mail address anywhere (MD-09-EMAIL still applies; `contact_email_required` names the reason, never the address).
   A name that is also a used `sources_terms` source is MD-12-CONFLICT.
3. **`as_of_basis`** (per field): `quote_time`, `last_trade`, `fetch_minus_delay` or `published`. Required
   (MD-13-MISSING) on the price and option-chain fields when they carry a value: last_price, iv, implied_move,
   open_interest, volume, bid, ask, spread, spread_pct_of_mid and ticker_context prior_close (YAML
   `as_of_basis.required_for`). Optional on iv_rank and the earnings fields (use `published` there).
   **`last_trade` is accepted as a conservative fallback** when the source shows no quote time: a last-trade time is
   never newer than the quote, so the age is over-stated, never under-stated. `fetch_minus_delay` (fetch time minus
   the source's delay) needs `delayed: true` with `delay_minutes` (MD-13-DELAY).
4. **Declared delay** (MD-14-DELAY): a `sources_terms` entry may declare `delayed: true, delay_minutes: N`
   (delay_minutes is required iff delayed is true; the schema enforces that). Every field whose `source` is that id
   must then say `delayed: true` with `delay_minutes >= N`. The 1.1.7 example had chain fields from a delayed source
   marked `delayed: false`; the 1.1.8 example fixes that and the `undeclared_delay` case catches it. The declaration
   lives in the schema (one place per source); the cross-file comparison is a validator rule, because JSON Schema
   cannot compare a field with an entry in another file. A source not declared delayed is not checked.
5. **Earnings independence by ORIGIN, not domain** (MD-07, 1.1.8 packs; 1.1.7 packs keep the domain rule). Every
   `sources[]` entry has `origin` (issuer, exchange, broker, data_vendor, news, other) and `origin_entity` (for an
   issuer: its ticker; otherwise a name such as the vendor's). OK needs >= 2 entries whose (origin, origin_entity)
   differ (entity compared case-insensitively). An issuer's release counts as ONE source wherever it is read: the IR
   site, GlobeNewswire, PR Newswire, Business Wire or gcs-web.com. A source whose URL is on one of those domains
   (YAML `earnings.issuer_channel_domains`) or whose host starts with `ir.`, `investor.` or `investors.` must say
   origin issuer, and in a ticker file an issuer's origin_entity must be that ticker (MD-07-ORIGIN). IR sites on other
   hosts cannot be recognised by URL; Oodle must tag them issuer (judgement call).
6. **earnings_time** stays UNCONFIRMED unless two independent sources (as above) give the session. A BMO / AMC
   session *inferred* from a conference-call time is not a source: it goes in `summary.md` only, never into
   `earnings_time`.
7. **catalysts.json** (`kind: catalyst_log`, MD-15), optional, 1.1.8 packs only, listed in `pack.sha256`. Header
   `schema_version`, `pack_id`, `pack_built_at`, `data_label` and `date` equal index.json (MD-11-PACK).
   `catalysts[]`: `ticker` (null = macro / market-wide), `catalyst_type` (earnings, fomc, macro_release,
   investor_day, conference, fda, analyst_action, other), `event_date` (YYYY-MM-DD, NY date, or null), optional
   `event_time` (ISO 8601 with a UTC offset; absent or null = date-only), `status` (CONFIRMED, UNCONFIRMED, UNKNOWN),
   `sources` (the 1.1.8 source refs, same origin rule) and an optional `note`.
   - event_date null if and only if status UNKNOWN (MD-15-UNKNOWN / MD-15-VALUE).
   - A dated entry needs at least one source (MD-15-UNSOURCED).
   - CONFIRMED needs >= 2 sources with distinct (origin, origin_entity) (MD-15-UNCONFIRMED).
   - event_time must have an offset (MD-15-TIME-NAIVE, MD-15-TIME-INVALID), and its NY date must be event_date;
     event_time without event_date, or an event_date that is not a real date, is MD-15-DATE.
   - Sources get MD-07-ORIGIN (with the entry's ticker) and MD-04 on their as_of.
   Optional `ticker_context[]`: `{ticker, sector?, prior_close?, analyst_actions?}`, one entry per ticker
   (MD-15-DUPLICATE). `prior_close` is a normal sourced field (MD-04, MD-06, MD-08, MD-13, MD-14) but never STALE
   under MD-05 (a prior close is not a live quote). `analyst_actions[]`: `{firm, action (upgrade, downgrade,
   initiate, reiterate, target_raise, target_cut, other), from, to, date, source}`; a date after the pack's NY date is
   MD-04-FUTURE. A catalyst or context ticker need not be in index.json (macro and context entries are allowed).
   The loader allows the name `catalysts.json` only when index.json says 1.1.8 (in a 1.1.7 pack it is MD-01-NAME,
   as in spec 1.1.7). It peeks index.json before the scan and re-checks the verified index afterwards.
8. **MD-05 vs R01**: see Staleness above (20 minutes at pack_built_at vs 15 minutes at decision time).

### Judgement calls (1.1.8)

10. The 1.1.7 schema defs, rules and messages are untouched; 1.1.8 documents use parallel `*V118` defs plus
    `CatalystLog`, selected by `schema_version`. A 1.1.7 document carrying a 1.1.8 field fails MD-03-SCHEMA.
11. MD-14 is a validator rule over a schema-declared delay (point 4). It checks fields with a value only, and a
    declared delay of N accepts a field delay of N or more.
12. `origin_entity` for an issuer is the ticker symbol, so the check is mechanical. For a macro catalyst (ticker
    null) an issuer's entity is free text (for example the agency).
13. Sources' `as_of` is now time-checked (MD-04) in 1.1.8 documents. 1.1.7 documents keep the 1.1.7 behaviour (not
    checked).
14. ticker_context lives in catalysts.json, not in the ticker files, so the ticker-file shape stays the 1.1.7 shape
    plus as_of_basis.
