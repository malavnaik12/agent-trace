import time
import uuid
import json
from functools import wraps
from deepdiff import DeepDiff
from audit.db import get_db
from audit.anomalies import detect_anomalies


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

        # Snapshot input state before the node runs
        input_snapshot = json.loads(json.dumps(state, default=str))

        try:
            output_state = node_fn(state)
            success, error = True, None
        except Exception as e:
            output_state = state  # return unchanged state on failure
            success, error = False, str(e)

        duration_ms = int((time.time() - started) * 1000)
        output_snapshot = json.loads(json.dumps(output_state, default=str))

        # What did this node actually change?
        diff = DeepDiff(input_snapshot, output_snapshot, ignore_order=True).to_dict()

        with get_db() as db:
            seq = db.execute(
                "SELECT COUNT(*) FROM spans WHERE run_id=?", (run_id,)
            ).fetchone()[0]

            db.execute(
                """INSERT INTO spans
                     (span_id, run_id, node_name, sequence_order, started_at,
                      duration_ms, input_state, output_state, state_diff,
                      success, error_message)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    span_id,
                    run_id,
                    node_name,
                    seq,
                    str(started),
                    duration_ms,
                    json.dumps(input_snapshot, default=str),
                    json.dumps(output_snapshot, default=str),
                    json.dumps(diff, default=str),
                    1 if success else 0,
                    error,
                ),
            )

        detect_anomalies(run_id, span_id, node_name, output_snapshot, diff, duration_ms)

        if not success:
            raise RuntimeError(error)

        return output_state

    return wrapper
