import time
import uuid
import json
import contextvars
from functools import wraps
from deepdiff import DeepDiff
from audit.db import get_db
from audit.anomalies import detect_anomalies

# Per-execution callback for live node-level events.
# Set this ContextVar before invoking the graph; run_in_executor copies it
# into the worker thread so the tracer can push updates from the thread.
_span_callback = contextvars.ContextVar("span_callback", default=None)


def traced(node_fn):
    """
    Wrap a LangGraph node function with full audit tracing.

    Usage:
        g.add_node("plan", traced(plan))
    """

    @wraps(node_fn)
    def wrapper(state):
        span_id = str(uuid.uuid4())
        run_id = state["run_id"]
        node_name = node_fn.__name__
        started = time.time()

        cb = _span_callback.get(None)
        if cb:
            cb({"type": "node_start", "node": node_name})

        input_snapshot = json.loads(json.dumps(state, default=str))

        try:
            output_state = node_fn(state)
            success, error = True, None
        except Exception as e:
            output_state = state
            success, error = False, str(e)

        duration_ms = int((time.time() - started) * 1000)
        output_snapshot = json.loads(json.dumps(output_state, default=str))
        diff = DeepDiff(input_snapshot, output_snapshot, ignore_order=True).to_dict()

        tokens_used = output_snapshot.get("tokens_this_node") or None

        with get_db() as db:
            seq = db.execute(
                "SELECT COUNT(*) FROM spans WHERE run_id=?", (run_id,)
            ).fetchone()[0]

            db.execute(
                """INSERT INTO spans
                     (span_id, run_id, node_name, sequence_order, started_at,
                      duration_ms, input_state, output_state, state_diff,
                      tokens_used, success, error_message)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    span_id, run_id, node_name, seq, str(started),
                    duration_ms,
                    json.dumps(input_snapshot, default=str),
                    json.dumps(output_snapshot, default=str),
                    json.dumps(diff, default=str),
                    tokens_used,
                    1 if success else 0,
                    error,
                ),
            )

        # detect_anomalies returns list of fired rules for real-time notification
        anomalies = detect_anomalies(run_id, span_id, node_name, output_snapshot, diff, duration_ms)

        if cb:
            cb({
                "type": "node_done",
                "node": node_name,
                "ms": duration_ms,
                "success": success,
                "changed": list(diff.keys()),
                "anomalies": anomalies,
            })

        if not success:
            raise RuntimeError(error)

        return output_state

    return wrapper
