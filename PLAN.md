# Personal Finance Investigation System

**Owner:** Jonathan Eckel
**Time budget:** ~30-45 hours over 3 weeks (10-15 hrs/week)
**Status:** Scoping and planning complete. Pre-week-1 work in progress.

---

## Problem Statement

Build an LLM agent that investigates and explains candidate transaction anomalies in personal financial data. A minimal deterministic detection layer produces realistic inputs. The project's focus, demo, and writeup are the agentic investigation layer and the architectural judgment behind it.

## Core Architectural Argument

Alerting and investigation are different problems with different tooling needs.

- **Detection layer (deterministic):** rules and simple classifiers find candidate anomalies. Fast, evaluable, no LLM needed. Deliberately minimal: its job is to produce realistic inputs for the agent, not to be a real anomaly detection system.
- **Investigation layer (agentic):** for each candidate alert, an LLM agent decides what to look at, gathers context via tools, and synthesizes a structured explanation. This is where an agent earns its place: when the problem is "decide what to look up next, conditional on what you find."

The README and demo writeup will explicitly argue this distinction. *Here's where the agent earns its place, and here's where it doesn't.* That argument is the primary credibility artifact for the on-paper LLM experience gap.

---

## Scope

### Alert Types (3)
1. **Possible duplicate charge.** Same or near-same amount, same merchant, within N days.
2. **Unusual amount for known merchant.** Significant deviation from spend distribution at that merchant.
3. **Possible forgotten subscription.** Recurring charge with high temporal regularity.

Explicitly out of scope: "new merchant" (too noisy), "fraud / out-of-pattern category" (subjective, hard to label).

### Agent Tools (3 minimum, 4 if time allows)
1. `query_transaction_history(filters)`: search transactions by merchant, category, date range, amount.
2. `get_recurring_pattern(merchant)`: return any detected recurring-charge pattern for a merchant, or none.
3. `get_user_context()`: return synthesized profile: typical spend per category, top merchants, monthly cadence.
4. *Optional:* `lookup_merchant(raw_string)`: normalize messy merchant strings. Itself a structured-output LLM problem.

Explicitly out of scope: web search, fraud-DB lookup, account-balance check. Every added tool multiplies eval effort.

### Output Schema (per investigation)

```json
{
  "transaction": { ... },
  "alert_type": "duplicate" | "unusual_amount" | "subscription",
  "investigation_trace": [
    { "tool": "...", "input": {...}, "output": {...} }
  ],
  "evidence": [ "...", "..." ],
  "verdict": "benign" | "suspicious" | "needs_review",
  "confidence": 0.0-1.0,
  "explanation": "...",
  "recommended_action": "dismiss" | "review" | "dispute"
}
```

The `investigation_trace` is the demo differentiator. Most LLM demos hide the agent's work. Showing the trace makes the reasoning visible, and is exactly what an interviewer wants to see.

---

## Tech Stack

| Layer | Choice |
|---|---|
| Frontend | Streamlit |
| Hosting | AWS App Runner |
| LLM provider | Anthropic API (direct) |
| Agent framework | LangGraph |
| Vector DB | Chroma (persistent) |
| Data store | DuckDB |
| Secrets | AWS Secrets Manager or App Runner env vars |

## Data

- **Dev dataset:** real credit card export, one card, 6-12 months. Gitignored, never committed, never deployed.
- **Demo dataset:** 3-5 hand-curated and sanitized transactions, at least one per alert type. Committed to repo.
- **Eval coverage:** if real data contains fewer than 3 examples of any alert type, inject clearly-labeled synthetic anomalies to ensure coverage. Real-shaped data plus labeled synthetics gives ground-truth control.
- **Dev/demo separation:** baked into repo structure from day one. No retrofitting.

---

## "Demo-able" Acceptance Criteria

1. Live URL, no auth, works on a phone.
2. 3-5 pre-flagged demo transactions, at least one per alert type, all sanitized.
3. Click a transaction, agent trace renders, structured verdict and explanation display.
4. Eval section visible from the demo: detection precision/recall, agent verdict accuracy on a labeled set, at least one architectural comparison (e.g., agent with all tools vs ablation).
5. README explicit about the architectural argument: where the agent earns its place and where it doesn't.
6. 60-second and 5-minute verbal walkthroughs practiced.

**Stretch (only if real slack remains):** multi-card expansion, fourth tool, polished UI, Loom video, LinkedIn writeup.

---

## Plan

### Pre-Week-1 (this week, 1-2 hrs)
- Download 6-12 months of credit card data from bank.
- Eyeball schema, transaction volume, merchant string quality.
- Count rough natural occurrences per alert type (target: ≥3 each).
- Plan synthetic anomaly injections for any gaps.
- De-risks data unknowns before formal kickoff.

### Week 1: Vertical Slice + Foundations (10-12 hrs)

*Goal: ugly end-to-end working locally, App Runner deploy attempted.*

- Repo, dependencies, basic Streamlit shell. (1-2h)
- Load real CC data into DuckDB, define schema, cleanup, inject synthetics. (3-4h)
- Detection layer v0: one alert type only (duplicate charge, simplest). (1-2h)
- Agent skeleton in LangGraph: one tool (`query_transaction_history`), basic loop, structured output. (2-3h)
- Wire end-to-end: flagged transaction to agent to trace rendered in Streamlit. (1-2h)
- First App Runner deploy attempt. (1-2h)

**Checkpoint:** flagged transaction renders an agent trace, even if ugly. App Runner attempted, ideally working.

### Week 2: Depth + Completeness (10-12 hrs)

*Goal: full system, all 3 alert types, all tools, eval baselines.*

- Detection layer v1: add remaining 2 alert types (unusual amount, forgotten subscription). (3h)
- Add remaining agent tools: `get_recurring_pattern`, `get_user_context`. Decide on `lookup_merchant` based on time. (2h)
- Agent prompt tuning: structured output schema, verdict logic, explanation quality. (2h)
- Hand-label eval data: ~30 transactions for detection truth, ~15-20 investigations for verdict accuracy. (2h)
- Eval harness v0: run agent over labeled set, score verdicts, log traces. (1-2h)

**Checkpoint:** all 3 alert types working through agent. Eval baselines exist. Re-deploy.

### Week 3: Eval Rigor + Demo + Writeup (10-15 hrs)

*Goal: shipped, public, polished, defensible.*

- Eval comparison experiment (e.g., agent with all tools vs ablation). This is the differentiator. (3h)
- Curate and sanitize demo dataset (3-5 transactions, one per alert type minimum). (2h)
- Demo polish: trace rendering UX, eval page, landing copy. (3h)
- Final App Runner deploy: domain, SSL, env vars/secrets, log streaming. (2-3h)
- README and LinkedIn-ready writeup, leading with the architectural argument. (2h)
- Practice 60-second and 5-minute walkthroughs out loud. (1h)

**Checkpoint:** Live URL, public, demo data loaded, eval rendered, README written, narrative rehearsed. Project shipped.

---

## Risks (Ranked)

1. **LangGraph learning curve.** First time using it. Could eat 4-5 hrs if a rabbit hole opens. Mitigation: start with the minimal docs example, modify in place. Don't design the perfect agent before writing any agent code.
2. **App Runner first-time deploy.** Could eat 3-4 hrs in week 1 with unfamiliar AWS quirks. Better to surface now than in week 3.
3. **Real data coverage gaps.** May lack examples of some alert types. Mitigation: inject labeled synthetics.
4. **Agent eval methodology.** No widely-agreed best practice. Will be partly invented. *Feature for the writeup*: being able to talk about agent eval as a hard open problem is itself credibility.
5. **Demo data sanitization.** Always longer than expected. Budget aggressively or accept imperfection.

**Slack budget:** the upper end of weekly hours (15) is the buffer. Consistently hitting it in weeks 1-2 means trouble. Hitting it in week 3 is expected.

---

## Operating Rules

- End-of-week check-in: what landed, what slipped, what changed.
- Scope creep gets pushed back hard. "Can I add a 4th alert type?" is almost certainly no.
- Dev data stays gitignored from day one. No retrofitting privacy.
- The plan is the contract until end-of-week-1 or a real wall.
- Update this file when reality diverges from the plan. Git history becomes the story of how the project evolved.
