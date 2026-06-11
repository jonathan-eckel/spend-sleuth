import pytest
from pydantic import ValidationError

from spend_sleuth.evals import (
    CaseAdapter,
    DuplicateCase,
    SubscriptionCase,
    UnusualAmountCase,
    Verdict,
)


def _txn(amount=128.00, date="2025-09-14"):
    return {
        "merchant": "BLUEBOTTLE #4417",
        "amount": amount,
        "date": date,
        "category": "Food & Drink",
    }


# --- worked example from verdict.md ---

def test_worked_example_validates():
    row = {
        "case_id": "dup_001",
        "source": "synthetic",
        "alert_type": "duplicate",
        "transactions": [_txn(), _txn()],
        "gold": {
            "verdict": "suspicious",
            "rationale": "Double-charge signature, no benign explanation.",
        },
    }
    case = CaseAdapter.validate_python(row)
    assert isinstance(case, DuplicateCase)
    assert case.gold.verdict is Verdict.SUSPICIOUS
    assert case.gold.recommended_action is None  # verdict-only design


# --- discriminated-union evidence requirements ---

def test_duplicate_requires_both_legs():
    row = {
        "case_id": "dup_002",
        "source": "synthetic",
        "alert_type": "duplicate",
        "transactions": [_txn()],
        "gold": {"verdict": "suspicious", "rationale": "x"},
    }
    with pytest.raises(ValidationError):
        CaseAdapter.validate_python(row)


def test_unusual_amount_requires_baseline():
    row = {
        "case_id": "amt_001",
        "source": "synthetic",
        "alert_type": "unusual_amount",
        "transaction": _txn(amount=400.00),
        "gold": {"verdict": "needs_review", "rationale": "x"},
    }
    with pytest.raises(ValidationError):
        CaseAdapter.validate_python(row)


def test_unusual_amount_with_baseline_validates():
    row = {
        "case_id": "amt_002",
        "source": "synthetic",
        "alert_type": "unusual_amount",
        "transaction": _txn(amount=400.00),
        "baseline": {"mean": 4.50, "stddev": 0.75, "count": 30, "min": 3.50, "max": 6.00},
        "gold": {"verdict": "suspicious", "rationale": "$400 at a $4 coffee merchant."},
    }
    case = CaseAdapter.validate_python(row)
    assert isinstance(case, UnusualAmountCase)


def test_subscription_validates():
    row = {
        "case_id": "sub_001",
        "source": "synthetic",
        "alert_type": "forgotten_subscription",
        "transactions": [_txn(amount=14.99, date=f"2025-0{m}-01") for m in range(1, 9)],
        "gold": {"verdict": "needs_review", "rationale": "Recurring, no signal it is wanted."},
    }
    case = CaseAdapter.validate_python(row)
    assert isinstance(case, SubscriptionCase)


# --- field-level guards ---

def test_unknown_alert_type_rejected():
    row = {
        "case_id": "x", "source": "synthetic", "alert_type": "nonsense",
        "gold": {"verdict": "benign", "rationale": "x"},
    }
    with pytest.raises(ValidationError):
        CaseAdapter.validate_python(row)


def test_extra_field_rejected():
    row = {
        "case_id": "dup_003", "source": "synthetic", "alert_type": "duplicate",
        "transactions": [_txn(), _txn()],
        "gold": {"verdict": "suspicious", "rationale": "x"},
        "surprise": True,
    }
    with pytest.raises(ValidationError):
        CaseAdapter.validate_python(row)


def test_upgrade_path_action_accepted_on_gold():
    # recommended_action is reserved for the confidence-gated upgrade; the
    # schema accepts it even though current gold leaves it None.
    row = {
        "case_id": "dup_004", "source": "synthetic", "alert_type": "duplicate",
        "transactions": [_txn(), _txn()],
        "gold": {"verdict": "suspicious", "rationale": "x", "recommended_action": "dispute"},
    }
    case = CaseAdapter.validate_python(row)
    assert case.gold.recommended_action.value == "dispute"
