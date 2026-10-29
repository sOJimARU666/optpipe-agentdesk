"""Priority 2: the Risk Officer is a pure function that passes all of Doodle's YAML cases and agrees
with spec/risk_reference.py (parity), including on the reviews in session.example.json."""
import copy
import json

import pytest

from optpipe.risk import evaluate, load_ruleset
from optpipe.risk.adapter import review_input_from_session
from optpipe.spec import discover
from optpipe.config import load_settings

_SPEC = discover(load_settings().spec_path)
_RAW = _SPEC.risk_rules() if _SPEC.path("risk_rules") else None
CASES = (_RAW or {}).get("tests", [])
pytestmark = pytest.mark.skipif(_RAW is None, reason="Doodle's spec not found")


@pytest.fixture(scope="module")
def ruleset(spec):
    return load_ruleset(spec)[0]


def _norm(o):
    return json.loads(json.dumps(o, default=str))


def _mine(inp, ruleset, cfg=None):
    out = evaluate(inp, ruleset, cfg).model_dump(mode="json")
    for r in out["results"]:
        for k in ("per_leg", "placeholder"):
            if r[k] is None:
                del r[k]
    return out


def test_case_count_is_55():
    assert len(CASES) == 55


@pytest.mark.parametrize("case", CASES, ids=[c["id"] for c in CASES])
def test_yaml_case(case, ref, raw_rules, ruleset):
    out = _mine(ref.apply_case(raw_rules["fixture"], case), ruleset, case.get("config"))
    assert out["overall"]["approval_required"] is True
    if case["rule"] == "OVERALL":
        assert out["overall"]["verdict"] == case["expect"]["result"]
        return
    r = next(x for x in out["results"] if x["rule_id"] == case["rule"])
    assert r["result"] == case["expect"]["result"], r
    for k, v in (case["expect"].get("measured") or {}).items():
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            assert r["measured"][k] == pytest.approx(v, abs=0.01)
        else:
            assert r["measured"][k] == v


@pytest.mark.parametrize("case", CASES, ids=[c["id"] for c in CASES])
def test_parity_with_reference(case, ref, raw_rules, ruleset):
    inp = ref.apply_case(raw_rules["fixture"], case)
    expected = ref.evaluate(copy.deepcopy(inp), raw_rules, case.get("config"))
    assert _norm(expected) == _norm(_mine(inp, ruleset, case.get("config")))


def test_clean_fixture_passes_everything(raw_rules, ruleset):
    out = _mine(raw_rules["fixture"], ruleset)
    assert {r["result"] for r in out["results"]} == {"PASS"}
    assert out["overall"]["verdict"] == "PASS" and out["overall"]["approval_required"] is True


def test_pure_function(raw_rules, ruleset):
    inp = copy.deepcopy(raw_rules["fixture"])
    snapshot = copy.deepcopy(inp)
    assert _mine(inp, ruleset) == _mine(inp, ruleset)
    assert inp == snapshot, "evaluate must not mutate its input"


def test_hard_unknown_vetoes(raw_rules, ruleset):
    inp = copy.deepcopy(raw_rules["fixture"])
    inp["legs"][0]["open_interest"] = {"value": None, "source_id": None, "as_of": None, "status": "missing"}
    out = _mine(inp, ruleset)
    r03 = next(r for r in out["results"] if r["rule_id"] == "R03_OPEN_INTEREST")
    assert r03["result"] == "UNKNOWN" and out["overall"]["verdict"] == "VETO"


def test_conflicting_value_is_unknown(raw_rules, ruleset):
    inp = copy.deepcopy(raw_rules["fixture"])
    inp["legs"][0]["open_interest"] = {"value": None, "source_id": None, "as_of": None, "status": "conflicting",
                                       "conflicts": [{"value": 900, "source_id": "a", "as_of": None}, {"value": 400, "source_id": "b", "as_of": None}]}
    r03 = next(r for r in _mine(inp, ruleset)["results"] if r["rule_id"] == "R03_OPEN_INTEREST")
    assert r03["result"] == "UNKNOWN"


def test_config_override_changes_threshold(raw_rules, ruleset):
    out = _mine(raw_rules["fixture"], ruleset, {"per_trade_max_loss_usd": 1})
    r04 = next(r for r in out["results"] if r["rule_id"] == "R04_MAX_LOSS")
    assert r04["result"] == "VETO"


def test_unknown_rule_id_fails_loudly(raw_rules, ruleset):
    bad = ruleset.model_copy(update={"rules": [*ruleset.rules, ruleset.rules[0].model_copy(update={"id": "R99_NEW"})]})
    with pytest.raises(Exception):
        evaluate(raw_rules["fixture"], bad)


def test_example_reviews_reproduced_from_session(example, ruleset):
    """Adapter (session -> rule inputs, point-in-time visibility) + engine == Doodle's stored reviews."""
    for rv in example["risk_reviews"]:
        inp = review_input_from_session(example, rv["ticket_id"], rv["run_id"], rv["evaluated_at"]["ny"])
        out = _mine(inp.model_dump(mode="json"), ruleset)
        assert [(r["rule_id"], r["result"]) for r in out["results"]] == [(r["rule_id"], r["result"]) for r in rv["results"]]
        assert out["overall"]["verdict"] == rv["overall"]["verdict"]


def test_adapter_parity_with_reference_inputs(example, ref, raw_rules, ruleset):
    for rv in example["risk_reviews"]:
        rinp = ref.inputs_from_session(example, rv["ticket_id"], rv["run_id"], rv["evaluated_at"]["ny"])
        mine = review_input_from_session(example, rv["ticket_id"], rv["run_id"], rv["evaluated_at"]["ny"])
        assert _norm(ref.evaluate(rinp, raw_rules)) == _norm(_mine(mine.model_dump(mode="json"), ruleset))


def test_ruleset_is_loaded_in_place_not_copied(spec, ruleset):
    assert ruleset.source == str(spec.path("risk_rules"))
    assert [r.id for r in ruleset.rules] == [r["id"] for r in spec.risk_rules()["rules"]]


def test_no_spec_means_no_risk_officer(tmp_path):
    from optpipe.risk.ruleset import SpecMissing
    with pytest.raises(SpecMissing):
        load_ruleset(discover(tmp_path))
