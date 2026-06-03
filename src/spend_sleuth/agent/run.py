import json
import os

import duckdb
from langchain_core.messages import SystemMessage, HumanMessage

from .graph import SYSTEM_PROMPT, build_graph
from .tools import make_tools


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
    graph = build_graph(tools, use_stub=use_stub)

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
    try:
        verdict = json.loads(last_message.content)
    except (json.JSONDecodeError, AttributeError):
        verdict = {
            "evidence": [],
            "verdict": "needs_review",
            "confidence": 0.0,
            "explanation": f"Agent returned unparseable response: {getattr(last_message, 'content', str(last_message))}",
            "recommended_action": "review",
        }

    return {
        "transaction": candidate.get("txn_a") or candidate.get("transaction"),
        "alert_type": candidate["alert_type"],
        "investigation_trace": final_state.get("investigation_trace", []),
        **verdict,
    }
