# Verdict Rubric

## Purpose

This document pins down what each `verdict` and `recommended_action` means. It has two consumers:

1. **The labeler.** When assigning gold verdicts to the eval set, this is the reference that keeps labels consistent across sessions.
2. **The agent.** The same definitions go into the agent's system prompt, so it is producing against the same target you are labeling against.

---

## Verdict classes

Three classes. Label the verdict the **available evidence** warrants, not the omniscient truth (see Labeling Principles).

### `benign`
Evidence supports a legitimate, expected charge. Nothing for the user to act on.
- **Evidence pattern:** the charge fits the user's history, the merchant's normal behavior, or a recognizable benign explanation.
- **Example:** two $4.50 charges at the same coffee shop on the same date. Same-amount, same-merchant, but the context (a cheap, fixed-price, repeatable purchase) makes a true duplicate unlikely.

### `suspicious`
Evidence points to a real problem: a genuine double-charge, a likely billing error, or a charge that shouldn't exist.
- **Evidence pattern:** the signature is hard to explain benignly. Identical-to-the-cent charges at the same merchant on the same date in a variable-price category, an amount wildly outside the merchant's range, etc.
- **Example:** two $128.00 charges at the same merchant on the same date, identical to the cent, at a restaurant (a variable-price category where an exact repeat is implausible). Double-charge signature.

### `needs_review`
Evidence is inconclusive, OR the charge is legitimate but the user should still decide what to do with it.
- **Evidence pattern:** signals are mixed, or the alert is the kind where the product's job is to surface-for-decision rather than to judge (forgotten subscriptions especially).
- **Example:** a $14.99 charge recurring monthly for eight months with high regularity. Real, not fraud, but the user may have forgotten it.

---

## Recommended actions and coupling

**Current design: gold labels `verdict` only. The action is a fixed 1:1 map.**

| verdict | recommended_action |
|---|---|
| `benign` | `dismiss` |
| `needs_review` | `review` |
| `suspicious` | `review` |

At the current data scale this 1:1 coupling is deliberate. A confidence-gated action (dispute vs. review for suspicious cases) only earns its place once the suspicious subset is large enough to tune a threshold against — splitting three or four suspicious cases into dispute/review and "tuning" a cutoff on them is fitting a line to a handful of points. Until then, `recommended_action` is a deterministic function of `verdict`, the headline metric is **3-class verdict accuracy against gold**, and gold carries no action or confidence label.

The agent should still **emit** a `confidence` number — it costs nothing, it is useful signal, and it is the quantity you will tune against later. Gold just does not depend on it yet. You can also check that the agent's own stated action matches what the map prescribes for its stated verdict, which catches rule-application bugs with no extra gold labels.

### Upgrade path: confidence-gated action (when the suspicious subset is large enough)

Once you have roughly ten or more suspicious cases spanning the dispute/review boundary, graduate to gating the action on confidence:

| verdict | confidence | recommended_action |
|---|---|---|
| `benign` | any | `dismiss` |
| `needs_review` | any | `review` |
| `suspicious` | high (>= 0.7) | `dispute` |
| `suspicious` | low (< 0.7) | `review` |

At that point `verdict` answers "what does the evidence say?" and `recommended_action` answers "what should the user do about it?" — the second depends on confidence and on the cost of acting, since disputing takes effort and can be wrong. Gold then carries a `recommended_action` (dispute vs. review) **for suspicious cases only**, representing the actionability judgment you would actually want; benign/needs_review actions stay derived. You do not approximate a numeric confidence in gold — you label the desired action directly, then tune the threshold (the 0.7 above is a placeholder) to the agent confidence level that reproduces those gold actions. This does not violate label-blind: the gold actions are set before the agent runs; tuning only reads the agent's numbers afterward.

---

## Per-alert-type decision rules

The boundary cases are where labeling actually gets hard. These rules resolve them so you label consistently.

### Duplicate charge
Disambiguating signals: same-date vs. different-date, merchant category, amount exactness. Note: data granularity is daily, so intra-day ordering and sub-day gaps are not observable. The finest timing signal is whether the two charges share a date.

- **`suspicious`:** identical-to-the-cent amount, same merchant, same date, in a variable-price category where an exact repeat is implausible (restaurant, retail, electronics). Strongest duplicate signature available at daily granularity.
- **`benign`:** same amount and merchant, but either a fixed-price/repeatable category where same-date repeats are normal (coffee, transit, fast food) OR the charges fall on different dates, suggesting two genuine purchases.
- **`needs_review`:** signals conflict (e.g., identical same-date amount, but at a merchant where an exact repeat is possible yet odd). Evidence does not disambiguate.

### Unusual amount for known merchant
Disambiguating signals: magnitude of deviation, plausibility of a legitimate cause.

- **`suspicious`:** amount is far outside the merchant's established distribution with no plausible legitimate cause (a $4 coffee merchant charging $400).
- **`benign`:** large deviation with a recognizable explanation (annual vs. monthly billing, a known bulk or one-off large order, an included tip, a documented price change).
- **`needs_review`:** the deviation is real and unexplained but not extreme enough to call an error. The agent cannot find a benign cause but the signature isn't alarming.

### Forgotten subscription
The hard one, because the charge is real and not fraud. The question is whether to surface it.

**Convention: default to `needs_review`, not `benign`.**

The product's job for this alert type is surface-for-decision. The agent usually cannot tell a wanted subscription from a forgotten one, so the **absence** of evidence that it is wanted should resolve to `needs_review`, not `benign`. Otherwise the agent silently dismisses exactly the charges the user wanted surfaced.

- **`needs_review`:** a recurring charge with high regularity and no strong evidence it is actively wanted. This is the default for the alert type.
- **`benign`:** evidence indicates the subscription is active and wanted (recent related activity, a major recognized service the user clearly uses). Should be rare, since the agent rarely has this signal.
- **`suspicious`:** the recurring charge has a problem signature: a free-trial-to-paid conversion, a price that crept up over time, or a charge that should not be recurring at all.

---

## Labeling principles

1. **Label to the evidence, not to omniscience.** The agent only sees the transaction data. If the evidence genuinely supports `needs_review`, that is the correct gold label even if you happen to know from outside the dataset that the charge was harmless. Do not penalize the agent for what it cannot see.
2. **Label blind.** Assign gold verdicts before running the agent. Never adjust a label to match the agent's output. That turns the eval circular.
3. **Synthetics are the backbone.** For injected synthetic anomalies you control the evidence, so engineer it to be unambiguous and the gold label is essentially free. Real cases get judgment labels using the rules above.
4. **Balance the set.** Aim for coverage across all three alert types and all three verdict classes. A set that is 80 percent `needs_review` tells you little.

---

## Worked example (gold-labeled case)

The canonical case schema lives in `src/spend_sleuth/evals/schema.py` (Pydantic models, validated on load); the row below is illustrative. The evidence a row carries depends on `alert_type` — a `duplicate` carries both legs of the pair, an `unusual_amount` carries the transaction plus a merchant baseline, a `forgotten_subscription` carries the recurring series. Fields under `gold` are what you label; the agent produces its own and the harness compares.

```json
{
  "case_id": "dup_001",
  "source": "synthetic",
  "alert_type": "duplicate",
  "transactions": [
    {
      "merchant": "BLUEBOTTLE #4417",
      "amount": 128.00,
      "date": "2025-09-14",
      "category": "Food & Drink"
    },
    {
      "merchant": "BLUEBOTTLE #4417",
      "amount": 128.00,
      "date": "2025-09-14",
      "category": "Food & Drink"
    }
  ],
  "gold": {
    "verdict": "suspicious",
    "rationale": "Second identical $128.00 charge at the same merchant on the same date, variable-price category. Double-charge signature, no benign explanation."
  }
}
```

---

## Decisions on record

- Verdict/action coupling: **1:1 map for now** (gold labels verdict only; action derived). Confidence-gated coupling is the documented upgrade path, deferred until the suspicious subset is large enough to tune a threshold.
- Gold carries `verdict` only — no action, no confidence label. Headline metric is 3-class verdict accuracy. The agent still emits a confidence number for later tuning.
- Dispute confidence threshold (0.7): a placeholder, tuned from the eval set when the confidence-gated upgrade is adopted, not fixed in advance.
- Forgotten-subscription no-signal default is `needs_review`, not `benign`.
- Duplicate-alert rows carry both legs of the pair under `transactions` (a list), since a duplicate verdict requires seeing both charges.
