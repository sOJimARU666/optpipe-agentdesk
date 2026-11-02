# Watcher rules v1.1.4 (alert-only, paper-only US options)

Source of truth: `watcher_rules.yaml` (config, scope, polling, dedupe, definitions, rules W01–W07, fixture, 48 test cases). `watcher_reference.py` is the reference evaluator (`poll(inp, state) -> (alerts, new_state, next_poll_seconds)`), `test_watcher_reference.py` runs the YAML cases. If this file and the YAML disagree, the YAML wins.

**The watcher never places, changes or cancels an order.** Every alert ends with the text `alert only, no order`.

## Scope
- Watched intraday: paper positions with status `open`, and tickets with status `approved_pending`.
- Tickets that are `vetoed` or `rejected` are not polled. They get one `SHADOW_EOD` info summary per NY trading day, at or after the close (16:00 NY, or 13:00 on early-close days).
- Anything else (closed positions, drafts) is ignored.

## Approval state (spec 1.1.3, FIX-SEC-001; revoked ruling spec 1.1.4)
- **Approval state is read ONLY from `approvals[].status`. There is no `decision` field.** The approval schema has `additionalProperties: false`, so a record carrying `decision` is schema-invalid; a watcher must never read it, trust it or fall back to it.
- Order of evaluation for a ticket target (`watcher_reference.target_from_session`):
  1. **VETO first:** if the ticket's latest review is VETO (recorded, or what its rule results give), or any approval of the ticket points at a VETO review, or the idea is `vetoed`, the target is `vetoed` (shadow only); an approval never un-vetoes a ticket.
  2. Idea `rejected` → `rejected` (shadow only).
  3. A **real approval** wins: some approval for the ticket has `status: active`, is not expired at the poll time (`approved_at <= now < expires_at`), and passes the 1.1.3 INV-05 checks (not a VETO review, same ticket, legs/qty/limit equal to the ticket, every flagged rule_id in `acknowledged_flags`) → `approved_pending`, `approved: true`.
  4. Otherwise, **any approval of the ticket with `status: revoked` → `rejected`** (shadow only). Spec 1.1.4 ruling: a revoked approval is fail-closed, never a watched what-if (it was hypothetical in 1.1.3); this matches the pipeline runtime (optpipe).
  5. Otherwise → hypothetical: watched as `approved_pending` with `approved: false`.
- Full approval-status mapping (YAML `scope.approval_status`, `watcher_reference.APPROVAL_STATUS_TARGET`), applied after steps 1–2:

  | `approvals[].status` of the ticket's approval | watcher target | polled intraday? |
  |---|---|---|
  | `active` (in window, INV-05 clean) | `approved_pending`, `approved: true` (real) | yes |
  | `active` but outside its window or failing INV-05 | `approved_pending`, `approved: false` (hypothetical) | yes |
  | `revoked` | `rejected` (shadow; 1.1.4, was hypothetical) | no – `SHADOW_EOD` only |
  | `expired` | `approved_pending`, `approved: false` (hypothetical) | yes |
  | `consumed` | `approved_pending`, `approved: false` (hypothetical); the entered trade is watched as its paper position | yes |
  | missing / none | `approved_pending`, `approved: false` (hypothetical) | yes |
  | any status, ticket vetoed (step 1) / idea rejected (step 2) | `vetoed` / `rejected` | no – `SHADOW_EOD` only |

  With several approvals for one ticket: a real approval wins (step 3); without one, a single revoked approval makes the ticket `rejected`, whatever the other approvals say.
- Reference: `watcher_reference.target_from_session(sess, ticket_id, now)` / `real_approval(...)`; tests in `test_invariants_v1_1_3.py` and `test_watcher_reference.py` (YAML cases SCOPE-e..g build the target from a session record via `approval_status`).

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
`rule_id`, `level`, `target_id`, `value`, `threshold`, `quote_as_of` (the oldest as_of among the quotes used), `at`, `detail`, `message` (1.1.3: `detail` is now also listed in the YAML `alert_fields`).
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
BASE-a, POLL-a..d, SCOPE-a..g (e..g: approval status → target, 1.1.4), DD-a..e (dedupe, escalation, cooldown), W01-a..e, W02-a..d, W03-a..d, W04-a..f, W05-a..d, W06-a..d, W07-a..d. Every rule has at least 2 alerting cases. The fixture is a JPM 340/350 call debit spread (pos-1), 2026-10-20 11:00 NY (17:00 Berlin CEST). `test_watcher_reference.py` also checks the alert contract (all fields present, message ends with `alert only, no order`), a replay test (warn → duplicate suppressed → exit_signal → duplicate suppressed), and that the evaluator contains no order or network code.
