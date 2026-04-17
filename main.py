"""
AgentTrace MVP — run with:
    python main.py "your question here"
"""

import uuid
import sys
from datetime import datetime, timezone
from dotenv import load_dotenv

from audit.db import init_db, get_db
from agent.graph import build_graph

load_dotenv()


def run(query: str) -> str:
    init_db()

    run_id = str(uuid.uuid4())

    # Create the run record before the graph starts
    with get_db() as db:
        db.execute(
            "INSERT INTO agent_runs (run_id, query, started_at) VALUES (?,?,?)",
            (run_id, query, datetime.now(timezone.utc).isoformat()),
        )

    graph = build_graph()
    initial_state = {
        "run_id": run_id,
        "query": query,
        "sub_queries": [],
        "search_results": [],
        "final_answer": "",
        "iteration": 0,
    }

    print(f"\n▶ run_id: {run_id}")
    print(f"▶ query:  {query}\n")

    final_state = graph.invoke(initial_state)

    # Mark run complete
    with get_db() as db:
        db.execute(
            """UPDATE agent_runs
               SET status='completed', completed_at=?, final_answer=?
               WHERE run_id=?""",
            (
                datetime.now(timezone.utc).isoformat(),
                final_state["final_answer"],
                run_id,
            ),
        )

    print("\n── Answer ──────────────────────────────────────")
    print(final_state["final_answer"])
    print("────────────────────────────────────────────────")
    print(f"\n✓ Trace saved → agenttrace.db (run_id: {run_id})")
    return final_state["final_answer"]


if __name__ == "__main__":
    query = (
        " ".join(sys.argv[1:])
        if len(sys.argv) > 1
        else "What is LangGraph and how does it work?"
    )
    run(query)
