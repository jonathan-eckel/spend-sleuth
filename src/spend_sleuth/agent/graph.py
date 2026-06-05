import json
import os
from typing import Annotated
from typing_extensions import TypedDict

from langchain_core.messages import BaseMessage, SystemMessage, AIMessage, ToolMessage
from langchain_anthropic import ChatAnthropic
from langgraph.graph import StateGraph, END
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode

SYSTEM_PROMPT = """You are a personal finance investigation agent. You will receive a candidate alert
produced by a deterministic detection system. Your job is to investigate the alert by querying
transaction history and user context, then produce a structured verdict.

Use the available tools to gather evidence before reaching a conclusion. Think step by step:
1. Query the transaction history for the merchant to understand frequency and amounts.
   For subscription alerts the candidate includes mean_interval_days — set days_back to
   at least mean_interval_days × 4 so you can see multiple full billing cycles.
   (Example: monthly subscription → days_back=120, quarterly → days_back=360.)
2. Check for recurring patterns if relevant.
3. Use get_user_context() to understand typical spending behavior.

After gathering evidence, your final message MUST be a raw JSON object and nothing else.
No preamble, no explanation, no markdown fences — just the JSON object starting with { and ending with }.

Required schema:
{
  "evidence": ["<finding 1>", "<finding 2>", ...],
  "verdict": "benign" | "suspicious" | "needs_review",
  "confidence": <float 0.0-1.0>,
  "explanation": "<concise explanation of your verdict>",
  "recommended_action": "dismiss" | "review" | "dispute"
}"""

_STUB_TOOL_CALL_1 = """{
  "id": "stub_tc_1",
  "name": "query_transaction_history",
  "args": {"merchant": "STUB_MERCHANT", "days_back": 90}
}"""

_STUB_TOOL_CALL_2 = """{
  "id": "stub_tc_2",
  "name": "get_user_context",
  "args": {}
}"""

_STUB_FINAL = json.dumps({
    "evidence": [
        "Stub: found 4 prior charges at this merchant in the last 90 days ranging from \$18.00 to \$22.50, establishing a clear visit history.",
        "Stub: the two flagged transactions are 6 days apart (\$19.99 on the 3rd, \$20.50 on the 9th), consistent with repeat visits rather than a duplicate posting.",
        "Stub: amount difference of \$0.51 is within normal price variation — true duplicates typically match exactly.",
        "Stub: no consistent automated billing pattern detected; charges appear organic and in-person.",
        "Stub: this category accounts for frequent low-value transactions in the user's history — repeat same-week visits are expected.",
    ],
    "verdict": "benign",
    "confidence": 0.92,
    "explanation": (
        "**Stub:** This alert is a false positive. The two flagged transactions reflect separate visits to the same merchant "
        "on different dates, not a duplicate charge. The \$0.51 amount difference is inconsistent with a system-generated "
        "duplicate, which would post the exact same amount. The merchant's transaction history shows a recurring pattern "
        "of similar-sized charges going back at least 90 days, strongly indicating a habitual purchase (e.g., a regular "
        "lunch spot). No action is needed."
    ),
    "recommended_action": "dismiss",
})


class AgentState(TypedDict):
    messages: Annotated[list[BaseMessage], add_messages]
    candidate: dict
    investigation_trace: list[dict]


def build_graph(tools: list, use_stub: bool = False, candidate: dict | None = None):
    if use_stub:
        llm_with_tools = _build_stub_llm(tools, candidate)
    else:
        llm_with_tools = ChatAnthropic(
            model="claude-sonnet-4-6",
            api_key=os.environ["ANTHROPIC_API_KEY"],
        ).bind_tools(tools)

    tool_node = ToolNode(tools)

    def call_model(state: AgentState) -> dict:
        response = llm_with_tools.invoke(state["messages"])
        return {"messages": [response]}

    def call_tools(state: AgentState) -> dict:
        last_message = state["messages"][-1]
        tool_results = tool_node.invoke({"messages": state["messages"]})
        trace = list(state.get("investigation_trace", []))
        for tool_call in last_message.tool_calls:
            matching = next(
                (m for m in tool_results["messages"] if isinstance(m, ToolMessage) and m.tool_call_id == tool_call["id"]),
                None,
            )
            trace.append({
                "tool": tool_call["name"],
                "input": tool_call["args"],
                "output": matching.content if matching else None,
            })
        return {"messages": tool_results["messages"], "investigation_trace": trace}

    def should_continue(state: AgentState) -> str:
        last = state["messages"][-1]
        if isinstance(last, AIMessage) and last.tool_calls:
            return "call_tools"
        return END

    graph = StateGraph(AgentState)
    graph.add_node("call_model", call_model)
    graph.add_node("call_tools", call_tools)
    graph.set_entry_point("call_model")
    graph.add_conditional_edges("call_model", should_continue, {"call_tools": "call_tools", END: END})
    graph.add_edge("call_tools", "call_model")

    return graph.compile()


def _build_stub_llm(tools: list, candidate: dict | None = None):
    """Return a fake LLM that exercises all three tool calls then returns a final JSON verdict."""
    from langchain_core.messages import AIMessage as _AI

    merchant = (candidate or {}).get("normalized_merchant", "STUB_MERCHANT")
    mean_interval = (candidate or {}).get("mean_interval_days") or 0
    days_back = max(int(mean_interval * 4), 90)
    tool_defs = {t.name: t for t in tools}
    call_count = [0]

    class _StubLLM:
        def invoke(self, messages):
            call_count[0] += 1
            if call_count[0] == 1:
                return _AI(
                    content="",
                    tool_calls=[{
                        "id": "stub_tc_1",
                        "name": "query_transaction_history",
                        "args": {"merchant": merchant, "days_back": days_back},
                        "type": "tool_call",
                    }],
                )
            elif call_count[0] == 2:
                return _AI(
                    content="",
                    tool_calls=[{
                        "id": "stub_tc_2",
                        "name": "get_recurring_pattern",
                        "args": {"merchant": merchant},
                        "type": "tool_call",
                    }],
                )
            elif call_count[0] == 3:
                return _AI(
                    content="",
                    tool_calls=[{
                        "id": "stub_tc_3",
                        "name": "get_user_context",
                        "args": {},
                        "type": "tool_call",
                    }],
                )
            else:
                return _AI(content=_STUB_FINAL)

    return _StubLLM()
