"""Reject invalid contract counts at the actual typed Risk Officer boundary.

This boundary deliberately accepts native positive integers only: integral
floats, numeric strings and booleans are not alternate contract-count syntax.
The session schema and its generated models are not modified by these checks.
"""
import copy
import sys

import pytest
from pydantic import ValidationError

from optpipe.risk import evaluate, load_ruleset
from optpipe.risk.review_input import ReviewInput


@pytest.fixture(scope="module")
def ruleset(spec):
    return load_ruleset(spec)[0]


@pytest.fixture(autouse=True)
def original_network_guard_is_installed():
    guard = sys.modules.get("optpipe_netguard")
    assert guard is not None and guard.installed()


def _location(field):
    return ("ticket", "quantity") if field == "quantity" else ("legs", 0, "ratio")


def _set_field(data, field, value):
    if field == "quantity":
        data["ticket"]["quantity"] = value
    else:
        data["legs"][0]["ratio"] = value


def _assert_rejected(data, field, ruleset):
    with pytest.raises(ValidationError) as caught:
        evaluate(data, ruleset)
    assert any(error["loc"] == _location(field) for error in caught.value.errors())


@pytest.mark.parametrize("field", ["quantity", "ratio"])
@pytest.mark.parametrize("value", [
    pytest.param(-1, id="negative"),
    pytest.param(0, id="zero"),
    pytest.param(False, id="false"),
    pytest.param(True, id="true"),
    pytest.param(1.5, id="fraction"),
    pytest.param(1.0, id="integral-float"),
    pytest.param("1", id="numeric-string"),
])
def test_evaluate_rejects_invalid_contract_counts(raw_rules, ruleset, field, value):
    data = copy.deepcopy(raw_rules["fixture"])
    _set_field(data, field, value)
    _assert_rejected(data, field, ruleset)


@pytest.mark.parametrize("field", ["quantity", "ratio"])
@pytest.mark.parametrize("method", ["assignment", "model_copy", "model_construct"])
def test_evaluate_revalidates_existing_nested_models(raw_rules, ruleset, field, method):
    typed = ReviewInput.model_validate(copy.deepcopy(raw_rules["fixture"]))
    nested = typed.ticket if field == "quantity" else typed.legs[0]

    if method == "assignment":
        # Validation at construction cannot protect a model later mutated by a caller.
        setattr(nested, field, 0)
        supplied = typed
    else:
        if method == "model_copy":
            invalid = nested.model_copy(update={field: 0})
        else:
            values = dict(nested)
            values[field] = 0
            invalid = type(nested).model_construct(**values)
        update = {"ticket": invalid} if field == "quantity" else {"legs": [invalid, *typed.legs[1:]]}
        if method == "model_copy":
            supplied = typed.model_copy(update=update)
        else:
            supplied = ReviewInput.model_construct(**{**dict(typed), **update})

    _assert_rejected(supplied, field, ruleset)


@pytest.mark.parametrize("quantity", [1, 2])
def test_valid_integer_inputs_keep_same_risk_results(raw_rules, ruleset, quantity):
    data = copy.deepcopy(raw_rules["fixture"])
    data["ticket"]["quantity"] = quantity
    typed = ReviewInput.model_validate(data)
    before = typed.model_dump(mode="python")
    from_dict = evaluate(data, ruleset).model_dump(mode="json")
    from_model = evaluate(typed, ruleset).model_dump(mode="json")
    assert from_dict == from_model
    assert typed.model_dump(mode="python") == before
    assert from_dict["overall"]["approval_required"] is True
    maximum_loss = next(r for r in from_dict["results"] if r["rule_id"] == "R04_MAX_LOSS")
    assert maximum_loss["measured"]["max_loss_usd"] == 425.0 * quantity
    assert maximum_loss["result"] == ("PASS" if quantity == 1 else "VETO")


def test_existing_ratio_default_remains_one(raw_rules, ruleset):
    original = copy.deepcopy(raw_rules["fixture"])
    omitted = copy.deepcopy(original)
    for leg in omitted["legs"]:
        leg.pop("ratio")
    typed = ReviewInput.model_validate(omitted)
    assert [leg.ratio for leg in typed.legs] == [1, 1]
    assert evaluate(typed, ruleset).model_dump(mode="json") == evaluate(original, ruleset).model_dump(mode="json")
