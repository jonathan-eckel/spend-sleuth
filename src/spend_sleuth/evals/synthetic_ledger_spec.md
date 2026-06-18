# Synthetic ledger eval spec

A curated personal-card ledger (built via the Demo Data Builder → `demo_data/builder.db`) plus a ground-truth **manifest**, used to measure detection recall/precision and 3-class verdict accuracy. Stays consistent with `detect.py` thresholds and [`schema.py`](schema.py) + [`verdict.md`](verdict.md).

## What the dataset represents

- **Realistic:** 200–500 transactions over 3–6 months — normal spend plus ≥ 3 recurring merchants (substrate for subscriptions and for unusual-amount baselines).
- **Interesting:** ≥ 3 planted anomalies per type (`duplicate`, `unusual_amount`, `forgotten_subscription`), each labeled `clear` or `borderline`.
- **Close calls:** ~10–12 near-miss distractors (≥ 2 per type) that tempt a detector but should **not** fire.

## Build targets

| Parameter | Target |
|---|---|
| Total transactions | 200–500 |
| Time span | 3–6 months |
| Planted per type | ≥ 3 (`clear`/`borderline`; aim ≥ 1 of each) |
| Anomaly-defining rows | ≤ 5% (duplicate 2nd legs + unusual spikes; subscription series excluded) |
| Near-miss distractors | ~10–12 (≥ 2 per type) |
| Recurring merchants | ≥ 3 |
| Messy merchant strings | ≥ 30% *(quality signal)* |

## Thresholds (mirror `detect.py`)

The validator imports the detectors so these can't drift:

- **duplicate:** same canonical merchant, amounts within $0.01, dates within 7d; not OMNY, not a suppressed subscription.
- **unusual_amount:** z ≥ 2.0 **and** Δ ≥ $10, with ≥ 5 priors in 365d (build target ≥ 8).
- **forgotten_subscription:** ≥ 3 occurrences, cv < 0.30, span ≥ 2× mean interval, within 400d.

## Manifest (ground truth)

JSONL joined to detection output on **`row_hash`**.

**Planted** — `{row_hash, alert_type, difficulty: clear|borderline, gold_verdict, gold_rationale, note}`. `alert_type` and `gold_verdict` use the `schema.py` enums (`forgotten_subscription`, etc.); gold is **verdict-only** (the action is the fixed 1:1 map in `verdict.md`). For a duplicate, `row_hash` is the flagged second leg.

**Near-miss** — `{row_hash, tempts_alert, should_fire: false, mechanism, note}`. `mechanism` names the gate it just misses (e.g. `dup_outside_window`, `unusual_below_delta`, `sub_irregular_cv`).

Borderline planted sit just *over* threshold (should fire — a close call); near-misses sit just *under* (should not). Fired planted alerts become `schema.Case` rows in `eval_investigations.jsonl`; all planted + near-miss populate `eval_detection.jsonl` (`should_fire` derived).

## Validation checks

Run detection with subscription suppression + OMNY filtering (as `cli.py` does), then assert:

1. **[GATE] Clear-planted recall = 100%** — every `clear` planted fires its `alert_type`. Borderline recall is reported separately (signal).
2. Per-type coverage: each type has ≥ 3 planted that fire.
3. Distractors present: each type has ≥ 2 near-misses.
4. **[GATE] Base rate ≤ 5%** (numerator = anomaly-defining rows, as above).
5. **[GATE] Discoverability per planted anomaly** — the context exists for it to fire: a duplicate sibling within tolerance/window; ≥ 5 priors clearing both unusual gates; a ≥ 3-occurrence series with cv < 0.30 and span ≥ 2× interval.
6. Merchant realism: ≥ 30% of distinct merchant strings carry a digit/special token.
7. Amount skew: right-skewed (mean/median ≥ 1.3 or top-decile share ≥ 0.35).
8. Unplanned alerts: fired alerts whose `row_hash` ∉ manifest (after suppression + OMNY filtering) — each is a catch to add or a false positive to fix.

**Hard gates:** 1, 4, 5. **Quality signals:** 2, 3, 6, 7, 8, and borderline recall.

## Caution

Don't tune to a perfect score. A few near-miss false positives and borderline misses are expected — and more credible than perfection.

## Note (builder caveat)

The Demo Data Builder anonymizes merchants to clean brand names and scrubs OMNY/store-number strings, so check 6 and transit-style distractors are **signals, not gates** until the builder optionally preserves those tokens (and surfaces `row_hash` for manifest authoring).
