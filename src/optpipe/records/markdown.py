"""Markdown rendering of a schema-v1 session record (the JSON is the source of truth) + hand-off trace."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from ..models.session import SessionTrace


def _v(f: Any) -> str:
    if not isinstance(f, dict) or "status" not in f:
        return "-" if f is None else str(f)
    if f["status"] == "conflicting":
        alts = "; ".join(f"{a.get('value')} ({a.get('source_id')})" for a in f.get("alternatives") or [])
        return f"CONFLICTING [{alts}]"
    if f["value"] is None:
        return f"{f['status'].upper()}"
    return f"{f['value']}" + ("" if f["status"] == "ok" else f" ({f['status']})")


def _t(p: dict) -> str:
    n, b = datetime.fromisoformat(p["ny"]), datetime.fromisoformat(p["berlin"])
    return f"{n:%Y-%m-%d %H:%M} New York / {b:%H:%M} Berlin"


def render_markdown(rec: dict, trace: Optional[SessionTrace] = None) -> str:
    s = rec["session"]
    L: list[str] = []
    a = L.append
    a(f"# Paper options session {s['date']} - SIMULATED ONLY (mode: {s['mode']})\n")
    a("> PAPER ONLY. No broker, no live orders. Tickets are HYPOTHETICAL data records. "
      "Every ticket needs Eugene's explicit approval; VETO tickets cannot be approved.\n")
    a("## Session")
    a(f"- id `{s['id']}`, created {_t(s['created'])}, updated {_t(s['updated'])}")
    mc = s["market_calendar"]
    a(f"- trading day: {mc['is_trading_day']}, regular session {_t(mc['regular_open'])} to {_t(mc['regular_close'])}")
    a(f"- risk ruleset {s.get('risk_ruleset_version')}")
    for n in s.get("notes") or []:
        a(f"- {n}")
    a("\n## Runs")
    for r in rec["runs"]:
        a(f"- `{r['run_id']}` {r['phase']} at {_t(r['started'])}; market status {_v(r['market_status'])}. {r.get('summary') or ''}")
    dq = rec["data_quality"]
    if dq.get("notes"):
        a(f"\n**Recommendation (latest run):** {dq['notes'][0]}\n")

    ps = rec["premarket_scan"]
    a(f"## Premarket scan (`{ps['run_id']}`)")
    for sc in ps["screens"]:
        rows = ", ".join(f"{x['ticker']} {_v(x.get('change_pct'))}%" for x in sc["rows"][:8]) or sc.get("status_reason") or "-"
        a(f"- **{sc['name']}** [{sc['status']}]: {rows}")
    for e in ps["earnings_reports"]:
        a(f"- earnings {e['ticker']} {e['fiscal_period']}: EPS {_v(e['eps_actual'])} vs {_v(e['eps_estimate'])}, "
          f"revenue {_v(e['revenue_actual_usd_bn'])}bn vs {_v(e['revenue_estimate_usd_bn'])}bn, "
          f"premarket {_v(e['premarket_change_pct'])}%")
    g = rec["global_snapshot"]["instruments"]
    if g:
        a("- global: " + ", ".join(f"{i['symbol']} {_v(i['change_pct'])}%" for i in g))

    a("\n## Event calendar")
    for e in rec["event_calendar"]["events"]:
        a(f"- `{e['event_id']}` {e['kind']} {e.get('underlying') or ''} at {_v(e['scheduled_at'])} ({e['timing']})")
    for c in rec["event_calendar"]["coverage"]:
        a(f"- coverage {c['kind']} {c.get('underlying') or '(macro)'}: checked through {_v(c['checked_through'])}")

    a("\n## Candidates")
    for c in rec["candidates"]:
        sn = c["snapshots"][-1]
        q = sn["quote"]
        a(f"### {c['underlying']} - {c.get('company_name', '')}")
        a(f"- last {_v(q['last'])}, bid {_v(q['bid'])}, ask {_v(q['ask'])}, change {_v(q['change_pct'])}% "
          f"(run `{sn['run_id']}`); IV {'MISSING' if not sn.get('iv') else _v(sn['iv']['iv30_current'])}")
        for cat in c["catalysts"]:
            a(f"- catalyst: {_v(cat)} [{cat.get('source_id')}]")
        for sl in sn["option_chain_slices"]:
            a(f"- chain {sl['expiry']}: {len(sl['contracts'])} contracts{'; ' + sl['slice_note'] if sl.get('slice_note') else ''}")

    a("\n## Tickets (HYPOTHETICAL - paper only, not orders)")
    for t in rec["tickets"]:
        a(f"### `{t['ticket_id']}` {t['strategy']} ({t.get('direction', '')})")
        a(f"- {t['label']}")
        for lg in t["legs"]:
            a(f"  - {lg['side']} {lg['ratio']}x {lg['contract_id']} {lg['type']} {lg['strike']} exp {lg['expiry']}")
        a(f"- qty {t['quantity']}, limit {t['limit_price']['amount']} {t['limit_price']['price_type']}; "
          f"pricing {t['pricing']['status']}: {t['pricing'].get('note') or ''}")
        a(f"- max loss ${t['max_loss']['amount_usd']}, max profit ${t['max_profit']['amount_usd']}, "
          f"hold {_t(t['hold_window']['start'])} to {_t(t['hold_window']['end'])}")
        for alt in t["dm_alternatives"]:
            a(f"  - alternative {alt['alt_id']} {alt['decision']}: {alt['reason']}")

    a("\n## Risk Officer reviews")
    for r in rec["risk_reviews"]:
        o = r["overall"]
        a(f"### `{r['review_id']}` -> **{o['verdict']}** (run `{r['run_id']}`, {_t(r['evaluated_at'])})")
        a(f"- blocking: {', '.join(o['blocking_rule_ids']) or '-'}; flags: {', '.join(o['flag_rule_ids']) or '-'}; "
          f"approval required: {o['approval_required']}")
        a("\n| rule | sev | result | measured | threshold | reason |\n|---|---|---|---|---|---|")
        for x in r["results"]:
            a(f"| {x['rule_id']} | {x['severity']} | {x['result']} | {x['measured']} | {x['threshold']} | "
              f"{str(x['reason']).replace('|', '/')} |")

    a("\n## Watcher rules (inactive until approval)")
    for w in rec["watcher_rules"]:
        a(f"- `{w['watch_id']}` {w['kind']} -> {w['action']} ({w.get('note') or ''})")
    if not rec["watcher_rules"]:
        a("- none (no ticket passed the Risk Officer)")

    a("\n## Idea log")
    for i in rec["idea_log"]:
        a(f"- `{i['idea_id']}` **{i['current_status']}**: " +
          " -> ".join(f"{h['status']} ({h['actor']})" for h in i["history"]))

    a(f"\n## Data quality\n- missing {len(dq['missing'])}, stale {len(dq['stale'])}, conflicting {len(dq['conflicting'])}")
    for p in dq["conflicting"]:
        a(f"  - conflicting: `{p}`")
    for n in (dq.get("notes") or [])[1:]:
        a(f"- {n}")

    if trace:
        a("\n## Hand-off trace (not part of schema v1; from the .trace.json file)")
        for rt in trace.runs:
            v = rt.validation
            a(f"### run `{rt.run_id}` ({rt.data_mode}); validate_session: "
              f"{'OK' if not v.get('errors') else str(len(v['errors'])) + ' errors'}")
            for st in rt.steps:
                a(f"{st.seq}. **{st.stage}** ({st.agent}, owner {st.owner}) {st.duration_ms:.0f} ms"
                  f"{' - ' + st.verdict if st.verdict else ''}{' ERROR ' + st.error if st.error else ''}")
    return "\n".join(L) + "\n"
