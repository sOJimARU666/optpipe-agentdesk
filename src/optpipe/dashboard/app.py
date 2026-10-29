"""FastAPI app: Today / Tickets / Agent timeline / History over an out/ directory.

GET-only by construction (tests/test_dashboard.py asserts it). `export=True` renders the same templates
for the static snapshots in docs/dashboard/ (relative links, no HTMX round-trips).
"""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, PlainTextResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from .data import OutDir, Session, berlin, clock, compact, fval, ftitle, pair

HERE = Path(__file__).parent
PAGES = ("today", "tickets", "timeline", "history")
VERDICT_ORDER = {"VETO": 0, "UNKNOWN": 1, "FLAG": 2, "PASS": 3}
_RAW_OK = (".json", ".md", ".sha256")


def _templates(export: bool) -> Jinja2Templates:
    t = Jinja2Templates(directory=str(HERE / "templates"))
    t.env.filters.update(berlin=berlin, clock=clock, pair=pair, fval=fval, ftitle=ftitle, compact=compact)
    t.env.globals.update(export=export, PAGES=PAGES)
    return t


def link(page: str, date: Optional[str], export: bool, **q: str) -> str:
    if export:
        return f"{page}.html"
    if page == "history":
        return "/history"
    base = f"/s/{date}/{page}"
    return base + ("?" + "&".join(f"{k}={v}" for k, v in q.items() if v) if any(q.values()) else "")


# ---------------------------------------------------------------- view models
def timeline_model(s: Session) -> list[dict]:
    out = []
    for run in (s.trace or {}).get("runs", []):
        steps = sorted(run.get("steps", []), key=lambda x: x["seq"])
        if not steps:
            continue
        t0 = datetime.fromisoformat(steps[0]["started_at"].replace("Z", "+00:00"))
        rows, end = [], 0.0
        for st in steps:
            off = (datetime.fromisoformat(st["started_at"].replace("Z", "+00:00")) - t0).total_seconds() * 1000
            rows.append({**st, "offset_ms": off})
            end = max(end, off + (st.get("duration_ms") or 0))
        end = end or 1.0
        for r in rows:
            r["left_pct"] = round(100 * r["offset_ms"] / end, 2)
            r["width_pct"] = round(max(1.2, 100 * (r.get("duration_ms") or 0) / end), 2)
        handoffs = [{"from": a["stage"], "to": b["stage"], "payload": a.get("outputs_summary") or {}}
                    for a, b in zip(rows, rows[1:])]
        rec_run = next((r for r in s.runs if r["run_id"] == run["run_id"]), {})
        verdicts = Counter(rv["overall"]["verdict"] for rv in s.record.get("risk_reviews", [])
                           if rv["run_id"] == run["run_id"])
        out.append({"run": run, "rec_run": rec_run, "steps": rows, "total_ms": round(end, 1),
                    "handoffs": handoffs, "verdicts": dict(sorted(verdicts.items(), key=lambda kv: VERDICT_ORDER[kv[0]])),
                    "validation": run.get("validation") or {}})
    return out


def tickets_model(s: Session) -> list[dict]:
    reviews = defaultdict(list)
    order = {r["run_id"]: i for i, r in enumerate(s.runs)}
    for rv in s.record.get("risk_reviews", []):
        reviews[rv["ticket_id"]].append(rv)
    out = []
    for t in s.record.get("tickets", []):
        rvs = sorted(reviews.get(t["ticket_id"], []), key=lambda r: order.get(r["run_id"], 99))
        out.append({"t": t, "reviews": rvs, "latest": rvs[-1] if rvs else None})
    return out


def history_model(od: OutDir) -> tuple[list[dict], list[dict]]:
    sessions, ideas = [], []
    for d in od.dates():
        try:
            s = od.load(d)
        except (KeyError, ValueError) as e:                      # unreadable file: show it, don't crash
            sessions.append({"date": d, "error": str(e)})
            continue
        v = Counter(rv["overall"]["verdict"] for rv in s.record.get("risk_reviews", []))
        tr = (s.trace or {}).get("runs", [])
        sessions.append({"date": d, "runs": s.runs, "tickets": len(s.record.get("tickets", [])),
                         "verdicts": dict(sorted(v.items(), key=lambda kv: VERDICT_ORDER[kv[0]])),
                         "recommendation": tr[-1].get("recommendation") if tr else None,
                         "integrity": s.integrity, "origin": s.origin})
        for i in s.record.get("idea_log", []):
            ideas.append({"date": d, **i})
    return sessions, ideas


# ---------------------------------------------------------------- app
def create_app(out_dir: Path | str, export: bool = False) -> FastAPI:
    od = OutDir(out_dir)
    tpl = _templates(export)
    app = FastAPI(title="optpipe dashboard (paper only)", docs_url=None, redoc_url=None, openapi_url=None)
    app.state.out = od
    app.mount("/static", StaticFiles(directory=str(HERE / "static")), name="static")

    def session(date: str) -> Session:
        try:
            return od.load(date)
        except KeyError:
            raise HTTPException(404, f"no session-{date}.json in {od.root}")

    def render(request: Request, name: str, page: str, s: Optional[Session], **ctx: Any) -> HTMLResponse:
        date = s.date if s else None
        nav = {p: link(p, date, export) for p in PAGES} if date else {"history": link("history", None, export)}
        return tpl.TemplateResponse(request, name, {"page": page, "s": s, "date": date, "nav": nav,
                                                    "dates": od.dates(), "out_dir": str(od.root), **ctx})

    @app.get("/healthz", response_class=PlainTextResponse)
    def healthz() -> str:
        return "ok (paper only, read-only)"

    @app.get("/", response_class=HTMLResponse)
    def index(request: Request):
        dates = od.dates()
        if dates:
            return RedirectResponse(link("today", dates[0], False), status_code=307)
        return render(request, "empty.html", "today", None)

    @app.get("/s/{date}", response_class=HTMLResponse)
    def session_root(date: str):
        session(date)
        return RedirectResponse(link("today", date, False), status_code=307)

    @app.get("/s/{date}/today", response_class=HTMLResponse)
    def today(request: Request, date: str, run: Optional[str] = None):
        s = session(date)
        run_id = run or (s.runs[-1]["run_id"] if s.runs else None)
        if run_id and not any(r["run_id"] == run_id for r in s.runs):
            raise HTTPException(404, f"no run {run_id} in {date}")
        cands = [{"c": c, "snap": next((x for x in c.get("snapshots", []) if x.get("run_id") == run_id),
                                      (c.get("snapshots") or [None])[-1])} for c in s.record.get("candidates", [])]
        ev = s.record.get("event_calendar", {})
        return render(request, "today.html", "today", s, run_id=run_id,
                      run_links={r["run_id"]: link("today", date, export, run=r["run_id"]) for r in s.runs},
                      scan=s.per_run("premarket_scans", run_id), glob=s.per_run("global_snapshots", run_id),
                      cands=cands, events=ev.get("events", []), coverage=ev.get("coverage", []),
                      dq={k: len(v) for k, v in s.record.get("data_quality", {}).items() if k != "notes"})

    @app.get("/s/{date}/tickets", response_class=HTMLResponse)
    def tickets(request: Request, date: str):
        s = session(date)
        return render(request, "tickets.html", "tickets", s, tickets=tickets_model(s))

    @app.get("/s/{date}/review/{review_id}", response_class=HTMLResponse)
    def review(request: Request, date: str, review_id: str):
        s = session(date)
        rv = next((r for r in s.record.get("risk_reviews", []) if r["review_id"] == review_id), None)
        if rv is None:
            raise HTTPException(404, f"no review {review_id}")
        return tpl.TemplateResponse(request, "_review.html", {"rv": rv, "s": s})

    @app.get("/s/{date}/timeline", response_class=HTMLResponse)
    def timeline(request: Request, date: str):
        s = session(date)
        return render(request, "timeline.html", "timeline", s, runs=timeline_model(s))

    @app.get("/s/{date}/step/{run_id}/{seq}", response_class=HTMLResponse)
    def step(request: Request, date: str, run_id: str, seq: int):
        s = session(date)
        tr = s.trace_run(run_id)
        st = next((x for x in (tr or {}).get("steps", []) if x["seq"] == seq), None)
        if st is None:
            raise HTTPException(404, f"no step {run_id}/{seq}")
        return tpl.TemplateResponse(request, "_step.html", {"st": st})

    @app.get("/history", response_class=HTMLResponse)
    def history(request: Request):
        sessions, ideas = history_model(od)
        dates = od.dates()
        s = od.load(dates[0]) if dates else None
        return render(request, "history.html", "history", s, sessions=sessions, ideas=ideas,
                      session_links={d: link("today", d, export) for d in dates})

    @app.get("/files/{name}")
    def raw(name: str):
        p = od.root / name
        if not (name.startswith("session-") and name.endswith(_RAW_OK) and "/" not in name and p.is_file()):
            raise HTTPException(404, name)
        mt = "application/json" if name.endswith(".json") else "text/plain; charset=utf-8"
        return FileResponse(p, media_type=mt)

    return app
