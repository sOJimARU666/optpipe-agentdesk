# Watcher rules v1.1.6 (alert-only, paper-only US options)

Source of truth: `watcher_rules.yaml` (config, scope, polling, dedupe, definitions, rules W01–W07, fixture, 59 test cases). `watcher_reference.py` is the reference evaluator (`poll(inp, state) -> (alerts, new_state, next_poll_seconds)`), `test_watcher_reference.py` runs the YAML cases. If this file and the YAML disagree, the YAML wins.

**The watcher never places, changes or cancels an order.** Every alert ends with the text `alert only, no order`.

## Scope
- Watched intraday: paper positions with status `open`, and tickets with status `approved_pending`.
- Tickets that are `vetoed` or `rejected` are not polled. They get one `SHADOW_EOD` info summary per NY trading day, at or after the close (16:00 NY, or 13:00 on early-close days).
- Anything else (closed positions, drafts) is ignored.

## Approval state (spec 1.1.3, FIX-SEC-001; revoked ruling spec 1.1.4; INV-16 and full-precedence rulings spec 1.1.5; open window, fail-closed times and explicit precedence spec 1.1.6)
- **Approval state is read ONLY from `approvals[].status`. There is no `decision` field.** The approval schema has `additionalProperties: false`, so a record carrying `decision` is schema-invalid; a watcher must never read it, trust it or fall back to it.
- **Blocked review (spec 1.1.5).** The watcher treats a risk review as VETO when it is *blocked*: its recorded verdict is VETO, its rule results give VETO (a VETO or a hard UNKNOWN), it has no rule results, **or it fails INV-16**: the stored `overall.verdict`, `blocking_rule_ids` or `flag_rule_ids` differ from `risk_reference.overall(results)` (e.g. *tampered_pass*: a stored PASS over FLAG results). Reference: `watcher_reference.review_blocked` = `validate_session.review_is_veto(rv) or validate_session.overall_errors(rv)` (the validator's INV-16 logic, not a copy). This holds for records of **every** `schema_version` (fail closed; the validator reports INV-16 as a record error only for 1.1.3+ records) and matches the pipeline runtime's `effective_verdict` (optpipe).
- **Full precedence for a ticket target** (spec 1.1.5; `watcher_reference.target_from_session`; YAML `scope.target_precedence` = `watcher_reference.TARGET_PRECEDENCE` = `[vetoed, terminal, rejected, approved_pending, idea_status]`; spec 1.1.6: the steps below are also the explicit top-level YAML `target_precedence` list, one entry per step 1, 2, 3, 4a, 4b in this order, plus `real_approval` for step 5). First match wins:
  1. **VETO first → `vetoed`:** the ticket's latest review (by `evaluated_at`) is blocked (including INV-16 failures such as *tampered_pass*), or any approval of the ticket points at a blocked review, or the idea is `vetoed`. Shadow only. An approval never un-vetoes a ticket, and VETO also wins over a rejected, executed or closed idea and over a revoked approval.
  2. **Terminal lifecycle → the idea status:** an idea that is `executed` or `closed` keeps its status (`watcher_reference.TERMINAL_IDEA_STATUSES`). It is not watched as a ticket; the entered trade is watched by the **position** rules (W01–W07 on its `paper_positions` entry). A revoked approval does **not** relabel it `rejected`, and no approval makes it a watched ticket.
  3. **Pre-execution → `rejected`:** for every other idea (`proposed`, `simulated`, `approved_pending`, `approved`, `rejected`, any other non-terminal status, or no idea): the idea is `rejected`, **or** any approval of the ticket has `status: revoked` and there is no real approval (step 5). Shadow only. So "revoked = rejected" overrides step 4 for `proposed` and `simulated` ideas (in 1.1.4 those passed through as `proposed` / `simulated`; 1.1.4 already made revoked fail-closed for watched tickets).
  4. **Otherwise:**
     a. idea `approved_pending`, `approved`, or no idea → `approved_pending`, watched: `approved: true` if a real approval (step 5) exists, else hypothetical (`approved: false`: expired, consumed, active but not real, no approval);
     b. any other non-approved idea (`proposed`, `simulated`, …) keeps its status and is **not** watched.
  5. **Real approval** (`watcher_reference.real_approval`): the idea status is `approved_pending` or `approved` (`watcher_reference.APPROVED_IDEA_STATUSES`) **and** some approval for the ticket has `status: active`, is inside its window at the poll time (spec 1.1.6: **`approved_at < now < expires_at`**, an open window compared as instants whatever the UTC offset, the same rule as INV-06 at `filled_at`; 1.1.3–1.1.5: `approved_at <= now < expires_at`), passes the 1.1.3 INV-05 checks (not a VETO review, same ticket, legs/qty/limit equal to the ticket, every flagged rule_id in `acknowledged_flags`) and its review passes INV-16. **An active approval on a non-approved idea (e.g. `proposed`) never counts as really approved**; with no idea at all the ticket is watched as hypothetical (step 4a), never real.
- **Times fail closed (spec 1.1.6, SOL-ACCEPT-001 G4/G5).** A naive (no UTC offset) or unparsable `now`, `approved_at` / `expires_at` never raises: no approval is real, and the target gains `errors: ["INV-06-TIME-NAIVE …"]` (or `-TIME-INVALID`). A review whose `evaluated_at` is naive / unparsable cannot be ordered, so the ticket is `vetoed` with `INV-02-TIME-NAIVE` / `-INVALID` in `errors`. Without such a problem the target has no `errors` key (the 1.1.5 shape). Review verdicts use the fail-closed aggregate (`risk_reference.classify`: an unrecognised result string, wrong case or empty, is VETO; UNKNOWN without `hard`/`soft` severity is hard).
- **Positions (spec 1.1.6).** `target_from_session` maps tickets only; it never evaluates `paper_positions`, so approval reuse (one approval opening two positions) is a record error for the validator (`INV-17-REUSE`, 1.1.6 records), not a watcher target.
- Full approval-status mapping (YAML `scope.approval_status`, `watcher_reference.APPROVAL_STATUS_TARGET`), for a ticket whose reviews are not blocked and whose idea is `approved_pending` / `approved`:

  | `approvals[].status` of the ticket's approval | watcher target | polled intraday? |
  |---|---|---|
  | `active` (in window, INV-05 and INV-16 clean) | `approved_pending`, `approved: true` (real) | yes |
  | `active` but outside its window or failing INV-05 | `approved_pending`, `approved: false` (hypothetical) | yes |
  | any status, its review (or the latest review) fails INV-16 | `vetoed` (shadow; 1.1.5, an `active` one was real) | no – `SHADOW_EOD` only |
  | `revoked` | `rejected` (shadow; 1.1.4, was hypothetical; 1.1.5: also for `proposed` / `simulated` ideas, not for `executed` / `closed`) | no – `SHADOW_EOD` only |
  | `expired` | `approved_pending`, `approved: false` (hypothetical) | yes |
  | `consumed` | `approved_pending`, `approved: false` (hypothetical); the entered trade is watched as its paper position | yes |
  | missing / none | `approved_pending`, `approved: false` (hypothetical) | yes |
  | any status, ticket vetoed (step 1) / idea rejected (step 3) | `vetoed` / `rejected` | no – `SHADOW_EOD` only |

  By idea status (reviews not blocked): `executed` / `closed` → kept, whatever the approvals (step 2); `proposed` / `simulated` → `rejected` with a revoked approval, otherwise kept and unwatched even with an active approval (steps 3, 4b, 5); no idea → as the table, except that an `active` approval is hypothetical, not real (step 5).

  With several approvals for one ticket: a blocked review behind any of them vetoes the ticket (step 1); otherwise, for a pre-execution idea, a real approval wins unless the idea is rejected; without one, a single revoked approval makes the ticket `rejected`, whatever the other approvals say.
- Known differences from the pipeline runtime (optpipe), outside the parity set and not spec rulings: optpipe relabels a revoked `executed` / `closed` idea `rejected` (spec: keeps it, step 2), and watches a ticket with a `proposed` / `simulated` / `executed` / `closed` idea as `approved_pending` (real with an active approval; spec: step 2 / 4b, never real, step 5).
- Reference: `watcher_reference.target_from_session(sess, ticket_id, now)` / `real_approval(...)` / `review_blocked(rv)`; tests in `test_invariants_v1_1_3.py`, `test_watcher_reference.py` (YAML cases SCOPE-e..r build the target from a session record via `approval_status`, `review_tamper`, `idea_status`) and `test_release_v1_1_5.py` (exact list of outcomes changed vs the frozen 1.1.4 reference).

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
BASE-a, POLL-a..d, SCOPE-a..r (e..g: approval status → target, 1.1.4; h..k: INV-16-inconsistent review → vetoed; l..r: idea status × approval, 1.1.5 — l/m proposed + revoked → rejected, n proposed + active → proposed and never really approved, o/p executed + revoked → executed, q simulated + revoked → rejected, r approved + active → real), DD-a..e (dedupe, escalation, cooldown), W01-a..e, W02-a..d, W03-a..d, W04-a..f, W05-a..d, W06-a..d, W07-a..d. Every rule has at least 2 alerting cases. The fixture is a JPM 340/350 call debit spread (pos-1), 2026-10-20 11:00 NY (17:00 Berlin CEST). `test_watcher_reference.py` also checks the alert contract (all fields present, message ends with `alert only, no order`), a replay test (warn → duplicate suppressed → exit_signal → duplicate suppressed), and that the evaluator contains no order or network code. Spec 1.1.6 adds no YAML case (still 59, none changed); `test_release_v1_1_6.py` covers the open approval window (at / 1 s before / 1 s after `expires_at` and `approved_at`, the same instant at `Z`, `+01:00`, `-05:00`), naive / unparsable times (no crash, INV id in `errors`), the fail-closed aggregate and the explicit `target_precedence` (order, md numbering, first match wins).
