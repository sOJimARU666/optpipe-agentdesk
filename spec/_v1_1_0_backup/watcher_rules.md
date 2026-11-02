# Watcher rules v1.1.0 (alert-only, paper-only US options)

Source of truth: `watcher_rules.yaml` (config, scope, polling, dedupe, definitions, rules W01–W07, fixture, 48 test cases). `watcher_reference.py` is the reference evaluator (`poll(inp, state) -> (alerts, new_state, next_poll_seconds)`), `test_watcher_reference.py` runs the YAML cases. If this file and the YAML disagree, the YAML wins.

**The watcher never places, changes or cancels an order.** Every alert ends with the text `alert only, no order`.

## Scope
- Watched intraday: paper positions with status `open`, and tickets with status `approved_pending`.
- Tickets that are `vetoed` or `rejected` are not polled. They get one `SHADOW_EOD` info summary per NY trading day, at or after the close (16:00 NY, or 13:00 on early-close days).
- Anything else (closed positions, drafts) is ignored.

## Polling (America/New_York; calendar from `risk_rules.yaml → config.market_hours`)
- Only during regular hours, 09:30–16:00 NY (13:00 on early closes). There is no polling on weekends, NYSE holidays or outside the session (`next_poll = None`).
- Every 300 s by default.
- Every 60 s when W01 is near or beyond a stop or target (within 10 %), or during the last 60 minutes before the close on any leg's expiry day.

## Levels, dedupe, cooldown
- The levels are `info` < `warn` < `exit_signal`. Each rule emits at most one alert per poll, at its highest level.
- **Dedupe** is on (rule, position or ticket, level). The same level, or a lower one, is never re-sent. There are no de-escalation alerts.
- **Escalation only.** A higher level than the last one sent goes out, subject to the cooldown.
- **Cooldown** is 30 minutes per (rule, target) after any sent alert. An escalation to `exit_signal` bypasses the cooldown. A held-back alert does not update the state, so it goes out on the first poll after the cooldown if the condition still holds.
- State shape: `sent{"RULE|target": level}`, `last_at{"RULE|target": iso}`, `wide_streak{leg_id: n}`.

## Alert fields
`rule_id`, `level`, `target_id`, `value`, `threshold`, `quote_as_of` (the oldest as_of among the quotes used), `at`, `detail`, `message`.
Message format: `[level] RULE target: detail | value=… threshold=… quote_as_of=… | alert only, no order`.

## Rules
| id | inputs | warn | exit_signal | info / notes |
|---|---|---|---|---|
| W01_MARK_VS_STOP_TARGET | leg bid/ask, mark_levels (entry, stop, target, tick) | mark within 10 % of \|entry − level\| of the stop or target | mark at or beyond the stop or target | mark = Σ sign·ratio·mid (debit); credit = cost to close (negated). Levels are rounded to the tick (half-up, default 0.01). "Beyond" = (mark − level)·(level − entry) ≥ 0. |
| W02_UNDERLYING_LEVELS | underlying last, prev_last, completed 5-min bars, levels | intraday cross of stop_below or stop_above | latest completed 5-min bar closes beyond a stop level | info on a breakeven cross |
| W03_IV_VS_ENTRY | leg IV vs entry IV | \|change\| ≥ 20 % on any leg | change ≤ −35 % for long premium | info "UNKNOWN" if entry IV or current IV is missing. Short premium: warn only. |
| W04_TIME | now, time_stop, leg expiries | DTE ≤ time-stop DTE + 2 (time_stop null: DTE ≤ 2) | from 10:00 NY on the time-stop date (persists while open); on any open leg's expiry day from 14:00 NY | |
| W05_SHORT_LEG_ASSIGNMENT | short legs, S, quotes, ex_dividend | short leg ITM and DTE ≤ 5 | ITM with extrinsic < 0.05; or a short call ITM on the trading day before ex-div with extrinsic < dividend | extrinsic = mid − intrinsic |
| W06_DATA_HEALTH | all quote fields | any quote not `ok` or older than 15 min during market hours; or a leg with bid-ask > 25 % of mid on 3 consecutive polls | – | while any quote is stale, exit_signals from W01–W05 are **suppressed** (not sent, state unchanged) |
| W07_EVENT_EVE | events (CPI, PPI, FOMC, NFP, earnings of this underlying), hold window | – | – | info on the trading session before an event that falls inside the hold window |

## Tests (watcher_rules.yaml → tests)
BASE-a, POLL-a..d, SCOPE-a..d, DD-a..e (dedupe, escalation, cooldown), W01-a..e, W02-a..d, W03-a..d, W04-a..f, W05-a..d, W06-a..d, W07-a..d. Every rule has at least 2 alerting cases. The fixture is a JPM 340/350 call debit spread (pos-1), 2026-10-20 11:00 NY (17:00 Berlin CEST). `test_watcher_reference.py` also checks the alert contract (all fields present, message ends with `alert only, no order`), a replay test (warn → duplicate suppressed → exit_signal → duplicate suppressed), and that the evaluator contains no order or network code.
