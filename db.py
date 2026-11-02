"""Persistance métier légère : dédup des leads et suivi de leur statut.

Distincte du checkpointer LangGraph (agent.py), qui stocke l'état complet du graphe
mais n'est pas fait pour être interrogé ("quels leads sont en attente ?"). Cette table
est la source de vérité métier pour la dédup rapide et l'audit.
"""
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Optional

import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS leads (
    dedup_key TEXT PRIMARY KEY,
    thread_id TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    icp_score INTEGER,
    sf_lead_id TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
"""


@contextmanager
def _connect():
    conn = sqlite3.connect(config.DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db() -> None:
    with _connect() as conn:
        conn.execute(SCHEMA)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def get_lead(dedup_key: str) -> Optional[sqlite3.Row]:
    with _connect() as conn:
        return conn.execute(
            "SELECT * FROM leads WHERE dedup_key = ?", (dedup_key,)
        ).fetchone()


def create_lead(dedup_key: str, thread_id: str) -> None:
    now = _now()
    with _connect() as conn:
        conn.execute(
            """INSERT INTO leads (dedup_key, thread_id, status, created_at, updated_at)
               VALUES (?, ?, 'pending', ?, ?)""",
            (dedup_key, thread_id, now, now),
        )


def update_status(dedup_key: str, status: str, sf_lead_id: Optional[str] = None) -> None:
    with _connect() as conn:
        conn.execute(
            """UPDATE leads SET status = ?, sf_lead_id = COALESCE(?, sf_lead_id), updated_at = ?
               WHERE dedup_key = ?""",
            (status, sf_lead_id, _now(), dedup_key),
        )


def update_score(dedup_key: str, icp_score: int) -> None:
    with _connect() as conn:
        conn.execute(
            "UPDATE leads SET icp_score = ?, updated_at = ? WHERE dedup_key = ?",
            (icp_score, _now(), dedup_key),
        )
