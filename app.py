"""
app.py — AgentTrace UI server

Run with:
    uvicorn app:app --reload --port 8000
Then open: http://localhost:8000
"""

import uuid
import json
from pathlib import Path
from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from dotenv import load_dotenv

load_dotenv()

from audit.db import init_db, get_db
from agent.graph import build_graph

app = FastAPI(title="AgentTrace")
app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"]
)

init_db()
_graph = build_graph()


# ── Request / Response models ─────────────────────────────────────────────────


class QueryRequest(BaseModel):
    query: str


# ── Routes ────────────────────────────────────────────────────────────────────


@app.get("/", response_class=HTMLResponse)
def index():
    return Path("ui.html").read_text()


@app.post("/run")
def run_query(req: QueryRequest):
    if not req.query.strip():
        raise HTTPException(status_code=400, detail="Query cannot be empty")

    run_id = str(uuid.uuid4())

    with get_db() as db:
        db.execute(
            "INSERT INTO agent_runs (run_id, query, started_at) VALUES (?,?,?)",
            (run_id, req.query.strip(), datetime.now(timezone.utc).isoformat()),
        )

    initial_state = {
        "run_id": run_id,
        "query": req.query.strip(),
        "sub_queries": [],
        "search_results": [],
        "final_answer": "",
        "iteration": 0,
        "tokens_this_node": 0,
    }

    final_state = _graph.invoke(initial_state)

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

        spans = db.execute(
            """SELECT node_name, sequence_order, duration_ms, success, state_diff
               FROM spans WHERE run_id=? ORDER BY sequence_order""",
            (run_id,),
        ).fetchall()

        anomalies = db.execute(
            "SELECT anomaly_type, severity, description FROM anomalies WHERE run_id=?",
            (run_id,),
        ).fetchall()

    return {
        "run_id": run_id,
        "answer": final_state["final_answer"],
        "sub_queries": final_state["sub_queries"],
        "spans": [
            {
                "node": s["node_name"],
                "step": s["sequence_order"],
                "ms": s["duration_ms"],
                "success": bool(s["success"]),
                "changed": list(json.loads(s["state_diff"] or "{}").keys()),
            }
            for s in spans
        ],
        "anomalies": [
            {
                "type": a["anomaly_type"],
                "severity": a["severity"],
                "description": a["description"],
            }
            for a in anomalies
        ],
    }


@app.get("/runs")
def list_runs():
    with get_db() as db:
        rows = db.execute(
            """SELECT run_id, query, status, anomaly_count, started_at
               FROM agent_runs ORDER BY started_at DESC LIMIT 20"""
        ).fetchall()
    return [dict(r) for r in rows]
