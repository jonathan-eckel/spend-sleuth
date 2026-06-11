"""Case schema for the eval set.

Canonical, machine-enforced shape of an eval row. The verdict *semantics* and
labeling rules live in ``src/spend_sleuth/evals/verdict.md``; this module enforces *structure* so
the loader, the gold rows, and the agent's input cannot silently drift.

The evidence a case carries depends on its ``alert_type``, so ``Case`` is a
discriminated union: a ``duplicate`` must carry both legs of the pair, an
``unusual_amount`` must carry the transaction plus the merchant's baseline, and
a ``forgotten_subscription`` must carry the recurring series. Violations are
validation errors, not undocumented conventions.

Load and validate rows with ``CaseAdapter``::

    import json
    from spend_sleuth.evals.schema import CaseAdapter

    case = CaseAdapter.validate_python(json.loads(row))
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from enum import Enum
from typing import Annotated, Literal, Optional, Union

from pydantic import BaseModel, Field, TypeAdapter


class AlertType(str, Enum):
    DUPLICATE = "duplicate"
    UNUSUAL_AMOUNT = "unusual_amount"
    FORGOTTEN_SUBSCRIPTION = "forgotten_subscription"


class Verdict(str, Enum):
    BENIGN = "benign"
    SUSPICIOUS = "suspicious"
    NEEDS_REVIEW = "needs_review"


class RecommendedAction(str, Enum):
    DISMISS = "dismiss"
    REVIEW = "review"
    DISPUTE = "dispute"


class Source(str, Enum):
    SYNTHETIC = "synthetic"
    REAL = "real"


class Transaction(BaseModel):
    """A single charge, as the agent sees it. Daily granularity — no intra-day
    timing is observable (see verdict.md, Duplicate charge)."""

    model_config = {"extra": "forbid"}

    merchant: str
    amount: Decimal
    date: date
    category: str


class MerchantBaseline(BaseModel):
    """The merchant's established amount distribution, for unusual-amount cases.

    Gives the agent something to judge the flagged amount *against*. Values are
    summary statistics over the merchant's prior charges in the dataset.
    """

    model_config = {"extra": "forbid"}

    mean: Decimal
    stddev: Decimal
    count: int = Field(ge=1)
    min: Decimal
    max: Decimal


class Gold(BaseModel):
    """The labeled answer. Current design is verdict-only (see verdict.md,
    Recommended actions and coupling).

    ``recommended_action`` is reserved for the confidence-gated upgrade path,
    where suspicious cases carry a labeled dispute/review action. It stays
    ``None`` under the current 1:1 design.
    """

    model_config = {"extra": "forbid"}

    verdict: Verdict
    rationale: str
    recommended_action: Optional[RecommendedAction] = None


class _CaseBase(BaseModel):
    model_config = {"extra": "forbid"}

    case_id: str
    source: Source
    gold: Gold


class DuplicateCase(_CaseBase):
    """A suspected double-charge. Carries both legs of the pair."""

    alert_type: Literal[AlertType.DUPLICATE] = AlertType.DUPLICATE
    transactions: list[Transaction] = Field(min_length=2)


class UnusualAmountCase(_CaseBase):
    """A charge that deviates from the merchant's norm. Carries the flagged
    transaction plus the merchant's baseline distribution to judge it against."""

    alert_type: Literal[AlertType.UNUSUAL_AMOUNT] = AlertType.UNUSUAL_AMOUNT
    transaction: Transaction
    baseline: MerchantBaseline


class SubscriptionCase(_CaseBase):
    """A possibly-forgotten recurring charge. Carries the recurring series so
    regularity is observable."""

    alert_type: Literal[AlertType.FORGOTTEN_SUBSCRIPTION] = (
        AlertType.FORGOTTEN_SUBSCRIPTION
    )
    transactions: list[Transaction] = Field(min_length=2)


Case = Annotated[
    Union[DuplicateCase, UnusualAmountCase, SubscriptionCase],
    Field(discriminator="alert_type"),
]
"""An eval row. Discriminated on ``alert_type``; validation enforces the
evidence shape each type requires."""

CaseAdapter: TypeAdapter[Case] = TypeAdapter(Case)
"""Use to load/validate a single case from parsed JSON."""

CaseListAdapter: TypeAdapter[list[Case]] = TypeAdapter(list[Case])
"""Use to load/validate a whole eval set (a JSON array of cases)."""
