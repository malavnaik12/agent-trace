import sqlite3
import pathlib
from contextlib import contextmanager

DB_PATH = pathlib.Path("agenttrace.db")
_SCHEMA = pathlib.Path(__file__).parent.parent / "db" / "schema.sql"


def init_db(path: pathlib.Path = DB_PATH) -> None:
    """Create tables if they don't exist. Call once at startup."""
    with sqlite3.connect(path) as conn:
        conn.executescript(_SCHEMA.read_text())
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")


@contextmanager
def get_db(path: pathlib.Path = DB_PATH):
    """Simple connection context manager. Commits on exit, rolls back on error."""
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
