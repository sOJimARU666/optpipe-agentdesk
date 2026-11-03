"""Orchestrator: Discovery -> Research -> Planner -> RiskOfficer -> Watcher, one *run* at a time.

Owner: Moodle (flow) / Poodle (code). Stages only see typed contracts. Every run is appended to the
schema-v1 session record (several runs per session: premarket, preopen, postopen...), and every
hand-off is written to a separate trace file. PAPER ONLY: nothing here talks to a broker.
"""
from __future__ import annotations

import copy
import os
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Literal, Optional

import yaml
from pydantic import BaseModel, ConfigDict, Field

from .agent import Agent
from .config import REPO_ROOT, Settings, repo_path
from .discovery import DiscoveryAgent
from .discovery.agent import SETTINGS_SRC
from .models.contracts import (DiscoveryInput, ManualCandidate, ManualCoverage, ManualEvent, ManualSource,
                               PlannerInput, PlannerParams, ResearchInput, RiskInput, WatcherInput)
from .models.schema_spec import SessionRecordSpec
from .models.schema_spec import SessionRecordSpec
from .records.compat import from_v1
from .scenario import scenario_note
from .models.session import HandoffTrace, RunTrace, SessionTrace
from .planner import PlannerAgent
from .providers.base import Fetcher
from .providers.cache import CachedFetcher
from .providers.fetchers import FixtureFetcher, HttpFetcher
from .providers.macro import LiveMacroCalendars, ReplayMacroCalendars, bls_user_agent
from .providers.treasury import LiveTreasuryRates, ReplayTreasuryRates
from .research.iv import IvHistoryStore
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

SCHEMA_VERSION = "1.1.6"   # Doodle's spec 1.1.6 (pinned 2026-11-03, SOL-ACCEPT-001 G12: the 1.1.3 checks plus the open INV-06 fill window, INV-05/06-TIME-*, INV-17 and the fail-closed INV-16): written as-is; compat.to_v1 only for v1 readers
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


PREMARKET_START = "04:00"


def phase_for(t: datetime, cal: Calendar) -> str:
    """Run phase from the NY clock (market logic is always in America/New_York).

    closed    weekend / NYSE holiday, before 04:00, or at/after the regular close
              (16:00; 13:00 on early-close days)                      [Doodle's ruling, v1.1]
    premarket 04:00 .. open-1h | preopen open-1h .. open | open open .. open+15m | intraday .. close
    Records are schema 1.1.6 (v1.1 shape), so "closed" is written as-is (Doodle's INV-10 checks it against the NY clock).
    """
    n = t.astimezone(NY)
    if not cal.is_trading_day(n.date()):
        return "closed"
    o, c = ny_at(n.date(), cal.open), cal.close_dt(n.date())
    if n < ny_at(n.date(), PREMARKET_START) or n >= c:
        return "closed"
    if n < o - timedelta(hours=1):
        return "premarket"
    if n < o:
        return "preopen"
    if n < o + timedelta(minutes=15):
        return "open"
    return "intraday"


@dataclass
class Pipeline:
    settings: Settings
    fetcher: Fetcher
    data_mode: Literal["fixtures", "live"]
    global_provider: Any
    macro: Any = None            # MacroCalendarProvider
    rates: Any = None            # RateProvider
    iv_store: Optional[IvHistoryStore] = None


def state_dir(settings: Settings) -> Path:
    return repo_path(os.environ.get("OPTPIPE_STATE_DIR", settings.iv.state_dir))


def cache_dir(settings: Settings) -> Path:
    return repo_path(os.environ.get("OPTPIPE_CACHE_DIR", settings.sources.cache_dir))


def resolve_record_path(ref: str, base: Path, spec: Optional[SpecFiles]) -> Optional[Path]:
    if ref.startswith("spec:"):
        return (spec.root / ref[5:]) if spec else None
    return base / ref


def build_pipeline(settings: Settings, fixtures_dir: Optional[Path], inputs: SessionInputs,
                   as_of: datetime) -> Pipeline:
    spec = discover(settings.spec_path)
    sc = settings.sources
    if fixtures_dir is not None:
        fetcher: Fetcher = FixtureFetcher(fixtures_dir, fetched_at=as_of)
        ref = inputs.global_snapshot_record
        p = resolve_record_path(ref, fixtures_dir, spec) if ref else None
        gp = RecordGlobalSnapshot(p) if p else UnavailableGlobalSnapshot(settings.discovery.global_indexes)
        replay = Path(fixtures_dir) / sc.replay_file
        macro = ReplayMacroCalendars(replay) if replay.exists() else None
        rates = ReplayTreasuryRates(replay, sc.treasury_tenor) if replay.exists() else None
        return Pipeline(settings, fetcher, "fixtures", gp, macro, rates,
                        IvHistoryStore(state_dir(settings) / "fixtures" / "iv_history"))
    # live: read-only public GETs (Finviz, Nasdaq, Fed, BLS, Treasury). Global snapshot has no live provider yet.
    http = HttpFetcher(settings.http.user_agent, settings.http.timeout_s)
    cached = CachedFetcher(http, cache_dir(settings), sc.cache_ttl_hours)
    bls_ua = bls_user_agent(sc.bls_contact_email)            # None (skip BLS) unless an email is configured
    bls = CachedFetcher(HttpFetcher(bls_ua, settings.http.timeout_s), cache_dir(settings),
                        sc.cache_ttl_hours) if bls_ua else None
    return Pipeline(settings, http, "live", UnavailableGlobalSnapshot(settings.discovery.global_indexes),
                    LiveMacroCalendars(cached, sc.fomc_url, bls, sc.bls_ics_url, sc.fred_calendar_url,
                                       sc.fred_release_ids),
                    LiveTreasuryRates(cached, sc.treasury_url, sc.treasury_tenor),
                    IvHistoryStore(state_dir(settings) / "live" / "iv_history"))


def _merge_by(old: list[dict], new: list[dict], key: str) -> list[dict]:
    ids = {x[key] for x in old}
    return old + [x for x in new if x[key] not in ids]


def _rename_sources(new: dict, old_sources: list[dict], as_of: datetime) -> dict:
    old = {x["id"]: x["fetched_at"] for x in old_sources}
    ren: dict[str, str] = {}
    for src in new["sources"]:
        sid = src["id"]
        if sid in old and old[sid] != src["fetched_at"]:
            t = datetime.fromisoformat(src["fetched_at"]).astimezone(NY)
            cand = f"{sid}-{t:%H%M}"
            if cand in old:
                cand = f"{sid}-{t:%H%M%S}"
            ren[sid] = cand
    if not ren:
        return new

    def walk(o):
        if isinstance(o, dict):
            return {k: walk(v) for k, v in o.items()}
        if isinstance(o, list):
            return [walk(v) for v in o]
        return ren.get(o, o) if isinstance(o, str) else o
    return walk(new)


def scenario_max_loss(s, rules: Ruleset) -> tuple[float, Optional[str]]:
    """Planner max loss per idea: account size x risk % when the scenario defines both. If that differs from
    the Risk Officer's per_trade_max_loss_usd (e.g. an explicit risk_overrides), the planner never sizes above
    R04: it takes the smaller one and the mismatch is noted. tests/test_scenario.py pins the shipped config
    (25,000 x 2% = 500 = profiles.demo per_trade_max_loss_usd)."""
    ro = float(rules.config.per_trade_max_loss_usd) if rules.config.per_trade_max_loss_usd is not None else None
    derived = s.scenario.max_loss_per_idea_usd if s.scenario else None
    if derived is None:
        if ro is None:
            raise ValueError("no max loss per idea: set scenario account/risk % or per_trade_max_loss_usd")
        return ro, None
    if ro is not None and abs(derived - ro) > 0.005:
        return min(derived, ro), (f"WARNING scenario max loss ${derived:g} (= ${s.scenario.account_size_usd:g} x "
                                  f"{s.scenario.max_risk_per_idea_pct:g}%) != Risk Officer per_trade_max_loss_usd "
                                  f"${ro:g}; planner sizes to ${min(derived, ro):g}")
    return derived, None


class Orchestrator:
    def __init__(self, pipeline: Pipeline):
        self.p = pipeline
        s = pipeline.settings
        self.spec = discover(s.spec_path)
        self.base_rules, self.rule_notes = load_ruleset(self.spec)
        # cfg = spec defaults <- scenario's risk profile (Doodle's risk_rules.yaml `profiles`) <- risk_overrides
        sc = s.scenario
        self.profile = sc.risk_profile if sc else None
        self.effective_overrides = {**self.base_rules.profile_overrides(self.profile), **s.risk_overrides}
        self.rules: Ruleset = self.base_rules.with_profile(self.profile).with_overrides(s.risk_overrides)
        self.nasdaq = NasdaqProvider(pipeline.fetcher)
        self.planner = PlannerAgent()
        self.risk = RiskOfficerAgent(self.rules, self.effective_overrides or None)
        self.max_loss_per_idea, mismatch = scenario_max_loss(s, self.rules)
        if mismatch:
            self.rule_notes = [*self.rule_notes, mismatch]
        if self.profile:
            self.rule_notes = [*self.rule_notes, f"risk profile '{self.profile}' from spec risk_rules.yaml "
                               f"({self.base_rules.profiles[self.profile].get('label', '')})"]
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
            *[n for n in [scenario_note(s.scenario, s.broker.name, s.trade_style)] if n],
            *self.rule_notes,
            "PLACEHOLDER: NYSE 2026 holidays/early closes in spec risk_rules.yaml are unverified",
            ("macro calendars: " + ("hand-made replay snapshot from real captures (fixtures)"
                                    if self.p.data_mode == "fixtures" else "Fed + BLS schedule pages (live GET, cached)")
             if self.p.macro is not None else "macro calendars: none configured; FOMC/CPI/PPI/NFP coverage missing"),
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
                return F(q.market_status, f"src-nasdaq-{slug(sym)}-info-{as_of.astimezone(NY):%H%M}", t, "ok")  # same id as research (run hhmm)
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
        research = ResearchAgent(self.nasdaq, self.nasdaq, self.nasdaq, macro=self.p.macro, rates=self.p.rates,
                                 iv_store=self.p.iv_store, data_mode=self.p.data_mode,
                                 iv_target_days=s.iv.target_days, iv_min_history=s.iv.min_history_points)
        res = self._step(research, ResearchInput(
            as_of=as_of, run_id=run_id, picks=disc.picks, events=inputs.events, coverage=inputs.coverage,
            source_times=source_times, strike_window_pct=s.research.strike_window_pct))
        notes += res.notes

        pc = s.planner.model_dump()
        if not pc["max_risk_per_trade_usd"]:
            pc["max_risk_per_trade_usd"] = self.max_loss_per_idea
            sc = s.scenario
            if sc and sc.max_loss_per_idea_usd is not None and self.max_loss_per_idea == sc.max_loss_per_idea_usd:
                pc["risk_basis"] = (f"Per-trade max loss ${sc.max_loss_per_idea_usd:g} = {sc.max_risk_per_idea_pct:g}% of the "
                                    f"{'FICTIONAL ' if sc.fictional else ''}${sc.account_size_usd:,.0f} paper account "
                                    f"(scenario '{sc.name}', not confirmed by Eugene)")
        lo, hi = self.rules.config.hold_min_days, self.rules.config.hold_max_days   # R13 DTE window, if any
        if lo is not None and pc["preferred_dte_min"] < lo:
            pc["preferred_dte_min"] = lo
        if hi is not None and pc["preferred_dte_max"] > hi:
            pc["preferred_dte_max"] = hi
            notes.append(f"planner DTE window clamped to {pc['preferred_dte_min']}-{hi} d by risk rules hold period (R13)")
        tick_rules = self.rules.raw_config.get("tick_rules")
        if tick_rules:
            spec_pp = sorted(str(x).upper() for x in tick_rules.get("penny_pilot") or [])
            if spec_pp and spec_pp != sorted(x.upper() for x in pc["penny_pilot"]):
                notes.append(f"penny-pilot list from spec tick_rules {spec_pp} (settings.yaml fallback "
                             f"{pc['penny_pilot']} ignored)")
        else:
            notes.append("spec risk_rules.yaml has no config.tick_rules; tick rounding uses the settings.yaml fallback")
        params = PlannerParams(**pc, tick_rules=tick_rules, entry_not_before=s.market.entry_not_before,
                               max_leg_spread_pct=float(self.rules.config.max_spread_pct_of_mid) * 100)
        plan = self._step(self.planner, PlannerInput(
            as_of=as_of, run_id=run_id, candidates=res.candidates, event_calendar=res.event_calendar,
            bias=res.bias, params=params))
        notes += plan.notes

        dump = lambda xs: [x.model_dump(mode="json", exclude_unset=True) for x in xs]  # noqa: E731
        rec = self._assemble(existing, as_of, run_id, phase, inputs, disc, res, plan, dump,
                             self._market_status([p.symbol for p in disc.picks], as_of))

        risk_out = self._step(self.risk, RiskInput(record=SessionRecordSpec.model_validate(rec), run_id=run_id,
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
        SessionRecordSpec.model_validate(rec)
        SessionRecordSpec.model_validate(rec)          # Doodle's schema 1.1.6

        validation = self.validate(rec)
        trace = RunTrace(run_id=run_id, as_of=as_of, data_mode=self.p.data_mode, steps=self.steps,
                         recommendation=recommendation, notes=notes, validation=validation)
        return rec, trace, notes

    def _assemble(self, existing, as_of, run_id, phase, inputs, disc, res, plan, dump, market_status) -> dict:
        one = lambda m: m.model_dump(mode="json", exclude_unset=True)  # noqa: E731
        new = {"sources": [*dump(disc.sources), *dump(res.sources)],
               "premarket_scans": {**one(disc.premarket_scan), "run_id": run_id},
               "global_snapshots": {**one(disc.global_snapshot), "run_id": run_id},
               "event_calendar": one(res.event_calendar), "candidates": dump(res.candidates),
               "tickets": dump(plan.tickets), "idea_log": dump(plan.idea_log)}
        run = {"run_id": run_id, "phase": phase, "started": tspair(as_of), "market_status": market_status,
               "summary": self.run_spec.summary or f"{phase} run ({self.p.data_mode})"}
        if existing is None:
            rec: dict[str, Any] = {
                "schema_version": SCHEMA_VERSION, "session": self._session_block(as_of, None),
                "sources": [], "runs": [], "premarket_scans": [], "global_snapshots": [],
                "event_calendar": {"events": [], "coverage": []}, "candidates": [], "tickets": [],
                "risk_reviews": [], "approvals": [], "paper_positions": [], "watcher_rules": [], "alerts": [],
                "idea_log": [], "data_quality": {"missing": [], "stale": [], "conflicting": []}}
        else:
            rec = from_v1(existing)
            rec["session"] = {**self._session_block(as_of, existing["session"]["created"]),
                              "notes": existing["session"].get("notes") or []}
            # A re-fetch of the same page in a later run is a different source: give it a time-suffixed id
            # (the first run keeps the plain id, matching Doodle's example), so INV-01/INV-09 hold per run.
            new = _rename_sources(new, rec["sources"], as_of)
        rec["sources"] = _merge_by(rec["sources"], new["sources"], "id")
        rec["runs"].append(run)
        # Per-run scan + global snapshot keyed by run_id (Doodle's ruling); the singular v1 sections = latest run.
        for plural, single in (("premarket_scans", "premarket_scan"), ("global_snapshots", "global_snapshot")):
            rec[plural] = [x for x in rec.get(plural, []) if x["run_id"] != run_id] + [new[plural]]
            rec[single] = new[plural]
        ev = new["event_calendar"]
        rec["event_calendar"]["events"] = _merge_by(rec["event_calendar"]["events"], ev["events"], "event_id")
        newcov = {(c["kind"], c["underlying"]): c for c in ev["coverage"]}
        rec["event_calendar"]["coverage"] = [newcov.pop((c["kind"], c["underlying"]), c)
                                             for c in rec["event_calendar"]["coverage"]] + list(newcov.values())
        by_id = {c["candidate_id"]: c for c in rec["candidates"]}
        for c in new["candidates"]:
            if c["candidate_id"] in by_id:
                by_id[c["candidate_id"]]["snapshots"] += c["snapshots"]
            else:
                rec["candidates"].append(c)
        rec["tickets"] = _merge_by(rec["tickets"], new["tickets"], "ticket_id")
        rec["idea_log"] = _merge_by(rec["idea_log"], new["idea_log"], "idea_id")
        return rec

    def validate(self, rec: dict) -> dict:
        """errors: the 1.1.6 record against Doodle's validator + schema (the INV-11 trace file check runs
        on the written file, see validate_file), plus optpipe's guards (records/guards.py). Fails closed: a
        missing, unimportable or crashing validator is an error, never an OK (FIX-SEC-001 P0-5)."""
        mod, err = load_validator(self.spec)
        if mod is None:
            return {"validator": None, "errors": [err], "note": "spec validator unavailable; record NOT validated"}
        from .config import display_path
        from .records.guards import check_record, merge
        where = display_path(self.spec.path("session_validator"))
        try:
            errs = run_validator(mod, copy.deepcopy(rec))
        except ValidatorCrashed as e:
            return {"validator": None, "errors": [str(e)], "note": "spec validator crashed; record NOT validated"}
        return {"validator": where, "errors": merge(errs, check_record(rec))}


VALIDATOR_MISSING = "SPEC-VALIDATOR-MISSING"
VALIDATOR_BROKEN = "SPEC-VALIDATOR-IMPORT-FAILED"
VALIDATOR_CRASHED = "SPEC-VALIDATOR-CRASHED"


class ValidatorCrashed(Exception):
    """Doodle's validate() raised: the record is NOT validated (the CLI's exit 2, 'crash'); callers fail closed."""


def run_validator(mod, rec: dict, **kw) -> list[str]:
    """mod.validate(rec) as a list of error strings. A crash raises ValidatorCrashed (spec 1.1.3 CLI contract:
    0 = valid, 1 = record errors, 2 = unreadable input or crash; any non-zero is FAIL)."""
    try:
        return list(mod.validate(rec, **kw))
    except Exception as e:  # noqa: BLE001 - any crash must fail closed
        raise ValidatorCrashed(f"{VALIDATOR_CRASHED}: validate_session.validate raised {type(e).__name__}: {e}; "
                               "the record is NOT validated (fail closed)") from e


def load_validator(spec) -> tuple[Optional[Any], Optional[str]]:
    """(module, None) for Doodle's validate_session.py, or (None, error) when it is missing, fails to import or
    has no validate(). Callers must treat the error as a failed validation (fail closed, FIX-SEC-001 P0-5)."""
    from .config import display_path
    where = display_path(getattr(spec, "root", None)) if spec is not None else None
    if spec is None or spec.path("session_validator") is None:
        return None, (f"{VALIDATOR_MISSING}: validate_session.py not found in spec dir {where}; "
                      "the record cannot be validated (fail closed)")
    from .spec_pin import pin_error
    perr = pin_error(getattr(spec, "root", None))       # SOL-ACCEPT-001 G8: only the pinned release is trusted
    if perr:
        return None, perr
    try:
        mod = spec.load_module("session_validator")
    except Exception as e:  # noqa: BLE001 - any import failure must fail closed
        return None, (f"{VALIDATOR_BROKEN}: validate_session.py in {where} could not be imported "
                      f"({type(e).__name__}: {e}); the record cannot be validated (fail closed)")
    if mod is None or not callable(getattr(mod, "validate", None)):
        return None, (f"{VALIDATOR_BROKEN}: validate_session.py in {where} has no validate(); "
                      "the record cannot be validated (fail closed)")
    return mod, None


def run_session(settings: Settings, inputs: SessionInputs, as_of: datetime, fixtures_dir: Optional[Path],
                existing: Optional[dict] = None, existing_trace: Optional[SessionTrace] = None
                ) -> tuple[dict, SessionTrace]:
    orch = Orchestrator(build_pipeline(settings, fixtures_dir, inputs, as_of))
    rec, rt, _ = orch.run(inputs, as_of, existing)
    trace = existing_trace or SessionTrace(session_id=rec["session"]["id"])
    trace.runs.append(rt)
    return rec, trace


def validate_file(settings: Settings, path: Path) -> list[str]:
    """Doodle's validator on a written record file, with base_dir so INV-11 checks trace_ref against the trace,
    plus optpipe's guards. Never None and never [] unless validated: a missing/broken/crashing validator or an
    unreadable file is returned as an error (fail closed, FIX-SEC-001 P0-5)."""
    import json
    mod, err = load_validator(discover(settings.spec_path))
    if mod is None:
        return [err]
    p = Path(path)
    try:
        rec = json.loads(p.read_text())
    except (OSError, ValueError) as e:
        return [f"SPEC-RECORD-UNREADABLE: {p.name}: {type(e).__name__}: {e} (fail closed)"]
    return _validate_loaded(mod, rec, p.resolve().parent)


def validate_record(settings: Settings, rec: Any, base_dir: Path) -> list[str]:
    """validate_file on an already-parsed record (the caller parsed the file once and keeps that object, so the
    validated object IS the one it uses; SOL-ACCEPT-001 G7). base_dir = the record file's directory (INV-11)."""
    mod, err = load_validator(discover(settings.spec_path))
    if mod is None:
        return [err]
    return _validate_loaded(mod, rec, Path(base_dir).resolve())


def _validate_loaded(mod, rec: Any, base_dir: Path) -> list[str]:
    from .records.guards import check_record, merge
    try:
        errs = run_validator(mod, copy.deepcopy(rec), base_dir=base_dir)
    except ValidatorCrashed as e:
        return [str(e)]
    if not isinstance(rec, dict):
        return merge(errs, ["SPEC-RECORD-TYPE: the session record is not a JSON object (fail closed)"])
    try:
        gerrs = check_record(rec)
    except Exception as e:                    # noqa: BLE001 - a guard bug must block, never crash (fail closed)
        gerrs = [f"GUARD-CRASHED check_record raised {type(e).__name__}: {e} (fail closed)"]
    return merge(errs, gerrs)
