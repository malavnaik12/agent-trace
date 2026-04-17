from typing import TypedDict, List, Optional


class AgentState(TypedDict):
    # Identity — set at graph entry, never modified
    run_id: str
    query: str

    # plan node output
    sub_queries: List[str]

    # search node output
    search_results: List[dict]  # {"url": str, "title": str, "snippet": str}

    # synthesize node output
    final_answer: str

    # control flow
    iteration: int

    tokens_this_node: int
