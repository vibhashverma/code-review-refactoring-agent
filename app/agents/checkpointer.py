"""SQLite-backed LangGraph checkpointer (state survives restarts)."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from langgraph.checkpoint.sqlite import SqliteSaver


def create_checkpointer(db_path: str) -> SqliteSaver:
    """Open (creating if needed) a persistent SQLite checkpoint store.

    ``check_same_thread=False`` is required because FastAPI runs sync endpoints
    in a thread pool; ``SqliteSaver`` serialises access with an internal lock.
    """
    if db_path != ":memory:":
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path, check_same_thread=False)
    return SqliteSaver(conn)
