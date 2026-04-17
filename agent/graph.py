from langgraph.graph import StateGraph, END
from agent.state import AgentState
from agent.nodes import plan, search, synthesize
from audit.tracer import traced


def build_graph():
    g = StateGraph(AgentState)

    g.add_node("plan",      traced(plan))
    g.add_node("search",    traced(search))
    g.add_node("synthesize",traced(synthesize))

    g.set_entry_point("plan")
    g.add_edge("plan",      "search")
    g.add_edge("search",    "synthesize")
    g.add_edge("synthesize", END)

    return g.compile()
