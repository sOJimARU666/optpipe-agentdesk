# TradingAgents architecture harvest — evaluation only (2026-11-06)

Status: **evaluation only**. Concepts harvested from public upstream source; **no code imported**, **no dependency added**, **no optpipe `src/` change**, **no merge to main**. R6 / SEC-GATE-001 / R7 / MD-18 remain **OPEN**. Scope is Phase 1 reliability ideation for Sol's `sol/phase1-reliability` track.

| Field | Value |
|---|---|
| Author | Doodle (research/spec lane), authorized via Soodle ~09:40 Europe/Berlin 2026-11-06 |
| Upstream repo | [TauricResearch/TradingAgents](https://github.com/TauricResearch/TradingAgents) |
| Upstream commit read | `9fa1196fffbcfd9de5146d2dd2869df84ab99838` (2026-11-04; "announce Trading-R1 official release") |
| Upstream license | Apache-2.0 (`LICENSE`) — **attribution required before any literal copy; this note copies concepts only** |
| optpipe pin | Spec **1.1.8** (`/workspace/trading/spec`; risk / watcher / market_data rules) |
| Target branch | `sol/phase1-reliability` on `sOJimARU666/optpipe-agentdesk` |
| Sibling note | `audit/REL-001-baseline.md` |
| Method | Public shallow clone to `/tmp` for read-only inspection; **upstream code was not executed with network or credentials** |

## Hard cautions (apply to every row)

1. **No networking, credentials, or broker into optpipe.** TradingAgents vendors (Yahoo, Alpha Vantage, Reddit, StockTwits, TypeSafe, LLM providers) and `.env` API-key paths are **out of scope** for Phase 1. Prefer MD-23-NO-FALLBACK / R6 residual discipline; do not port dataflows/net or LLM clients.
2. **Prefer optpipe's aware clock over naive UTC.** Upstream `date_window.to_utc` treats naive datetimes as UTC; `get_current_date` uses `date.today()` (host local). Optpipe 1.1.8 requires offset-aware timestamps (MD-04-TIME-NAIVE; risk rules: America/New_York for market hours; dual NY/Berlin storage). Any KEEP IDEA must be re-specified on optpipe's clock model.
3. **Do not borrow non-repeatable return claims.** Upstream README/marketing and backtest hit-rate/mean-alpha summaries are **not** evidence for optpipe paper PnL. Decision-quality metrics ≠ portfolio simulation (see matrix row on evaluation honesty).
4. **Apache-2.0 attribution before any literal copy.** This harvest is concepts-only. No source file may be copied into optpipe without NOTICE/LICENSE handling and Eugene approval.
5. **R6 and SEC-GATE-001 stay OPEN.** Python-level guards elsewhere are not OS egress denial. This note does not close either gate.
6. **Phase 1 reliability only.** No PR C, no features deploy, no live path, no demo-watchlist promotion to personal.

## Comparison matrix

Columns: upstream idea (file + pinned URL) ↔ optpipe 1.1.8 equivalent or gap ↔ invariant/test that would cover it ↔ verdict ↔ reason.

| # | Upstream idea (file / URL @ `9fa1196`) | optpipe 1.1.8 equivalent or gap | Invariant / test that would cover it | Verdict | Reason |
|---|---|---|---|---|---|
| 1 | **Point-in-time clamp:** `as_of` / `as_of_window` never let a tool request a date after the run's `trade_date`; canonical `YYYY-MM-DD` ([`tradingagents/dataflows/date_window.py`](https://github.com/TauricResearch/TradingAgents/blob/9fa1196fffbcfd9de5146d2dd2869df84ab99838/tradingagents/dataflows/date_window.py)) | Partial: MD-04 rejects `as_of` after `pack_built_at`; R01/risk point-in-time usable-field rule. Gap: no first-class **replay trade_date** clamp on every consumer tool call; R7 proposes MD-19-LOOKAHEAD / MD-19-FUTURE | `test_md19_replay_no_lookahead`, `test_md19_future_field_unknown`, session INV usable-field as_of ≤ evaluated_at | **KEEP IDEA** | Concept aligns with Sol-flagged date_window theme and R7 criterion 4; re-implement on aware NY clock, not import upstream |
| 2 | **Undated / live-leak withhold:** `in_window` keeps undated items only when window reaches present; `withhold_live_profile` / `withhold_undisclosed_trades` / `withhold_undated_statements` replace post-decision vendor snapshots in historical runs (same file) | Gap: market packs are Oodle snapshots, not live vendor profiles; no explicit "withhold live profile in historical replay" rule in 1.1.8. R7 MD-19-LOOKAHEAD / MD-23 cover spirit | `test_md19_replay_lookahead`, `test_md21_replay_lookahead`, `test_md23_no_live_or_http_fallback` | **KEEP IDEA** | Strong reliability concept for any future replay harness; keep as fail-closed UNKNOWN/placeholder text policy, never as vendor call |
| 3 | **Coverage-gap honesty:** `coverage_gap` returns an explicit "unavailable for window" placeholder instead of pretending empty = no news ([`date_window.py`](https://github.com/TauricResearch/TradingAgents/blob/9fa1196fffbcfd9de5146d2dd2869df84ab99838/tradingagents/dataflows/date_window.py)) | Partial: MD-06 / risk "missing → UNKNOWN, never PASS"; calendars must be `checked_through` or R07–R09 return UNKNOWN. Gap: no shared coverage-gap string contract for discovery/research text | Soft-rule UNKNOWN tests; calendar `checked_through` cases in risk tests | **KEEP IDEA** | Maps cleanly to fail-closed UNKNOWN; useful for watcher/research prose honesty |
| 4 | **Future trade_date rejected** at graph entry (`_validate_trade_date` vs `get_current_date`) ([`trading_graph.py`](https://github.com/TauricResearch/TradingAgents/blob/9fa1196fffbcfd9de5146d2dd2869df84ab99838/tradingagents/graph/trading_graph.py)) | Partial/gap: MD-04 future field checks vs pack_built_at only; R7 notes a future `pack_built_at` can still load in 1.1.8; MD-19-FUTURE-PACK proposed | `test_md19_future_pack_rejected`, R01 future-quote UNKNOWN (>60s) | **KEEP IDEA** | Same family as MD-19-FUTURE*; implement in optpipe validator/replay clock, not host `date.today()` |
| 5 | **Naive→UTC assumption** in `to_utc` ([`date_window.py`](https://github.com/TauricResearch/TradingAgents/blob/9fa1196fffbcfd9de5146d2dd2869df84ab99838/tradingagents/dataflows/date_window.py)) | **Conflict:** optpipe MD-04-TIME-NAIVE / INV-05-TIME-NAIVE reject zone-less timestamps; market logic is America/New_York | Existing MD-04 / INV time-naive tests | **REJECT** | Optpipe already chose the safer clock contract; do not adopt naive-as-UTC |
| 6 | **Bounded analyst orchestration:** each selected analyst is a private subgraph; after `max_tool_rounds` a wrap-up forces a report so tool loops cannot hit recursion limits ([`setup.py`](https://github.com/TauricResearch/TradingAgents/blob/9fa1196fffbcfd9de5146d2dd2869df84ab99838/tradingagents/graph/setup.py), [`analyst_execution.py`](https://github.com/TauricResearch/TradingAgents/blob/9fa1196fffbcfd9de5146d2dd2869df84ab99838/tradingagents/graph/analyst_execution.py)) | Gap: optpipe stages (discovery/research/risk/watcher) are code agents, not LLM tool-loops; no max-tool-round analogue. Useful if LLM analysts are ever added later | Future: recursion/timeout invariant on any LLM tool node; Phase 1: N/A to current src | **DEFER** | Good pattern, but Phase 1 reliability is pack/loader/risk invariants, not LLM orchestration |
| 7 | **Analyst selection plan:** `build_analyst_execution_plan` requires ≥1 known analyst key; unknown key raises ([`analyst_execution.py`](https://github.com/TauricResearch/TradingAgents/blob/9fa1196fffbcfd9de5146d2dd2869df84ab99838/tradingagents/graph/analyst_execution.py)) | Partial: pipeline roles are fixed; config unknown keys should fail closed (aligns with MD-16 / schema extras rejection) | Config/schema extra-key tests; MD-02-EXTRA | **KEEP IDEA** | Small fail-closed config idea; already mostly mirrored by schema additionalProperties discipline |
| 8 | **Exhaustive conditional path maps:** `DEBATE_PATH_MAP` / `RISK_ANALYSIS_PATH_MAP` list every router return so a fall-through cannot miss an edge ([`setup.py`](https://github.com/TauricResearch/TradingAgents/blob/9fa1196fffbcfd9de5146d2dd2869df84ab99838/tradingagents/graph/setup.py), [`conditional_logic.py`](https://github.com/TauricResearch/TradingAgents/blob/9fa1196fffbcfd9de5146d2dd2869df84ab99838/tradingagents/graph/conditional_logic.py)) | Gap: optpipe handoffs are explicit Python calls, not LangGraph routers. Idea = "every branch label must be in a closed map" | Orchestrator branch-coverage tests; no silent default PASS | **DEFER** | Valuable if/when a graph DSL appears; not needed for Phase 1 pack reliability |
| 9 | **Round-capped debate / risk discuss:** `ConditionalLogic` stops bull/bear after `2 * max_debate_rounds` and risk trio after `3 * max_risk_discuss_rounds` ([`conditional_logic.py`](https://github.com/TauricResearch/TradingAgents/blob/9fa1196fffbcfd9de5146d2dd2869df84ab99838/tradingagents/graph/conditional_logic.py)) | Gap: no multi-agent debate loop in 1.1.8. Risk is deterministic R01–R13 | N/A Phase 1 | **DEFER** | Orchestration product feature, not reliability gate |
| 10 | **Checkpoint thread signature:** `thread_id(ticker, date, signature)` hashes graph-shape inputs so resume under a different analyst set / layout cannot reuse a checkpoint ([`checkpointer.py`](https://github.com/TauricResearch/TradingAgents/blob/9fa1196fffbcfd9de5146d2dd2869df84ab99838/tradingagents/graph/checkpointer.py), `_run_signature` in [`trading_graph.py`](https://github.com/TauricResearch/TradingAgents/blob/9fa1196fffbcfd9de5146d2dd2869df84ab99838/tradingagents/graph/trading_graph.py)) | Gap: optpipe has no LangGraph checkpoint DB. Closest: MD-18 bind pack_id/manifest to ingest; session record immutability after approval | `test_md18_manifest_binding_mismatch`, `test_md22_snapshot_cannot_change_after_validation` | **KEEP IDEA** | Integrity-of-resume concept transfers to "validated snapshot + consumer signature"; do not import SqliteSaver |
| 11 | **Safe ticker path component** for checkpoint DB filenames ([`checkpointer.py`](https://github.com/TauricResearch/TradingAgents/blob/9fa1196fffbcfd9de5146d2dd2869df84ab99838/tradingagents/graph/checkpointer.py) + `safe_ticker_component`) | Partial: MD-01/MD-17 path confinement; ticker pattern rules. Reinforce for any on-disk per-symbol state | `test_md17_root_confinement`, path-traversal cases in marketdata tests | **KEEP IDEA** | Directly supports Phase 1 filesystem reliability |
| 12 | **Clear checkpoints including WAL/SHM sidecars** ([`checkpointer.py`](https://github.com/TauricResearch/TradingAgents/blob/9fa1196fffbcfd9de5146d2dd2869df84ab99838/tradingagents/graph/checkpointer.py)) | Gap: optpipe not using SQLite checkpoints. General lesson: delete/replace must cover sidecar/state files | Future persistence hygiene tests | **DEFER** | Correct detail if SQLite appears; out of Phase 1 scope |
| 13 | **Evaluation honesty in backtest summarize:** scores settled decisions by rating hit-rate / mean alpha; `REVIEW` and unscored entries excluded from directional stats; holding window reported from log ([`backtest.py`](https://github.com/TauricResearch/TradingAgents/blob/9fa1196fffbcfd9de5146d2dd2869df84ab99838/tradingagents/backtest.py)) | Gap: optpipe paper sessions track tickets/fills/risk verdicts, not LLM rating hit-rate. Principle matches "do not equate decision-quality metrics with portfolio sim" | Docs/tests that separate risk-rule PASS rates from paper PnL; forbid marketing hit-rate as PnL | **KEEP IDEA** | Exactly Sol's evaluation-honesty theme; keep as reporting policy |
| 14 | **Backtest cell isolation:** one vendor failure appends to `failures` and continues; settlement failures tracked separately ([`backtest.py`](https://github.com/TauricResearch/TradingAgents/blob/9fa1196fffbcfd9de5146d2dd2869df84ab99838/tradingagents/backtest.py)) | Partial: hermetic pytest fixtures already isolate. Live multi-cell sweeps are not Phase 1 | Replay matrix MD-21 (fail closed per case, no crash) | **DEFER** | Operational batching; Phase 1 wants deterministic single-pack replay first |
| 15 | **Structured dissent (bull vs bear) then Research Manager commit** ([`state.py`](https://github.com/TauricResearch/TradingAgents/blob/9fa1196fffbcfd9de5146d2dd2869df84ab99838/tradingagents/agents/state.py) `InvestDebateState`; researchers under `agents/researchers/`) | Gap: optpipe research is not adversarial LLM debate. Risk rules are the dissent/veto channel (VETO/FLAG/UNKNOWN) | INV-16 overall = aggregate(results); hard UNKNOWN blocks | **DEFER** | Product/UX idea; optpipe already has stronger deterministic dissent via R01–R13 |
| 16 | **Risk trio debate (aggressive/conservative/neutral) before Portfolio Manager** ([`conditional_logic.py`](https://github.com/TauricResearch/TradingAgents/blob/9fa1196fffbcfd9de5146d2dd2869df84ab99838/tradingagents/graph/conditional_logic.py), `RiskDebateState`) | Equivalent spirit: multi-rule risk review. Prefer deterministic YAML rules over LLM risk debate for paper options | Existing `risk_reference` + 78 YAML cases; INV-16 | **REJECT** (for Phase 1) | LLM risk debate would weaken fail-closed guarantees Sol requires; keep optpipe rule engine |
| 17 | **Unreadable rating → `REVIEW` sentinel, not silent Hold** ([`rating.py`](https://github.com/TauricResearch/TradingAgents/blob/9fa1196fffbcfd9de5146d2dd2869df84ab99838/tradingagents/agents/rating.py)) | Strong parallel: missing/critical → **UNKNOWN / NO IDEA**, never PASS (risk rules; R7 MD-20-NO-IDEA). Approval still required even on PASS | `test_md20_each_critical_field_unknown`, INV-16-EMPTY, hard UNKNOWN → VETO overall | **KEEP IDEA** | Same fail-closed ethics as NO IDEA; adopt as naming/docs alignment only |
| 18 | **Explicit `AgentState` TypedDict contract** with annotated fields for reports, debate states, `final_rating`, memory notes ([`state.py`](https://github.com/TauricResearch/TradingAgents/blob/9fa1196fffbcfd9de5146d2dd2869df84ab99838/tradingagents/agents/state.py)) | Equivalent: `session.schema.json` + market_snapshot schema + Field wrappers. Gap: research/discovery intermediate state less formally contracted than session records | Schema tests; MD-20-SCOPE consumer inventory (R7) | **KEEP IDEA** | Reinforces "explicit state contracts vs NO IDEA"; inventory consumers against schema |
| 19 | **Structured Pydantic decision schemas** (`ResearchPlan`, `TraderProposal`, `PortfolioDecision`) with coerced optional floats so one bad field does not wipe the decision ([`schemas.py`](https://github.com/TauricResearch/TradingAgents/blob/9fa1196fffbcfd9de5146d2dd2869df84ab99838/tradingagents/agents/schemas.py)) | Partial: optpipe uses Decimal + schema; rejects bad numerics rather than coercing. R7 MD-16-NONFINITE rejects NaN/Inf | MD-16-NONFINITE tests; Decimal exactness conventions in AGENTS.md | **DEFER** | Coercion-vs-reject tension: optpipe should keep reject/UNKNOWN for market numerics; optional only for LLM prose fields if ever added |
| 20 | **Vendor/network-heavy dataflows and LLM provider stack** (`dataflows/vendors/*`, `llm_clients/*`, `.env.example`) | **Out of bounds** for optpipe paper path; conflicts with no-broker / MD-23 / R6 | N/A — do not port | **REJECT** | Explicit caution: no networking/credentials/broker into optpipe |
| 21 | **Portfolio context / memory log reflection loop** (`portfolio.py`, `memory/*`) feeding next decisions | Gap / risk: look-ahead and non-hermetic memory. Optpipe sessions are durable records but must not silently reuse stale unverified state | MD-22 snapshots immutable after validation; no cross-pack fill (MD-20) | **DEFER** | Interesting for later paper journaling; Phase 1 must not blur snapshot immutability |
| 22 | **Claimed strategy returns / demo performance in upstream README or reports** | No optpipe equivalent should cite them | N/A | **REJECT** | Non-repeatable / non-applicable to Eugene's paper options rules |

### Verdict counts

| Verdict | Rows |
|---|---|
| KEEP IDEA | 10 |
| DEFER | 8 |
| REJECT | 4 |
| **Total** | **22** |

## Themes → row map (Sol-flagged)

| Theme | Rows |
|---|---|
| Point-in-time guards (`date_window`) | 1–5 |
| Bounded analyst orchestration | 6–9 |
| Resume / checkpointer integrity | 10–12 |
| Evaluation honesty (decision quality ≠ portfolio sim) | 13–14, 22 |
| Structured dissent / veto | 15–17 |
| Explicit state contracts vs NO IDEA | 17–19, 18 |

## What this note does **not** do

- Does not change `spec/` VERSION (stays **1.1.8**).
- Does not implement MD-16…MD-23 or close any R7 criterion.
- Does not add TradingAgents as a dependency or submodule.
- Does not open a PR, touch `main`, or modify `src/`.
- Does not claim independent verification of upstream tests (files were read; suite not run here).

## Sources (pinned)

- Upstream tree at `9fa1196fffbcfd9de5146d2dd2869df84ab99838`:  
  https://github.com/TauricResearch/TradingAgents/tree/9fa1196fffbcfd9de5146d2dd2869df84ab99838
- Modules read (blob URLs in matrix): `trading_graph.py`, `setup.py`, `conditional_logic.py`, `checkpointer.py`, `analyst_execution.py`, `agents/state.py`, `dataflows/date_window.py`, `backtest.py`, plus supporting `agents/rating.py`, `agents/schemas.py`, `LICENSE`.
- optpipe: `/workspace/trading/spec` 1.1.8 (`risk_rules.yaml/.md`, `watcher_rules.yaml/.md`, `market_data_rules.yaml/.md`, `session.schema.json`); `/workspace/trading/proposals/R7-acceptance-proposal.md` (v2).
- Sibling: `audit/REL-001-baseline.md` on `sol/phase1-reliability` @ `b7e9a5b68a08e2a311053e3ed5dd2c1abd798071` (pre-write head).
