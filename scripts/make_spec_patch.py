"""Build the proposed v1.1 schema + fixed example as a COPY under spec_patches/v1.1/.

Doodle's spec folder is only read. Re-running is deterministic; `--check` exits 1 if the committed
copy differs from what this script would produce (used in CI). Every change is listed in
spec_patches/v1.1/CHANGES.md and as a unified diff in spec_patches/v1.1/*.diff.
"""
from __future__ import annotations

import argparse
import copy
import difflib
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "spec_patches" / "v1.1"
sys.path.insert(0, str(ROOT / "src"))


def dumps(o) -> str:
    return json.dumps(o, indent=2, ensure_ascii=False) + "\n"


def patch_schema(s: dict) -> dict:
    s = copy.deepcopy(s)
    p, d = s["properties"], s["$defs"]
    # 1. schema_version: accept 1.0.0 records unchanged, 1.1.0 for records using the new fields
    p["schema_version"] = {"enum": ["1.0.0", "1.1.0"]}
    # 2. open interest is an integer count (contracts and the IntegerField def)
    d["IntegerField"] = {"$ref": "#/$defs/Field", "properties": {
        "value": {"type": ["integer", "null"]},
        "conflicts": {"items": {"properties": {"value": {"type": "integer"}}}}}}
    d["OptionContract"]["properties"]["open_interest"] = {"$ref": "#/$defs/IntegerField",
                                                         "description": "OCC open interest (contracts, integer)"}
    # 3. run phase 'closed' (before 04:00 / after the close NY, weekends, NYSE holidays)
    ph = p["runs"]["items"]["properties"]["phase"]
    ph["enum"] = ph["enum"] + ["closed"]
    ph["description"] = ("NY clock. closed = before 04:00 or after the regular close (16:00, 13:00 on early-close "
                         "days) on a trading day, or a weekend / NYSE holiday")
    # 4. per-run premarket scan / global snapshot, keyed by run_id like candidate snapshots
    p["premarket_scans"] = {"description": "One premarket scan per run (run_id unique). premarket_scan, if present, "
                                           "is the latest of these (kept for v1 readers).",
                            "type": "array", "items": {"$ref": "#/properties/premarket_scan"}}
    p["global_snapshots"] = {"description": "One global snapshot per run (run_id unique). global_snapshot, if "
                                            "present, is the latest of these (kept for v1 readers).",
                             "type": "array", "items": {"$ref": "#/properties/global_snapshot"}}
    # 5. audit link to the separate hand-off trace file
    p["trace_ref"] = {"type": "object", "additionalProperties": False, "required": ["path", "sha256"],
                      "description": "Hand-off trace written next to this record (not embedded).",
                      "properties": {"path": {"type": "string", "pattern": "^[^/].*\\.trace\\.json$",
                                              "description": "relative to this record's directory"},
                                     "sha256": {"type": "string", "pattern": "^[0-9a-f]{64}$"}}}
    # 6. tick-size rounding: keep raw and rounded prices
    lp = d["LimitPrice"]["properties"]
    lp["raw_amount"] = {"type": "number", "minimum": 0, "description": "unrounded price before tick rounding"}
    lp["tick_size"] = {"type": "number", "exclusiveMinimum": 0}
    lp["rounding"] = {"enum": ["down", "up", "nearest", "none"],
                      "description": "direction applied to raw_amount (debit limit: down)"}
    cp = d["Condition"]["properties"]
    cp["raw_value"] = {"type": "number", "description": "unrounded threshold before tick rounding"}
    cp["tick_size"] = {"type": "number", "exclusiveMinimum": 0}
    cp["rounding"] = {"enum": ["down", "up", "nearest", "none"]}
    s["description"] = (s.get("description", "") + " | v1.1 PROPOSAL (Poodle, spec_patches/v1.1/CHANGES.md)").strip()
    return s


def patch_example(ex: dict) -> dict:
    ex = copy.deepcopy(ex)

    def walk(o):
        if isinstance(o, dict):
            oi = o.get("open_interest")
            if isinstance(oi, dict) and isinstance(oi.get("value"), float) and oi["value"].is_integer():
                oi["value"] = int(oi["value"])
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)
    walk(ex)
    for rv in ex["risk_reviews"]:
        for r in rv["results"]:
            if r["rule_id"] == "R03_OPEN_INTEREST":
                if isinstance(r.get("measured"), dict):
                    r["measured"] = {k: int(v) if isinstance(v, float) and v.is_integer() else v
                                     for k, v in r["measured"].items()}
                for pl in r.get("per_leg") or []:
                    if isinstance(pl["measured"], float) and pl["measured"].is_integer():
                        pl["measured"] = int(pl["measured"])
                r["reason"] = re.sub(r"OI (\d+)\.0\b", r"OI \1", r["reason"])
    return ex


def build(spec_dir: Path) -> dict[str, str]:
    s_txt = (spec_dir / "session.schema.json").read_text()
    e_txt = (spec_dir / "session.example.json").read_text()
    s_new, e_new = dumps(patch_schema(json.loads(s_txt))), dumps(patch_example(json.loads(e_txt)))
    diff = lambda a, b, n: "".join(difflib.unified_diff(  # noqa: E731
        dumps(json.loads(a)).splitlines(True), b.splitlines(True), f"a/{n}", f"b/{n}", n=2))
    return {"session.schema.json": s_new, "session.example.json": e_new,
            "session.schema.json.diff": diff(s_txt, s_new, "session.schema.json"),
            "session.example.json.diff": diff(e_txt, e_new, "session.example.json")}


def main(argv=None) -> int:
    from optpipe.config import load_settings
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    a = ap.parse_args(argv)
    files = build(load_settings().spec_path)
    if a.check:
        bad = [n for n, t in files.items() if not (OUT / n).exists() or (OUT / n).read_text() != t]
        print("spec_patches up to date" if not bad else f"spec_patches stale: {bad}")
        return 1 if bad else 0
    OUT.mkdir(parents=True, exist_ok=True)
    for n, t in files.items():
        (OUT / n).write_text(t)
        print("wrote", OUT / n)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
