"""
viewer.py — inspect AgentTrace runs from the terminal.

Usage:
    python viewer.py            # list last 10 runs
    python viewer.py <run_id>   # show full trace for a run
"""

import sys
import json
from audit.db import init_db, get_db


def list_runs():
    with get_db() as db:
        rows = db.execute(
            """SELECT run_id, query, status, anomaly_count, started_at
               FROM agent_runs ORDER BY started_at DESC LIMIT 10"""
        ).fetchall()

    if not rows:
        print("No runs yet. Run: python main.py \"your question\"")
        return

    print(f"\n{'STATUS':<12} {'ANOMALIES':<10} {'QUERY':<50} RUN ID")
    print("─" * 100)
    for r in rows:
        flag = "⚠" if r["anomaly_count"] > 0 else "✓"
        status = f"{flag} {r['status']}"
        query_preview = (r["query"] or "")[:48]
        print(f"{status:<12} {r['anomaly_count']:<10} {query_preview:<50} {r['run_id']}")


def show_run(run_id: str):
    with get_db() as db:
        run = db.execute(
            "SELECT * FROM agent_runs WHERE run_id=?", (run_id,)
        ).fetchone()

        if not run:
            print(f"No run found with id: {run_id}")
            return

        spans = db.execute(
            """SELECT * FROM spans WHERE run_id=?
               ORDER BY sequence_order""", (run_id,)
        ).fetchall()

        anomalies = db.execute(
            "SELECT * FROM anomalies WHERE run_id=?", (run_id,)
        ).fetchall()

    print(f"\n── Run ─────────────────────────────────────────")
    print(f"  id:       {run['run_id']}")
    print(f"  query:    {run['query']}")
    print(f"  status:   {run['status']}")
    print(f"  anomalies:{run['anomaly_count']}")

    print(f"\n── Spans ({len(spans)}) ──────────────────────────────")
    for s in spans:
        ok = "✓" if s["success"] else "✗"
        diff = json.loads(s["state_diff"] or "{}")
        changed = list(diff.keys()) if diff else ["(no change)"]
        print(f"  {s['sequence_order']}. {ok} {s['node_name']:<14} {s['duration_ms']}ms   changed: {', '.join(changed)}")
        if s["error_message"]:
            print(f"       error: {s['error_message']}")

    if anomalies:
        print(f"\n── Anomalies ({len(anomalies)}) ──────────────────────────")
        for a in anomalies:
            print(f"  [{a['severity'].upper()}] {a['anomaly_type']}")
            print(f"         {a['description']}")
    else:
        print(f"\n── No anomalies detected ✓")

    if run["final_answer"]:
        print(f"\n── Answer ───────────────────────────────────────")
        print(f"  {run['final_answer'][:300]}{'...' if len(run['final_answer']) > 300 else ''}")
    print()


if __name__ == "__main__":
    init_db()
    if len(sys.argv) > 1:
        show_run(sys.argv[1])
    else:
        list_runs()
