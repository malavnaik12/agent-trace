import json
import uuid
from datetime import datetime
from audit.db import get_db

# Tokens above this count are flagged as a spike even when no baseline exists yet.
_INITIAL_SPIKE_THRESHOLD = 1000


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
    Three rules for the MVP — returns a list of detected anomaly dicts so the
    caller can forward them as real-time events.

      1. stale_state     — node ran but changed nothing (HIGH)
      2. unexpected_loop — same node visited more than twice in this run (HIGH)
      3. token_spike     — tokens > 2× rolling avg for this node, or > 1000
                           when no baseline exists yet (MED)
    """
    detected = []

    # Rule 1: stale_state — diff is empty, node silently did nothing
    if not diff:
        desc = f"{node_name} executed but made no state change — possible silent failure"
        _insert_anomaly(run_id, span_id, "stale_state", "high", desc, {"node": node_name})
        detected.append({"type": "stale_state", "severity": "high", "description": desc})

    with get_db() as db:
        # Rule 2: unexpected_loop — count includes the span just inserted
        count = db.execute(
            "SELECT COUNT(*) FROM spans WHERE run_id=? AND node_name=?",
            (run_id, node_name),
        ).fetchone()[0]
        if count > 2:
            desc = f"{node_name} has been visited {count} times in this run — agent may be stuck"
            _insert_anomaly(
                run_id, span_id, "unexpected_loop", "high", desc,
                {"node": node_name, "visit_count": count},
            )
            detected.append({"type": "unexpected_loop", "severity": "high", "description": desc})

        # Rule 3: token_spike
        tokens = output_state.get("tokens_this_node") or 0
        if tokens > 0:
            row = db.execute(
                """SELECT AVG(tokens_used) FROM (
                     SELECT tokens_used FROM spans
                     WHERE node_name=? AND tokens_used IS NOT NULL AND tokens_used > 0
                     ORDER BY started_at DESC LIMIT 20
                   )""",
                (node_name,),
            ).fetchone()
            avg = row[0] or 0

            # Spike if tokens exceed 2× rolling average, or exceed the initial
            # threshold when no baseline exists yet (avg == 0).
            spike_threshold = avg * 2 if avg > 0 else _INITIAL_SPIKE_THRESHOLD
            if tokens > spike_threshold:
                avg_label = f"{avg:.0f}" if avg > 0 else "none (first baseline)"
                desc = f"{node_name} used {tokens} tokens — threshold {spike_threshold:.0f} (avg: {avg_label})"
                _insert_anomaly(
                    run_id, span_id, "token_spike", "medium", desc,
                    {"tokens": tokens, "rolling_avg": round(avg), "threshold": round(spike_threshold), "node": node_name},
                )
                detected.append({"type": "token_spike", "severity": "medium", "description": desc})

    return detected
