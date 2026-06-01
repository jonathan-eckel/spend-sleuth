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
2. Check for recurring patterns if relevant.
3. Use get_user_context() to understand typical spending behavior.

After gathering evidence, respond with ONLY a JSON object (no markdown, no extra text) matching
this exact schema:
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
        "Stub: found 3 prior charges at this merchant in the last 90 days",
        "Stub: amounts are consistent with typical spend at this merchant",
    ],
    "verdict": "needs_review",
    "confidence": 0.6,
    "explanation": "Stub investigation: two charges found close in time. Manual review recommended.",
    "recommended_action": "review",
})


class AgentState(TypedDict):
    messages: Annotated[list[BaseMessage], add_messages]
    candidate: dict
    investigation_trace: list[dict]


def build_graph(tools: list, use_stub: bool = False):
    if use_stub:
        llm_with_tools = _build_stub_llm(tools)
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


def _build_stub_llm(tools: list):
    """Return a fake LLM that exercises two tool calls then returns a final JSON verdict."""
    from langchain_core.messages import AIMessage as _AI

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
                        "args": {"merchant": "STUB_MERCHANT", "days_back": 90},
                        "type": "tool_call",
                    }],
                )
            elif call_count[0] == 2:
                return _AI(
                    content="",
                    tool_calls=[{
                        "id": "stub_tc_2",
                        "name": "get_user_context",
                        "args": {},
                        "type": "tool_call",
                    }],
                )
            else:
                return _AI(content=_STUB_FINAL)

    return _StubLLM()
