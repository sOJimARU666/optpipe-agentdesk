# Risk rules v1.1.0 (paper-only US options)

The machine-readable source of truth is `risk_rules.yaml`. It holds the config, definitions, rules, profiles, fixture and 78 test cases. `risk_reference.py` is the reference implementation, and `test_risk_reference.py` runs the YAML tests (`.venv/bin/python -m pytest -q`). If this document and the YAML disagree, the YAML wins.

## Principles
- **Deterministic.** Every rule is a pure function of the inputs and `evaluated_at`. Prices are compared with exact `Decimal` arithmetic, so a threshold hit exactly is included (≤ / ≥ pass).
- **Point-in-time.** A data Field is only usable if its `status` is `ok`, its value is not null, and its source was fetched at or before `evaluated_at`. A Field that is `missing`, `conflicting`, or not yet fetched cannot be used. A `stale` Field can be used only by rules that do not care about freshness. R01 treats stale quotes as a VETO.
- **Missing input gives UNKNOWN, never PASS.** An UNKNOWN on a **hard** rule blocks the trade, the same as a VETO. An UNKNOWN on a **soft** rule is shown to the approver as a flag.
- **Precedence within a rule** (for example per leg): VETO > UNKNOWN > FLAG > PASS.
- **Overall verdict:** VETO if any rule returns VETO or any hard rule returns UNKNOWN. Otherwise FLAG if any rule returns FLAG or any soft rule returns UNKNOWN. Otherwise PASS. `blocking_rule_ids` lists the rules behind a VETO, and `flag_rule_ids` lists the FLAGs and soft UNKNOWNs. **Eugene must still approve the exact contracts, even on a PASS.** Only a PASS or FLAG review can be approved, and every flag must be acknowledged.
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
| R04_MAX_LOSS ⚠placeholder | H | legs, qty, limit, fees | max loss of the expiry payoff at the **limit price**, checked at S ∈ {0} ∪ strikes. Unlimited if the net call ratio is < 0. | unlimited or > $500 | – | no limit price, no threshold, or legs with different expiries | ≤ $500 |
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
