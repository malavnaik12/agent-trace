import json
import uuid
from datetime import datetime
from audit.db import get_db


def _insert_anomaly(run_id, span_id, anomaly_type, severity, description, evidence):
    with get_db() as db:
        db.execute(
            """INSERT INTO anomalies
                 (anomaly_id, run_id, span_id, anomaly_type, severity,
                  description, detected_at, evidence)
               VALUES (?,?,?,?,?,?,?,?)""",
            (
                str(uuid.uuid4()),
                run_id,
                span_id,
                anomaly_type,
                severity,
                description,
                datetime.utcnow().isoformat(),
                json.dumps(evidence),
            ),
        )
        db.execute(
            "UPDATE agent_runs SET anomaly_count = anomaly_count + 1, status = 'anomalous' WHERE run_id = ?",
            (run_id,),
        )


def detect_anomalies(run_id, span_id, node_name, output_state, diff, duration_ms):
    """
    Three rules for the MVP:
      1. stale_state     — node ran but changed nothing (HIGH)
      2. unexpected_loop — same node visited more than twice (HIGH)
      3. token_spike     — tokens > 2x rolling avg for this node (MED)
    """

    # Rule 1: stale_state
    if not diff:
        _insert_anomaly(
            run_id,
            span_id,
            "stale_state",
            "high",
            f"{node_name} executed but made no state change — possible silent failure",
            {"node": node_name, "diff": {}},
        )

    with get_db() as db:
        # Rule 2: unexpected_loop
        count = db.execute(
            "SELECT COUNT(*) FROM spans WHERE run_id=? AND node_name=?",
            (run_id, node_name),
        ).fetchone()[0]
        if count > 2:
            _insert_anomaly(
                run_id,
                span_id,
                "unexpected_loop",
                "high",
                f"{node_name} has been visited {count} times — agent may be stuck",
                {"node": node_name, "visit_count": count},
            )

        # Rule 3: token_spike
        tokens = output_state.get("tokens_this_node")
        if tokens:
            row = db.execute(
                """SELECT AVG(tokens_used) FROM (
                     SELECT tokens_used FROM spans
                     WHERE node_name=? AND tokens_used IS NOT NULL
                     ORDER BY started_at DESC LIMIT 20
                   )""",
                (node_name,),
            ).fetchone()
            avg = row[0] or 0
            if avg > 0 and tokens > avg * 2:
                _insert_anomaly(
                    run_id,
                    span_id,
                    "token_spike",
                    "medium",
                    f"{node_name} used {tokens} tokens vs rolling avg {avg:.0f}",
                    {"tokens": tokens, "rolling_avg": round(avg), "node": node_name},
                )
