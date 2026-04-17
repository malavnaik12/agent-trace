# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

**AgentTrace** — an auditable research agent that decomposes user queries into focused sub-queries, retrieves results via web search, synthesizes answers, and records detailed execution traces with anomaly detection.

## Running the project

**Dependencies:** Requires `ANTHROPIC_API_KEY` and `TAVILY_API_KEY` in `.env`.

```bash
pip install -r requirements.txt

# CLI mode
python main.py "your question here"

# Web UI (http://localhost:8000)
uvicorn app:app --reload --port 8000

# Terminal trace inspector
python viewer.py              # list last 10 runs
python viewer.py <run_id>     # full trace for a specific run
```

## Architecture

The agent runs as a **linear LangGraph state graph**: `plan → search → synthesize → END`.

- **`agent/graph.py`** — Compiles the LangGraph `StateGraph`. Each node is wrapped with the `traced()` decorator before being registered.
- **`agent/nodes.py`** — Three pure functions operating on `AgentState`: `plan` (calls Claude to decompose the query into 2–3 sub-queries), `search` (calls Tavily API for each sub-query), `synthesize` (calls Claude to produce a cited final answer).
- **`agent/state.py`** — `AgentState` TypedDict: `run_id`, `query`, `sub_queries`, `search_results`, `final_answer`, `iteration`, `tokens_this_node`.

### Audit layer

The `audit/` package wraps nodes transparently and stores everything in **SQLite** (`agenttrace.db`).

- **`audit/tracer.py`** — `traced()` decorator. Captures `input_snapshot` / `output_snapshot` JSON, timing (`duration_ms`), DeepDiff between states, then inserts a `spans` row and triggers anomaly detection.
- **`audit/anomalies.py`** — Three rules run after each span: `stale_state` (HIGH, diff is empty), `unexpected_loop` (HIGH, node visited >2×), `token_spike` (MED, tokens >2× rolling avg of last 20 runs).
- **`audit/db.py`** — `get_db()` context manager; initializes schema from `db/schema.sql` on first run; WAL mode + foreign keys enabled.

### Database schema (`db/schema.sql`)

Three tables: `agent_runs` (one row per execution), `spans` (one row per node invocation), `anomalies` (one row per detected issue). `agent_runs.status` is set to `'anomalous'` if any anomaly fires.

### Web server (`app.py`)

FastAPI with three routes:
- `GET /` — serves `ui.html`
- `POST /run` — accepts `{query}`, invokes graph, returns `{run_id, answer, sub_queries, spans, anomalies}`
- `GET /runs` — last 20 runs with metadata

### LLM usage

All LLM calls use **Claude Haiku 4.5** (`claude-haiku-4-5-20251001`) with tight token limits (256 for plan, 512 for synthesize). No tool use — synthesis and query decomposition are prompt-only.

## Notes

- `.claude/settings.local.json` restricts Bash to `git ls-tree` commands. Update permissions as the project grows.
- `agenttrace.db` is gitignored; delete it locally to reset all trace data.
- The `iteration` field and loop detection rules exist for future multi-turn refinement — the current MVP always completes in a single pass.
