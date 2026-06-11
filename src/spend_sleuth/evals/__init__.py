"""Eval harness for Spend Sleuth.

Semantics (what each verdict means, how to label) live in ``src/verdict.md``.
This package owns the machine contract: the case schema and, later, the runner.
"""

from spend_sleuth.evals.schema import (
    AlertType,
    Case,
    CaseAdapter,
    DuplicateCase,
    Gold,
    MerchantBaseline,
    RecommendedAction,
    SubscriptionCase,
    Transaction,
    UnusualAmountCase,
    Verdict,
)

__all__ = [
    "AlertType",
    "Case",
    "CaseAdapter",
    "DuplicateCase",
    "Gold",
    "MerchantBaseline",
    "RecommendedAction",
    "SubscriptionCase",
    "Transaction",
    "UnusualAmountCase",
    "Verdict",
]
