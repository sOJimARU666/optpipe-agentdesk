# Risk rules v1.1.4 (paper-only US options)

The machine-readable source of truth is `risk_rules.yaml`. It holds the config, definitions, rules, profiles, fixture and 78 test cases. `risk_reference.py` is the reference implementation, and `test_risk_reference.py` runs the YAML tests (`.venv/bin/python -m pytest -q`). If this document and the YAML disagree, the YAML wins.

## Principles
- **Deterministic.** Every rule is a pure function of the inputs and `evaluated_at`. Prices are compared with exact `Decimal` arithmetic, so a threshold hit exactly is included (≤ / ≥ pass).
- **Point-in-time.** A data Field is only usable if its `status` is `ok`, its value is not null, and its source was fetched at or before `evaluated_at`. A Field that is `missing`, `conflicting`, or not yet fetched cannot be used. A `stale` Field can be used only by rules that do not care about freshness. R01 treats stale quotes as a VETO.
- **Missing input gives UNKNOWN, never PASS.** An UNKNOWN on a **hard** rule blocks the trade, the same as a VETO. An UNKNOWN on a **soft** rule is shown to the approver as a flag.
- **Precedence within a rule** (for example per leg): VETO > UNKNOWN > FLAG > PASS (`risk_reference.combine`). **1.1.3: no results at all combine to UNKNOWN (fail closed; was PASS)**, so a rule that combines per-leg / per-quote / per-event results (R01–R03, R07–R09) can never pass silently on zero items: it returns UNKNOWN, which blocks for hard rules (VETO overall) and flags for soft ones. No YAML case outcome changed (all 78 still pass). R09's explicit "no short legs → PASS" is unchanged.
- **Overall verdict:** VETO if any rule returns VETO or any hard rule returns UNKNOWN. Otherwise FLAG if any rule returns FLAG or any soft rule returns UNKNOWN. Otherwise PASS. `blocking_rule_ids` lists the rules behind a VETO, and `flag_rule_ids` lists the FLAGs and soft UNKNOWNs. **Eugene must still approve the exact contracts, even on a PASS.** Only a PASS or FLAG review can be approved, and every flag must be acknowledged.
- **Flag acknowledgement (session INV-05, 1.1.3 and 1.1.4 records):** an approval's `acknowledged_flags` must list exactly the review's flagged rule_ids (FLAG or soft UNKNOWN results, plus any recorded `flag_rule_ids`): none missing (`INV-05-ACK-MISSING`; an absent field acknowledges nothing), none extra (`INV-05-ACK-EXTRA`; a PASS review needs an empty list), no duplicates (`INV-05-ACK-DUPLICATE`). An approval of a VETO review is `INV-05-VETO`, whatever it acknowledges.
- **Stored verdict = computed verdict (session INV-16, 1.1.3 and 1.1.4 records):** `overall.verdict`, `blocking_rule_ids` and `flag_rule_ids` must equal `risk_reference.overall(results)` (ids as sets). A stored PASS or FLAG over any VETO or hard UNKNOWN is `INV-16-VERDICT`; consumers must never trust a stored verdict the results do not support.
- **Time zones.** Market hours, DTE and event days are all computed in **America/New_York**, never Europe/Berlin. Their DST changes fall on different dates. In 2026 the EU switches back on Oct 25 and the US on Nov 1. During that week NY is 5 hours behind Berlin instead of 6, so the 09:30 ET open is 14:30 Berlin rather than 15:30 (test R01-f). Timestamps without a zone are rejected.

## Config (risk_rules.yaml → `config`, overridable per review via `config_overrides`)
| key | default | note |
|---|---|---|
| per_trade_max_loss_usd | 500 | **PLACEHOLDER: needs Eugene** |
| max_underlying_risk_usd / max_total_open_risk_usd / max_open_positions | 1000 / 2000 / 5 | **PLACEHOLDER: needs Eugene** (kept as the defaults in v1.1) |
| max_sector_risk_usd / sector_map | null / {} | v1.1. null = sector cap off |
| allowed_structures | long_call, long_put, bull_call_debit_spread, bear_put_debit_spread, bull_put_credit_spread, bear_call_credit_spread, iron_condor | v1.1, R12 |
| hold_min_days / hold_max_days | 7 / null | v1.1, R13 (null = no maximum) |
| quote_max_age_seconds | 900 | 15 min |
| quote_future_tolerance_seconds | 60 | as_of further in the future than this → UNKNOWN |
| max_spread_pct_of_mid | 0.10 | |
| min_open_interest | 500 | |
| min_dte | 7 | |
| net_price_basis | mid | `natural` = pay the ask on buys, receive the bid on sells |
| fees_per_contract_usd | 0 | |
| macro_event_kinds | CPI, PPI, FOMC, NFP | |
| min_put_extrinsic | 0.05 | |
| iv_min_history_obs / iv_rank_high / iv_rank_low | 20 / 0.80 / 0.20 | |
| market hours | 09:30–16:00 NY, early close 13:00; 2026 NYSE holidays and early closes listed | verify the calendar against nyse.com yearly |

## Shared definitions
- `mid = (bid+ask)/2`.
- Window: `start = max(evaluated_at, hold_window.start)` and `end = min(hold_window.end, 16:00 NY on the earliest leg expiry)`. Both ends are inclusive.
- Event interval by timing: `bmo` = 09:30 NY on the event date, `amc` = 16:00 NY, `at_time` = the exact instant, and `unknown` = the whole NY calendar day (any overlap counts).
- Ex-dividend exposure is at the close of the trading day **before** the ex-date, because that is when an early exercise happens.
- Coverage: the calendar for that kind and underlying must be `checked_through` at least the window-end date. Otherwise "no events found" is not trusted and the rule returns UNKNOWN.
- DTE = expiry date − the NY calendar date of `evaluated_at`.

## Rules (H = hard, S = soft)
| id | sev | inputs | formula | VETO | FLAG | UNKNOWN | PASS |
|---|---|---|---|---|---|---|---|
| R01_QUOTE_FRESHNESS | H | evaluated_at, underlying.last, legs bid/ask | age = evaluated_at − as_of | market closed (NY hours), any quote stale, or age > 900 s | – | any quote unusable, or as_of > 60 s in the future | open and every age ≤ 900 s |
| R02_BID_ASK_WIDTH | H | legs bid/ask | (ask−bid)/mid per leg | any leg > 0.10 | – | unusable or invalid quote (ask<bid, ask≤0, bid<0) | all legs ≤ 0.10 |
| R03_OPEN_INTEREST | H | legs OI | OI per leg | any leg < 500 | – | OI unusable | all legs ≥ 500 |
| R04_MAX_LOSS ⚠placeholder | H | legs, qty, limit, fees | max loss at the **limit price**. v1.1.1: for the four verticals whose legs match the strategy, the INV-14 formula (`verticals.py`) + fees; otherwise the expiry payoff checked at S ∈ {0} ∪ strikes. Unlimited if the net call ratio is < 0. | unlimited or > $500 | – | no limit price, no threshold, or legs with different expiries | ≤ $500 |
| R05_NET_PRICE_VS_LIMIT | H | legs bid/ask, side, ratio, limit | net = Σbuy px − Σsell px (mid or natural) | debit: net > limit; credit: −net < limit | – | quote unusable | within limit |
| R06_MIN_DTE | H | legs expiry, evaluated_at | min DTE (NY date) | < 7 | – | no legs | ≥ 7 |
| R07_EARNINGS_IN_WINDOW | H | earnings events + coverage, hold window, intended_event_ids | event interval ∩ window | earnings in window and not intended | every in-window earnings event is intended | coverage short or date unusable | covered and none in window |
| R08_MACRO_EVENTS | S | CPI/PPI/FOMC/NFP events + coverage | event interval ∩ window | – | macro event in window | any kind's coverage missing or short | all covered, none in window |
| R09_EARLY_ASSIGNMENT | H | short legs, S, quotes, ex-div events + coverage | intrinsic and extrinsic of each short leg; ex-div exposure in window | short call with ex-div in window AND (ITM or extrinsic < dividend) | short call with ex-div in window, OTM with extrinsic ≥ dividend; or short put ITM with extrinsic < 0.05 | short call present and ex-div coverage or data unusable | no short legs, or none of the above |
| R10_IV_VS_HISTORY | S | iv30 current + history | IV rank = (cur−min)/(max−min) | – | debit with rank > 0.80, or credit with rank < 0.20 | IV missing or < 20 observations | otherwise |
| R11_CONCENTRATION ⚠placeholder | S | open paper positions, R04 max loss, sector_map | risk in the same underlying, **same sector (v1.1)**, total open risk, position count (each including the ticket) | – | same > 1000, sector > max_sector_risk_usd (if set), total > 2000, count > 5, or unlimited | positions unusable; or sector cap set and an underlying (ticket or open position) has no sector | otherwise |
| R12_DEFINED_RISK_STRUCTURE (v1.1) | H | ticket.strategy, legs, limit price_type, allowed_structures | naked = Σ(sign·ratio) < 0 for calls or for puts; legs must form the declared strategy (vertical: 2 legs, same type/expiry/ratio, one buy + one sell; debit/credit matches) | naked short; strategy not allowed; legs or price_type don't match | – | no legs or no strategy | allowed and matching |
| R13_HOLD_PERIOD (v1.1) | H | evaluated_at, hold window, legs expiry | hold_days = NY date(hold end) − NY date(max(evaluated_at, hold start)); dte = min leg DTE | hold_days or dte outside [hold_min_days, hold_max_days]; or time stop after the earliest expiry's close | – | no legs / no hold_min_days | otherwise |

## Profiles (v1.1)
Layering: `config` defaults ← `profiles.<name>.config_overrides` ← per-review `config_overrides`. `risk_reference.evaluate(..., profile="demo")`; test cases may carry `profile: demo`.

**`profiles.demo`: "DEMO - fictional scenario, values relayed by agent Moodle, not confirmed by Eugene directly"** (`confirmed_by_eugene: false`). Not real limits; the placeholder defaults above stay in force unless the demo profile is selected.

| setting | demo value |
|---|---|
| broker_display | Harborline Securities (fictional, display only) |
| paper_account_equity_usd | 25,000 |
| max_risk_per_idea_pct → per_trade_max_loss_usd (R04) | 2 % → 500 |
| max_open_positions (ideas) | 5 |
| max_underlying_risk_usd | 500 (one idea per underlying) |
| max_sector_risk_usd | 1,500 (6 %) |
| max_total_open_risk_usd | 2,500 (10 %) |
| allowed_structures (R12) | bull_call_debit_spread, bear_put_debit_spread, bull_put_credit_spread, bear_call_credit_spread (no long single options, no iron condor; naked short = VETO) |
| hold_min_days / hold_max_days (R13) | 7 / 28 |
| watchlist / sector_map | JPM Financial; WDC, AAPL, MSFT, NVDA Technology; XOM Energy |

## Tests
`risk_rules.yaml → tests`: 78 cases, 3–10 per rule plus overall OV-a..f (R11-d..j, R12-a..h, R13-a..f and OV-e/f are v1.1; the demo cases run with `profile: demo`). Each case starts from an all-PASS fixture (JPM 340/350 call debit spread, evaluated 2026-10-14 11:00 ET, limit 4.25) and applies `set`/`append`/`remove` patches. Coverage includes exact boundaries (15 min, 10%, OI 500, 7 DTE, inclusive window end) and DST cases. `test_risk_reference.py` also checks that `session.example.json` validates and that its stored reviews match a recomputation by the reference implementation. It also includes 8 negative validator tests.

## Sample outcome (2026-10-13, ticket tkt-jpm-bcs-340-350)
- R12 and R13 PASS in both reviews (bull call debit spread; hold and DTE within 7+ days).
- 07:52 ET (13:52 CEST) premarket: **VETO**. R01 (market closed, stale quotes), R02 (350C 20.8%), R03 (340C OI 472), and R09 UNKNOWN (ex-div calendar not yet fetched).
- 12:16 ET (18:16 CEST) post-open: **VETO**. R02 (340C 14.5%, 350C 24.7%), R03 (340C OI 472), and R05 (mid 3.11 > limit 3.10). Soft UNKNOWNs: R08 (FOMC/NFP/PPI coverage), R10 (no IV).

## Tick rounding (session schema INV-13; `config.tick_rules`)
Applies to the 1.1.0+ raw tick fields: `limit_price.raw_amount` and `Condition.raw_value`, each with `tick_size` and `rounding`. The validator is `validate_session.py`; the risk rules R01–R13 do not use these fields.

| | below $3.00 | at or above $3.00 |
|---|---|---|
| penny-pilot names (`penny_pilot`, **PLACEHOLDER** [SPY, QQQ, IWM, JPM]; verify against the Cboe/OCC list) | 0.01 | 0.05 |
| all other names | 0.05 | 0.10 |

- The tier is chosen by the **raw** price. The rounded value must equal `round(raw, tick, direction)`, computed in exact Decimal (`repr` of the float, so 3.1006249999999995 → 3.10 down).
- `down` = floor, `up` = ceiling, `nearest` = half-up, `none` = rounded equals raw.

| ticket | limit | stop (`stop_rules`) | target (`target_rules`) |
|---|---|---|---|
| debit (all versions) | down (pay at most the cap) | up (exit no later) | down (take profit no later) |
| credit vertical (1.1.1 / 1.1.2 / 1.1.3 / 1.1.4 records) | **up** (receive at least the min credit) | **down** (buy back no later) | **up** (buy back no later) |

- Entry, exit and watcher conditions are only checked for consistency, with no forced direction.
- 1.1.0 records keep the 1.1.0 rule: no direction on a credit limit, and stop up / target down for every ticket.

## Vertical consistency (session schema INV-14, 1.1.1, 1.1.2, 1.1.3 and 1.1.4 records)
Applies to bull call / bear put debit spreads and bull put / bear call credit spreads.
- **Structure:** 2 legs of the same type and expiry, ratio 1:1, multiplier 100, one buy and one sell. Strikes must be ordered as the strategy says, and `price_type` must match (debit / credit).
- **Inputs:** every value comes from the legs.
  - width = |long − short strike|.
  - The price is the **limit**, the worst acceptable price (max debit / min credit).
  - stop and target are the single `position_value` condition (cost to close) in `stop_rules` and in `target_rules`.

| | credit (bull put / bear call) | debit (bull call / bear put) |
|---|---|---|
| price | 0 < credit < width | 0 < debit < width |
| stop | credit < stop_debit < width | 0 < stop < debit |
| target | 0 ≤ target_buyback < credit | debit < target ≤ width |
| max_loss | (width − credit) × 100 × qty | debit × 100 × qty |
| max_profit | credit × 100 × qty | (width − debit) × 100 × qty |
| breakeven | short − credit (put) / short + credit (call) | long + debit (call) / long − debit (put) |
| planned loss at stop | (stop − credit) × 100 × qty ≤ max_loss | – |

- **Stored values:** `max_loss.amount_usd`, `max_profit.amount_usd` and the breakeven whose `at_price` equals the limit must equal the computed values. `unlimited` must be false, and `at_price`, if set, must equal the limit. Any other `breakevens[]` entry must match the formula at its own `at_price`.
- **Precision:** arithmetic is exact Decimal with a 0.01 tolerance on $ amounts and breakevens; the inequalities are exact.
- **Credit stop check:** the planned loss at the stop is implied by stop < width, so it can only fail together with that clause.

## Stop / target direction (session schema INV-15, 1.1.2, 1.1.3 and 1.1.4 records)
Applies to the same four verticals. The stop and the target are the same single `position_value` condition (cost to close the spread) in `stop_rules` and in `target_rules` that INV-14 reads; INV-15 checks its comparison operator `op`.

| | stop (`stop_rules`) | target (`target_rules`) |
|---|---|---|
| debit (bull call / bear put): position_value = what selling the spread back returns | **`lte`** (exit when the value falls to the stop) | **`gte`** (take profit when the value rises to the target) |
| credit (bull put / bear call): position_value = cost to buy the spread back | **`gte`** (exit when the buy-back cost rises to the stop) | **`lte`** (take profit when the buy-back cost falls to the target) |

- Any other operator (`lt`, `gt`, `eq`, `cross_above`, `cross_below`, or the opposite direction) is an INV-15 error naming the ticket, the side, the found and the required operator.
- A missing or duplicate `position_value` condition is reported by INV-14 only; INV-15 skips that side.
- Only the `position_value` condition is checked; other stop / target conditions (e.g. `underlying_close`) are free.
- 1.0.0, 1.1.0 and 1.1.1 records are not checked, so the same operators there still validate.
