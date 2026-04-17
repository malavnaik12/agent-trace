"""
MVP nodes — three nodes only:
  plan      → decompose the query into sub-queries
  search    → run each sub-query through Tavily
  synthesize → produce a final answer from search results

This is intentionally small. fetch and classify come in the next iteration.
"""

import os
from anthropic import Anthropic
from tavily import TavilyClient
from agent.state import AgentState

_llm    = Anthropic()
_tavily = None

def _get_tavily():
    global _tavily
    if _tavily is None:
        _tavily = TavilyClient(api_key=os.environ["TAVILY_API_KEY"])
    return _tavily


def plan(state: AgentState) -> AgentState:
    """
    Decompose the user's query into 2-3 focused search sub-queries.
    """
    response = _llm.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=256,
        system=(
            "You are a research planner. Given a question, produce exactly 2 focused "
            "web search queries that together would answer it. "
            "Respond with one query per line, no numbering, no explanation."
        ),
        messages=[{"role": "user", "content": state["query"]}],
    )
    sub_queries = [
        line.strip()
        for line in response.content[0].text.strip().splitlines()
        if line.strip()
    ][:3]  # cap at 3

    return {**state, "sub_queries": sub_queries}


def search(state: AgentState) -> AgentState:
    """
    Run each sub-query through Tavily and collect results.
    """
    all_results = []
    for q in state["sub_queries"]:
        results = _get_tavily().search(q, max_results=3)
        for r in results.get("results", []):
            all_results.append({
                "query": q,
                "url":     r.get("url", ""),
                "title":   r.get("title", ""),
                "snippet": r.get("content", "")[:500],
            })

    return {**state, "search_results": all_results, "iteration": state["iteration"] + 1}


def synthesize(state: AgentState) -> AgentState:
    """
    Produce a grounded answer from the search results.
    """
    context = "\n\n".join(
        f"[{r['title']}] ({r['url']})\n{r['snippet']}"
        for r in state["search_results"]
    )

    response = _llm.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=512,
        system=(
            "You are a research synthesizer. Using only the provided search results, "
            "answer the question clearly and concisely. Cite sources by URL inline."
        ),
        messages=[{
            "role": "user",
            "content": f"Question: {state['query']}\n\nSearch results:\n{context}"
        }],
    )

    return {**state, "final_answer": response.content[0].text.strip()}
