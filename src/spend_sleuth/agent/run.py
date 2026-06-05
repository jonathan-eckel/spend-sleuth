import json
import math
import os
import re

import duckdb
from langchain_core.messages import SystemMessage, HumanMessage
from pydantic import BaseModel, field_validator

from .graph import SYSTEM_PROMPT, build_graph
from .tools import make_tools

_VALID_VERDICTS = {"benign", "suspicious", "needs_review"}
_VALID_ACTIONS = {"dismiss", "review", "dispute"}


class _VerdictSchema(BaseModel):
    evidence: list[str] = []
    verdict: str = "needs_review"
    confidence: float = 0.0
    explanation: str = ""
    recommended_action: str = "review"

    @field_validator("confidence", mode="after")
    @classmethod
    def clamp_confidence(cls, v: float) -> float:
        if math.isnan(v) or math.isinf(v):
            return 0.0
        return max(0.0, min(1.0, v))

    @field_validator("verdict", mode="before")
    @classmethod
    def normalize_verdict(cls, v) -> str:
        return v if v in _VALID_VERDICTS else "needs_review"

    @field_validator("recommended_action", mode="before")
    @classmethod
    def normalize_action(cls, v) -> str:
        return v if v in _VALID_ACTIONS else "review"

    @field_validator("evidence", mode="before")
    @classmethod
    def coerce_evidence(cls, v) -> list[str]:
        if isinstance(v, str):
            return [v]
        if isinstance(v, list):
            return [str(e) for e in v]
        return []


def _extract_json_block(text: str) -> str | None:
    """Extract the first {...} block from a text response."""
    match = re.search(r'\{.*\}', text, re.DOTALL)
    return match.group(0) if match else None


def _parse_verdict(content: str) -> _VerdictSchema:
    """Parse and validate the LLM's verdict JSON, with coercion and safe fallbacks."""
    parsed = None
    for candidate_text in [content, _extract_json_block(content)]:
        if candidate_text is None:
            continue
        try:
            parsed = json.loads(candidate_text)
            break
        except (json.JSONDecodeError, TypeError):
            continue

    if parsed is not None:
        return _VerdictSchema.model_validate(parsed)

    return _VerdictSchema(explanation=f"Agent returned unparseable response: {content}")


def investigate(
    candidate: dict,
    conn: duckdb.DuckDBPyConnection,
    use_stub: bool = False,
) -> dict:
    """Run agent investigation on a candidate alert. Returns a structured investigation result."""
    if not use_stub and not os.environ.get("ANTHROPIC_API_KEY"):
        raise EnvironmentError(
            "ANTHROPIC_API_KEY not set. Pass use_stub=True or set the env var."
        )

    tools = make_tools(conn)
    graph = build_graph(tools, use_stub=use_stub, candidate=candidate)

    initial_messages = [
        SystemMessage(content=SYSTEM_PROMPT),
        HumanMessage(content=f"Investigate this candidate alert:\n\n{json.dumps(candidate, default=str, indent=2)}"),
    ]

    final_state = graph.invoke({
        "messages": initial_messages,
        "candidate": candidate,
        "investigation_trace": [],
    })

    last_message = final_state["messages"][-1]
    content = getattr(last_message, "content", "") or ""
    verdict = _parse_verdict(content)

    return {
        "transaction": candidate.get("txn_a") or candidate.get("transaction"),
        "alert_type": candidate["alert_type"],
        "investigation_trace": final_state.get("investigation_trace", []),
        **verdict.model_dump(),
    }
