import json
import pytest
from spend_sleuth.agent.run import _parse_verdict


def _good_payload(**overrides) -> str:
    base = {
        "evidence": ["charge seen 3x in 90 days"],
        "verdict": "benign",
        "confidence": 0.95,
        "explanation": "Routine purchase.",
        "recommended_action": "dismiss",
    }
    base.update(overrides)
    return json.dumps(base)


def test_happy_path():
    v = _parse_verdict(_good_payload())
    assert v.verdict == "benign"
    assert v.confidence == 0.95
    assert v.recommended_action == "dismiss"
    assert v.evidence == ["charge seen 3x in 90 days"]


def test_confidence_string_coerced():
    v = _parse_verdict(_good_payload(confidence="0.87"))
    assert v.confidence == pytest.approx(0.87)


def test_confidence_clamped_high():
    v = _parse_verdict(_good_payload(confidence=1.5))
    assert v.confidence == 1.0


def test_confidence_clamped_low():
    v = _parse_verdict(_good_payload(confidence=-0.3))
    assert v.confidence == 0.0


def test_invalid_verdict_normalised():
    v = _parse_verdict(_good_payload(verdict="false_positive"))
    assert v.verdict == "needs_review"


def test_invalid_action_normalised():
    v = _parse_verdict(_good_payload(recommended_action="ignore"))
    assert v.recommended_action == "review"


def test_evidence_string_wrapped():
    v = _parse_verdict(_good_payload(evidence="single finding"))
    assert v.evidence == ["single finding"]


def test_missing_fields_use_defaults():
    v = _parse_verdict(json.dumps({"verdict": "suspicious"}))
    assert v.confidence == 0.0
    assert v.evidence == []
    assert v.recommended_action == "review"


def test_json_wrapped_in_prose():
    prose = 'Here is my analysis:\n\n' + _good_payload() + '\n\nLet me know if you have questions.'
    v = _parse_verdict(prose)
    assert v.verdict == "benign"


def test_unparseable_response_falls_back():
    v = _parse_verdict("Sorry, I cannot determine the verdict.")
    assert v.verdict == "needs_review"
    assert v.confidence == 0.0
    assert "unparseable" in v.explanation
