"""
app.py — AgentTrace UI server

Run with:
    uvicorn app:app --reload --port 8000
Then open: http://localhost:8000
"""

import uuid
import json
import asyncio
from pathlib import Path
from datetime import datetime, timezone
from typing import Literal

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from anthropic import Anthropic
from langgraph.graph import StateGraph, END
from dotenv import load_dotenv

load_dotenv()

from audit.db import init_db, get_db
from agent.graph import build_graph
from agent.state import AgentState
from agent.nodes import plan, search, synthesize
from agent.query_generator import generate_query
from audit.tracer import traced, _span_callback

app = FastAPI(title="AgentTrace")
app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"]
)

init_db()
_graph = build_graph()
_llm = Anthropic()


# ── Request / Response models ─────────────────────────────────────────────────


class QueryRequest(BaseModel):
    query: str


class TestRequest(BaseModel):
    scenario: Literal["clean", "stale_state", "token_spike", "unexpected_loop"]


# ── Test scenario graph builders ──────────────────────────────────────────────

# Each builder returns a compiled graph that injects a specific anomaly condition.

def _build_stale_graph():
    """plan is a no-op → diff is empty → stale_state fires (HIGH)."""
    def plan_noop(state):
        return state  # returns state unchanged; DeepDiff produces {}

    g = StateGraph(AgentState)
    g.add_node("plan", traced(plan_noop))
    g.add_node("search", traced(search))
    g.add_node("synthesize", traced(synthesize))
    g.set_entry_point("plan")
    g.add_edge("plan", "search")
    g.add_edge("search", "synthesize")
    g.add_edge("synthesize", END)
    return g.compile()


def _build_token_spike_graph():
    """plan reports artificially high token usage → token_spike fires (MED)."""
    def plan_spike(state):
        result = plan(state)
        return {**result, "tokens_this_node": 9999}

    g = StateGraph(AgentState)
    g.add_node("plan", traced(plan_spike))
    g.add_node("search", traced(search))
    g.add_node("synthesize", traced(synthesize))
    g.set_entry_point("plan")
    g.add_edge("plan", "search")
    g.add_edge("search", "synthesize")
    g.add_edge("synthesize", END)
    return g.compile()


def _build_loop_graph():
    """plan loops 3 times before real planning → unexpected_loop fires (HIGH) on visit 3."""
    def plan_looping(state):
        # Dummy iterations 0 and 1; real plan on iteration 2
        if state["iteration"] < 2:
            return {**state, "iteration": state["iteration"] + 1}
        result = plan(state)
        return {**result, "iteration": state["iteration"] + 1}

    def route_plan(state):
        return "search" if state["iteration"] >= 3 else "plan"

    g = StateGraph(AgentState)
    g.add_node("plan", traced(plan_looping))
    g.add_node("search", traced(search))
    g.add_node("synthesize", traced(synthesize))
    g.set_entry_point("plan")
    g.add_conditional_edges("plan", route_plan, {"plan": "plan", "search": "search"})
    g.add_edge("search", "synthesize")
    g.add_edge("synthesize", END)
    return g.compile()


_TEST_QUERIES = {
    "clean":            "What is the boiling point of water and why does altitude affect it?",
    "stale_state":      "How do trees convert sunlight into energy through photosynthesis?",
    "token_spike":      "What are the main health benefits of regular aerobic exercise?",
    "unexpected_loop":  "How do vaccines train the immune system to fight disease?",
}


def _run_scenario(graph, query: str) -> dict:
    """Execute a graph and return the structured result (spans + anomalies)."""
    run_id = str(uuid.uuid4())

    with get_db() as db:
        db.execute(
            "INSERT INTO agent_runs (run_id, query, started_at) VALUES (?,?,?)",
            (run_id, query, datetime.now(timezone.utc).isoformat()),
        )

    initial_state = {
        "run_id": run_id, "query": query,
        "sub_queries": [], "search_results": [],
        "final_answer": "", "iteration": 0, "tokens_this_node": 0,
    }

    final_state = graph.invoke(initial_state)

    with get_db() as db:
        db.execute(
            """UPDATE agent_runs SET status='completed', completed_at=?, final_answer=?
               WHERE run_id=?""",
            (datetime.now(timezone.utc).isoformat(), final_state["final_answer"], run_id),
        )
        spans = db.execute(
            """SELECT node_name, sequence_order, duration_ms, success, state_diff, tokens_used
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
                "tokens": s["tokens_used"],
            }
            for s in spans
        ],
        "anomalies": [
            {"type": a["anomaly_type"], "severity": a["severity"], "description": a["description"]}
            for a in anomalies
        ],
    }


# ── Agent loops (live demo via WebSocket) ─────────────────────────────────────


async def _generator_loop(query_queue: asyncio.Queue, push) -> None:
    """Agent 1 — continuously generates research queries and enqueues them."""
    while True:
        await push({"type": "agent1_thinking"})
        try:
            query = await generate_query(_llm)
            await push({"type": "agent1_query", "query": query})
            await query_queue.put(query)
            await query_queue.join()
            await asyncio.sleep(4)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            await push({"type": "agent1_error", "error": str(e)})
            await asyncio.sleep(5)


async def _processor_loop(
    query_queue: asyncio.Queue,
    push,
    push_from_thread,
    loop: asyncio.AbstractEventLoop,
) -> None:
    """Agent 2 — processes queries through plan → search → synthesize."""
    while True:
        query = await query_queue.get()
        run_id = str(uuid.uuid4())
        try:
            await push({"type": "agent2_start", "query": query, "run_id": run_id})

            with get_db() as db:
                db.execute(
                    "INSERT INTO agent_runs (run_id, query, started_at) VALUES (?,?,?)",
                    (run_id, query, datetime.now(timezone.utc).isoformat()),
                )

            initial_state = {
                "run_id": run_id, "query": query,
                "sub_queries": [], "search_results": [],
                "final_answer": "", "iteration": 0, "tokens_this_node": 0,
            }

            _span_callback.set(push_from_thread)
            final_state = await loop.run_in_executor(None, _graph.invoke, initial_state)

            with get_db() as db:
                db.execute(
                    """UPDATE agent_runs SET status='completed', completed_at=?, final_answer=?
                       WHERE run_id=?""",
                    (datetime.now(timezone.utc).isoformat(), final_state["final_answer"], run_id),
                )
                spans = db.execute(
                    """SELECT node_name, sequence_order, duration_ms, success, state_diff, tokens_used
                       FROM spans WHERE run_id=? ORDER BY sequence_order""",
                    (run_id,),
                ).fetchall()
                anomalies = db.execute(
                    "SELECT anomaly_type, severity, description FROM anomalies WHERE run_id=?",
                    (run_id,),
                ).fetchall()

            await push({
                "type": "agent2_complete",
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
                        "tokens": s["tokens_used"],
                    }
                    for s in spans
                ],
                "anomalies": [
                    {"type": a["anomaly_type"], "severity": a["severity"], "description": a["description"]}
                    for a in anomalies
                ],
            })

        except asyncio.CancelledError:
            raise
        except Exception as e:
            await push({"type": "agent2_error", "error": str(e)})
        finally:
            query_queue.task_done()


# ── Routes ────────────────────────────────────────────────────────────────────


@app.get("/", response_class=HTMLResponse)
def index():
    return Path("ui.html").read_text(encoding="utf-8")


@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket):
    await ws.accept()

    send_queue: asyncio.Queue = asyncio.Queue()
    query_queue: asyncio.Queue = asyncio.Queue(maxsize=1)
    loop = asyncio.get_running_loop()

    async def push(msg: dict) -> None:
        await send_queue.put(msg)

    def push_from_thread(msg: dict) -> None:
        loop.call_soon_threadsafe(send_queue.put_nowait, msg)

    async def ws_writer() -> None:
        while True:
            msg = await send_queue.get()
            if msg is None:
                break
            try:
                await ws.send_json(msg)
            except Exception:
                return

    writer_task = asyncio.create_task(ws_writer())
    gen_task = asyncio.create_task(_generator_loop(query_queue, push))
    proc_task = asyncio.create_task(
        _processor_loop(query_queue, push, push_from_thread, loop)
    )

    await push({"type": "connected"})

    try:
        async for _ in ws.iter_text():
            pass
    except (WebSocketDisconnect, Exception):
        pass
    finally:
        gen_task.cancel()
        proc_task.cancel()
        await asyncio.gather(gen_task, proc_task, return_exceptions=True)
        send_queue.put_nowait(None)
        await writer_task


@app.post("/run")
def run_query(req: QueryRequest):
    if not req.query.strip():
        raise HTTPException(status_code=400, detail="Query cannot be empty")
    return _run_scenario(_graph, req.query.strip())


@app.post("/run/test")
def run_test(req: TestRequest):
    """
    Run a pre-wired anomaly test scenario and return the full trace + anomalies.

    Scenarios:
      clean            — normal run, no anomalies expected
      stale_state      — plan returns unchanged state  → HIGH stale_state
      token_spike      — plan reports 9999 tokens       → MED  token_spike
      unexpected_loop  — plan loops 3×                 → HIGH unexpected_loop
    """
    graphs = {
        "clean":           _graph,
        "stale_state":     _build_stale_graph(),
        "token_spike":     _build_token_spike_graph(),
        "unexpected_loop": _build_loop_graph(),
    }
    return _run_scenario(graphs[req.scenario], _TEST_QUERIES[req.scenario])


@app.get("/runs")
def list_runs():
    with get_db() as db:
        rows = db.execute(
            """SELECT run_id, query, status, anomaly_count, started_at
               FROM agent_runs ORDER BY started_at DESC LIMIT 20"""
        ).fetchall()
    return [dict(r) for r in rows]
