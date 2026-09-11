"""
Scan history persistence (SQLite, zero-config).

Every analyze/research/growth-plan call records the account's metrics, so the
UI can show follower/engagement trends and since-last-scan deltas.
"""
import os
import sqlite3
from datetime import datetime, timezone
from typing import List, Optional, Tuple

from models import ProfileInsight, ScanRecord

DB_PATH = os.getenv("SCAN_HISTORY_DB", os.path.join(os.path.dirname(__file__), "scan_history.db"))

_SCHEMA = """
CREATE TABLE IF NOT EXISTS scans (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT NOT NULL,
    scanned_at TEXT NOT NULL,
    followers INTEGER NOT NULL,
    engagement_rate REAL NOT NULL,
    avg_likes REAL NOT NULL,
    posting_frequency_per_week REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_scans_username ON scans(username, scanned_at);
"""


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.executescript(_SCHEMA)  # executescript allows multiple statements
    return conn


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def record_scan(insight: ProfileInsight) -> None:
    """Insert one scan row (best-effort: persistence must never break the API)."""
    try:
        with _connect() as conn:
            conn.execute(
                "INSERT INTO scans (username, scanned_at, followers, engagement_rate, avg_likes, posting_frequency_per_week) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    insight.profile.username.lower(),
                    _now(),
                    insight.profile.followers,
                    insight.metrics.engagement_rate,
                    insight.metrics.avg_likes,
                    insight.metrics.posting_frequency_per_week,
                ),
            )
    except Exception:
        pass  # storage is best-effort


def get_history(username: str) -> Tuple[List[ScanRecord], Optional[ScanRecord]]:
    """All scans for a username (oldest → newest) plus the previous scan
    (second-newest) for delta computation."""
    try:
        with _connect() as conn:
            rows = conn.execute(
                "SELECT scanned_at, followers, engagement_rate, avg_likes, posting_frequency_per_week "
                "FROM scans WHERE username = ? ORDER BY scanned_at ASC",
                (username.lower(),),
            ).fetchall()
    except Exception:
        return [], None

    records = [
        ScanRecord(
            scanned_at=ts,
            followers=f,
            engagement_rate=er,
            avg_likes=al,
            posting_frequency_per_week=cad,
            followers_delta=0,
            er_delta=0.0,
        )
        for ts, f, er, al, cad in rows
    ]
    for i in range(1, len(records)):
        records[i].followers_delta = records[i].followers - records[i - 1].followers
        records[i].er_delta = round(records[i].engagement_rate - records[i - 1].engagement_rate, 3)

    previous = records[-2] if len(records) >= 2 else None
    return records, previous
