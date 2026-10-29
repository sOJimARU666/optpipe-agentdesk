"""Orchestrator: Discovery -> Research -> Planner -> RiskOfficer -> Watcher, one *run* at a time.

Owner: Moodle (flow) / Poodle (code). Stages only see typed contracts. Every run is appended to the
schema-v1 session record (several runs per session: premarket, preopen, postopen...), and every
hand-off is written to a separate trace file. PAPER ONLY: nothing here talks to a broker.
"""
from __future__ import annotations

import copy
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Literal, Optional

import yaml
from pydantic import BaseModel, ConfigDict, Field

from .agent import Agent
from .config import Settings
from .discovery import DiscoveryAgent
from .discovery.agent import SETTINGS_SRC
from .models.contracts import (DiscoveryInput, ManualCandidate, ManualCoverage, ManualEvent, ManualSource,
                               PlannerInput, PlannerParams, ResearchInput, RiskInput, WatcherInput)
from .models.schema_v1 import SessionRecordV1
from .models.session import HandoffTrace, RunTrace, SessionTrace
from .planner import PlannerAgent
from .providers.base import Fetcher
from .providers.fetchers import FixtureFetcher, HttpFetcher
from .providers.finviz import FinvizScreener
from .providers.global_snapshot import RecordGlobalSnapshot, UnavailableGlobalSnapshot
from .providers.nasdaq import NasdaqProvider
from .records.fields import BERLIN, F, MISS, NY, iso, ny_at, slug, tspair
from .records.idea_log import apply_reviews, quality_index
from .research import ResearchAgent
from .risk.agent import RiskOfficerAgent
from .risk.engine import Calendar
from .risk.ruleset import Ruleset, load_ruleset
from .spec import SpecFiles, discover

SCHEMA_VERSION = "1.0.0"
MODE = "paper"   # the only mode the schema allows; there is no live mode anywhere in this package


class RunSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    run_id: Optional[str] = None
    phase: Optional[str] = None
    summary: Optional[str] = None


class SessionInputs(BaseModel):
    """Per-run inputs with no automated source yet (curated by Doodle / Eugene), each value sourced."""
    model_config = ConfigDict(extra="forbid")
    as_of: Optional[datetime] = None
    run: RunSpec = Field(default_factory=RunSpec)
    global_snapshot_record: Optional[str] = None      # schema-v1 JSON; "spec:" prefix = Doodle's spec dir
    sources: list[ManualSource] = Field(default_factory=list)
    manual_candidates: list[ManualCandidate] = Field(default_factory=list)
    events: list[ManualEvent] = Field(default_factory=list)
    coverage: list[ManualCoverage] = Field(default_factory=list)
    earnings_reports: list[dict] = Field(default_factory=list)


def load_inputs(path: str | Path | None) -> SessionInputs:
    if not path:
        return SessionInputs()
    return SessionInputs.model_validate(yaml.safe_load(Path(path).read_text()) or {})


def phase_for(t: datetime, cal: Calendar) -> str:
    """Schema run phase from the NY clock (market logic is always in America/New_York)."""
    n = t.astimezone(NY)
    if not cal.is_trading_day(n.date()):
        return "afterhours"
    o, c = ny_at(n.date(), cal.open), cal.close_dt(n.date())
    if n < o - timedelta(hours=1):
        return "premarket"
    if n < o:
        return "preopen"
    if n < o + timedelta(minutes=15):
        return "open"
    return "intraday" if n < c else "afterhours"


@dataclass
class Pipeline:
    settings: Settings
    fetcher: Fetcher
    data_mode: Literal["fixtures", "live"]
    global_provider: Any


def resolve_record_path(ref: str, base: Path, spec: Optional[SpecFiles]) -> Optional[Path]:
    if ref.startswith("spec:"):
        return (spec.root / ref[5:]) if spec else None
    return base / ref


def build_pipeline(settings: Settings, fixtures_dir: Optional[Path], inputs: SessionInputs,
                   as_of: datetime) -> Pipeline:
    spec = discover(settings.spec_path)
    if fixtures_dir is not None:
        fetcher: Fetcher = FixtureFetcher(fixtures_dir, fetched_at=as_of)
        ref = inputs.global_snapshot_record
        p = resolve_record_path(ref, fixtures_dir, spec) if ref else None
        gp = RecordGlobalSnapshot(p) if p else UnavailableGlobalSnapshot(settings.discovery.global_indexes)
        return Pipeline(settings, fetcher, "fixtures", gp)
    # live: read-only public GETs (Finviz, Nasdaq). Global snapshot has no live provider yet.
    return Pipeline(settings, HttpFetcher(settings.http.user_agent, settings.http.timeout_s), "live",
                    UnavailableGlobalSnapshot(settings.discovery.global_indexes))


def _merge_by(old: list[dict], new: list[dict], key: str) -> list[dict]:
    ids = {x[key] for x in old}
    return old + [x for x in new if x[key] not in ids]


class Orchestrator:
    def __init__(self, pipeline: Pipeline):
        self.p = pipeline
        s = pipeline.settings
        self.spec = discover(s.spec_path)
        self.base_rules, self.rule_notes = load_ruleset(self.spec)
        self.rules: Ruleset = self.base_rules.with_overrides(s.risk_overrides)
        self.nasdaq = NasdaqProvider(pipeline.fetcher)
        self.planner = PlannerAgent()
        self.risk = RiskOfficerAgent(self.rules, s.risk_overrides or None)
        from .watcher import WatcherAgent
        self.watcher = WatcherAgent()
        self.steps: list[HandoffTrace] = []
        self.run_id = ""

    # ---- trace ----
    def _step(self, agent: Agent, inp):
        started = datetime.now(timezone.utc)
        t0 = time.perf_counter()
        try:
            out, err = agent(inp), None
        except Exception as e:  # recorded in the trace, then re-raised
            out, err = None, f"{type(e).__name__}: {e}"
        self.steps.append(HandoffTrace(
            seq=len(self.steps) + 1, run_id=self.run_id, stage=agent.stage, agent=agent.name, owner=agent.owner,
            started_at=started, duration_ms=round((time.perf_counter() - t0) * 1000, 2),
            inputs_summary=agent.summarize_input(inp),
            outputs_summary=agent.summarize_output(out) if out is not None else {},
            verdict=agent.verdict(out) if out is not None else None, error=err))
        if err:
            raise RuntimeError(f"stage {agent.stage} failed: {err}")
        return out

    # ---- session-level pieces ----
    def _session_block(self, as_of: datetime, created: Optional[dict]) -> dict:
        s, cal = self.p.settings, Calendar(self.rules.config.market_hours)
        d = as_of.astimezone(NY).date()
        notes = [
            "PAPER ONLY: no broker integration; tickets are HYPOTHETICAL data records, never orders.",
            f"data mode: {self.p.data_mode}" + (" (fixture replay: created/updated = replayed as_of)"
                                                if self.p.data_mode == "fixtures" else ""),
            f"broker (informational, never connected): {s.broker.name}; trade style: {s.trade_style}",
            f"watchlist: {s.watchlist or 'PLACEHOLDER (empty)'}; sectors: {s.sectors or 'PLACEHOLDER (empty)'}",
            *self.rule_notes,
            "PLACEHOLDER: NYSE 2026 holidays/early closes in spec risk_rules.yaml are unverified",
            "PLACEHOLDER: no FOMC/NFP calendar source; their coverage is recorded as missing",
        ]
        if s.risk_overrides:
            notes.append(f"risk config overrides: {s.risk_overrides}")
        o, c = ny_at(d, cal.open), cal.close_dt(d)
        off = int((as_of.astimezone(BERLIN).utcoffset() - as_of.astimezone(NY).utcoffset()).total_seconds() // 3600)
        return {"id": f"sess-{d.isoformat()}", "date": d.isoformat(), "mode": MODE,
                "created": created or tspair(as_of), "updated": tspair(as_of),
                "risk_ruleset_version": self.rules.ruleset_version,
                "market_calendar": {"timezone": "America/New_York", "is_trading_day": cal.is_trading_day(d),
                                    "early_close": d in cal.early_closes, "regular_open": tspair(o),
                                    "regular_close": tspair(c), "ny_berlin_offset_hours": off},
                "notes": notes}

    def _market_status(self, symbols: list[str], as_of: datetime) -> dict:
        for sym in symbols:
            try:
                q = self.nasdaq.get_quote(sym)
            except Exception:
                continue
            if q.market_status:
                t = q.fetched_at or as_of
                return F(q.market_status, f"src-nasdaq-{slug(sym)}-info-{t.astimezone(NY):%H%M}", t, "ok")
        return MISS(note="no quote source reported a market status")

    # ---- one run ----
    def run(self, inputs: SessionInputs, as_of: datetime, existing: Optional[dict] = None
            ) -> tuple[dict, RunTrace, list[str]]:
        s = self.p.settings
        cal = Calendar(self.rules.config.market_hours)
        # The inputs file's run block describes the run at inputs.as_of; a different --as-of is a new run.
        own = inputs.run if as_of == inputs.as_of else RunSpec()
        phase = own.phase or phase_for(as_of, cal)
        run_id = own.run_id or f"run-{phase}-{as_of.astimezone(NY):%H%M}"
        if existing and any(r["run_id"] == run_id for r in existing["runs"]):
            run_id = f"{run_id}-{as_of.astimezone(NY):%H%M%S}"
        self.run_id, self.steps, self.run_spec = run_id, [], own
        notes: list[str] = []

        manual_sources = [{"id": m.id, "url": m.url, "kind": m.kind, "fetched_at": iso(m.fetched_at or as_of),
                           **({"publisher": m.publisher} if m.publisher else {}),
                           **({"title": m.title} if m.title else {})} for m in inputs.sources]
        settings_src = {"id": SETTINGS_SRC, "url": "urn:optpipe:config:settings.yaml", "kind": "user_input",
                        "fetched_at": iso(as_of), "publisher": "Eugene Ward (settings.yaml)"}

        discovery = DiscoveryAgent(FinvizScreener(self.p.fetcher, s.discovery.finviz_filters), self.p.global_provider,
                                   s.discovery.finviz_filters, inputs.earnings_reports, [settings_src, *manual_sources])
        disc = self._step(discovery, DiscoveryInput(
            as_of=as_of, run_id=run_id, screens=s.discovery.screens, watchlist=s.watchlist,
            manual_candidates=inputs.manual_candidates, max_candidates=s.discovery.max_candidates))
        notes += disc.notes

        source_times = {x["id"]: datetime.fromisoformat(x["fetched_at"]) for x in [settings_src, *manual_sources]}
        research = ResearchAgent(self.nasdaq, self.nasdaq, self.nasdaq)
        res = self._step(research, ResearchInput(
            as_of=as_of, run_id=run_id, picks=disc.picks, events=inputs.events, coverage=inputs.coverage,
            source_times=source_times, strike_window_pct=s.research.strike_window_pct))
        notes += res.notes

        pc = s.planner.model_dump()
        pc["max_risk_per_trade_usd"] = pc["max_risk_per_trade_usd"] or float(self.rules.config.per_trade_max_loss_usd)
        params = PlannerParams(**pc, entry_not_before=s.market.entry_not_before,
                               max_leg_spread_pct=float(self.rules.config.max_spread_pct_of_mid) * 100)
        plan = self._step(self.planner, PlannerInput(
            as_of=as_of, run_id=run_id, candidates=res.candidates, event_calendar=res.event_calendar,
            bias=res.bias, params=params))
        notes += plan.notes

        dump = lambda xs: [x.model_dump(mode="json", exclude_unset=True) for x in xs]  # noqa: E731
        rec = self._assemble(existing, as_of, run_id, phase, inputs, disc, res, plan, dump,
                             self._market_status([p.symbol for p in disc.picks], as_of))

        risk_out = self._step(self.risk, RiskInput(record=SessionRecordV1.model_validate(rec), run_id=run_id,
                                                   evaluated_at=as_of))
        reviews = [r.model_dump(mode="json", exclude_unset=True) for r in risk_out.risk_reviews]
        for rv, src in zip(reviews, risk_out.risk_reviews):     # keep explicit nulls the schema requires
            for d, s_ in zip(rv["results"], src.results):
                d["measured"], d["threshold"] = s_.measured, s_.threshold
        rec["risk_reviews"] = _merge_by(rec["risk_reviews"], reviews, "review_id")

        w = self._step(self.watcher, WatcherInput(tickets=rec["tickets"], risk_reviews=reviews))
        rec["watcher_rules"] = _merge_by(rec["watcher_rules"], dump(w.watcher_rules), "watch_id")

        rec["idea_log"], recommendation = apply_reviews(rec["idea_log"], reviews, as_of)
        rec["data_quality"] = {**quality_index(rec), "notes": [recommendation, *notes][:50]}
        SessionRecordV1.model_validate(rec)

        validation = self.validate(rec)
        trace = RunTrace(run_id=run_id, as_of=as_of, data_mode=self.p.data_mode, steps=self.steps,
                         recommendation=recommendation, notes=notes, validation=validation)
        return rec, trace, notes

    def _assemble(self, existing, as_of, run_id, phase, inputs, disc, res, plan, dump, market_status) -> dict:
        new_sources = [*dump(disc.sources), *dump(res.sources)]
        run = {"run_id": run_id, "phase": phase, "started": tspair(as_of), "market_status": market_status,
               "summary": self.run_spec.summary or f"{phase} run ({self.p.data_mode})"}
        if existing is None:
            rec: dict[str, Any] = {
                "schema_version": SCHEMA_VERSION, "session": self._session_block(as_of, None),
                "sources": [], "runs": [], "premarket_scan": disc.premarket_scan.model_dump(mode="json", exclude_unset=True),
                "global_snapshot": disc.global_snapshot.model_dump(mode="json", exclude_unset=True),
                "event_calendar": {"events": [], "coverage": []}, "candidates": [], "tickets": [],
                "risk_reviews": [], "approvals": [], "paper_positions": [], "watcher_rules": [], "alerts": [],
                "idea_log": [], "data_quality": {"missing": [], "stale": [], "conflicting": []}}
        else:
            rec = copy.deepcopy(existing)
            rec["session"] = {**self._session_block(as_of, existing["session"]["created"]),
                              "notes": existing["session"].get("notes") or []}
            # premarket_scan / global_snapshot are single sections in schema v1: the first run keeps them.
        rec["sources"] = _merge_by(rec["sources"], new_sources, "id")
        rec["runs"].append(run)
        ev = res.event_calendar.model_dump(mode="json", exclude_unset=True)
        rec["event_calendar"]["events"] = _merge_by(rec["event_calendar"]["events"], ev["events"], "event_id")
        newcov = {(c["kind"], c["underlying"]): c for c in ev["coverage"]}
        rec["event_calendar"]["coverage"] = [newcov.pop((c["kind"], c["underlying"]), c)
                                             for c in rec["event_calendar"]["coverage"]] + list(newcov.values())
        by_id = {c["candidate_id"]: c for c in rec["candidates"]}
        for c in dump(res.candidates):
            if c["candidate_id"] in by_id:
                by_id[c["candidate_id"]]["snapshots"] += c["snapshots"]
            else:
                rec["candidates"].append(c)
        rec["tickets"] = _merge_by(rec["tickets"], dump(plan.tickets), "ticket_id")
        rec["idea_log"] = _merge_by(rec["idea_log"], dump(plan.idea_log), "idea_id")
        return rec

    def validate(self, rec: dict) -> dict:
        mod = self.spec.load_module("session_validator") if self.spec else None
        if mod is None:
            return {"validator": None, "errors": [], "note": "spec validate_session.py not found; pydantic only"}
        errs = list(mod.validate(copy.deepcopy(rec)))
        return {"validator": str(self.spec.path("session_validator")), "errors": errs}


def run_session(settings: Settings, inputs: SessionInputs, as_of: datetime, fixtures_dir: Optional[Path],
                existing: Optional[dict] = None, existing_trace: Optional[SessionTrace] = None
                ) -> tuple[dict, SessionTrace]:
    orch = Orchestrator(build_pipeline(settings, fixtures_dir, inputs, as_of))
    rec, rt, _ = orch.run(inputs, as_of, existing)
    trace = existing_trace or SessionTrace(session_id=rec["session"]["id"])
    trace.runs.append(rt)
    return rec, trace
