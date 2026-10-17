"""Per-run actual compiled async LangGraph stages and isolated role inputs."""

from copy import deepcopy
from typing import TypedDict

from langgraph.graph import END, START, StateGraph
from langsmith import tracing_context

from .domain import document, next_stage


class GraphState(TypedDict, total=False):
    next: str | None
    packet: dict | None


def public_input(owner, role, value):
    parsed = document(value["document"])
    public = {
        "request": owner.intent["request"],
        "source": {
            "title": parsed["title"],
            "sha256": parsed["sha256"],
            "text": parsed["text"],
            "clauses": deepcopy(parsed["clauses"]),
            "body_paragraph_units": deepcopy(parsed["units"]),
        },
        "human_review_required": True,
        "legal_approval": None,
    }
    if role in ("risk_anthropic", "risk_openai"):
        # Each reviewer receives the same extraction, with no peer review.
        public["extraction_proposal"] = deepcopy(
            value["review"]["observations"][0]["payload"]
        )
    elif role == "summary":
        public["independent_reviews"] = [
            deepcopy(x["payload"]) for x in value["review"]["observations"][1:3]
        ]
        public["reconciliation"] = deepcopy(value["review"]["reconciliation"])
    return public


async def run_graph(owner):
    builder = StateGraph(GraphState)
    stages = (
        "extraction",
        "risk_anthropic",
        "risk_openai",
        "reconcile",
        "summary",
        "publish",
    )
    for stage in stages:

        async def node(state, selected=stage):
            read = owner.read(selected)
            payload = provenance = None
            if selected not in ("reconcile", "publish"):
                payload, provenance = await owner.native.call(
                    selected, public_input(owner, selected, read["value"])
                )
            view = owner.apply(read, payload, provenance)
            return {"next": next_stage(view), "packet": deepcopy(view["packet"])}

        builder.add_node(stage, node)
    builder.add_edge(START, "extraction")
    for stage in stages:
        builder.add_conditional_edges(
            stage,
            lambda state: state["next"] or END,
            {**{s: s for s in stages}, END: END},
        )
    graph = builder.compile()
    with tracing_context(enabled=False, parent=False):
        return await graph.ainvoke(
            {"next": "extraction", "packet": None},
            config={"callbacks": [], "recursion_limit": 12},
        )
