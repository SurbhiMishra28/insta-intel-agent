"""
Scan history persistence (SQLite, zero-config).

Every analyze/research/growth-plan call records the account's metrics, so the
UI can show follower/engagement trends and since-last-scan deltas, and the
growth tracker can compare an account NOW vs its stored past (last scan,
1 week ago, 1 month ago) — all from real recorded searches, never invented.
"""
import os
import sqlite3
from datetime import datetime, timedelta, timezone
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
    posting_frequency_per_week REAL NOT NULL,
    posts_count INTEGER,
    avg_comments REAL
);
CREATE INDEX IF NOT EXISTS idx_scans_username ON scans(username, scanned_at);
"""


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.executescript(_SCHEMA)  # executescript allows multiple statements
    # Migration for DBs created before posts_count/avg_comments existed.
    for col, ddl in (("posts_count", "ALTER TABLE scans ADD COLUMN posts_count INTEGER"),
                     ("avg_comments", "ALTER TABLE scans ADD COLUMN avg_comments REAL")):
        try:
            conn.execute(ddl)
        except sqlite3.OperationalError:
            pass  # column already exists
    return conn


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def record_scan(insight: ProfileInsight) -> None:
    """Insert one scan row (best-effort: persistence must never break the API).

    REAL HISTORY ONLY: simulated profiles carry the badge
    `data_age_hours == -1` and are refused — growth tracking must never
    build trends out of invented numbers."""
    try:
        if getattr(insight.profile, "data_age_hours", None) == -1:
            return  # simulated — never record
        record_metrics(
            insight.profile.username,
            followers=insight.profile.followers,
            engagement_rate=insight.metrics.engagement_rate,
            avg_likes=insight.metrics.avg_likes,
            posting_frequency_per_week=insight.metrics.posting_frequency_per_week,
            posts_count=insight.profile.posts_count,
            avg_comments=insight.metrics.avg_comments,
        )
    except Exception:
        pass  # storage is best-effort


def record_metrics(
    username: str,
    followers: int,
    engagement_rate: float,
    avg_likes: float,
    posting_frequency_per_week: float,
    posts_count: Optional[int] = None,
    avg_comments: Optional[float] = None,
) -> None:
    """Record one timeline point from raw real metrics (no insight object
    needed) — used by chat grounding and any path that measures a real
    profile outside the full analysis pipeline. Best-effort."""
    try:
        with _connect() as conn:
            conn.execute(
                "INSERT INTO scans (username, scanned_at, followers, engagement_rate, avg_likes, "
                "posting_frequency_per_week, posts_count, avg_comments) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    (username or "").strip().lstrip("@").lower(),
                    _now(),
                    followers,
                    engagement_rate,
                    avg_likes,
                    posting_frequency_per_week,
                    posts_count,
                    avg_comments,
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
                "SELECT scanned_at, followers, engagement_rate, avg_likes, posting_frequency_per_week, "
                "posts_count, avg_comments "
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
            posts_count=pc,
            avg_comments=ac,
            followers_delta=0,
            er_delta=0.0,
        )
        for ts, f, er, al, cad, pc, ac in rows
    ]
    for i in range(1, len(records)):
        records[i].followers_delta = records[i].followers - records[i - 1].followers
        records[i].er_delta = round(records[i].engagement_rate - records[i - 1].engagement_rate, 3)

    previous = records[-2] if len(records) >= 2 else None
    return records, previous


# ---------------------------------------------------------------------------
# Growth tracking — compare an account NOW against its stored past
# ---------------------------------------------------------------------------

def get_growth_comparison(username: str) -> dict:
    """Build a growth comparison for one handle entirely from stored scans.

    Baselines: previous scan (last two recorded searches), 1 week ago and
    1 month ago (nearest stored scan within a ±3-day tolerance window so a
    weekly/monthly ritual keeps working even if a scan is a day or two off).
    Every number is a real recorded measurement — when a baseline is missing
    the response says so honestly instead of inventing data.
    """
    uname = (username or "").strip().lstrip("@").lower()
    try:
        with _connect() as conn:
            rows = conn.execute(
                "SELECT scanned_at, followers, engagement_rate, avg_likes, "
                "posting_frequency_per_week, posts_count, avg_comments "
                "FROM scans WHERE username = ? ORDER BY scanned_at ASC",
                (uname,),
            ).fetchall()
    except Exception:
        rows = []

    if len(rows) < 2:
        return {
            "username": uname,
            "enough_history": False,
            "scan_count": len(rows),
            "first_scan": rows[0][0] if rows else None,
            "latest": None,
            "baselines": [],
            "verdict": (
                "Growth tracking starts from this account's first scan. "
                "Analyze this handle again later (a week or a month from now) "
                "and every new scan will be compared against today's stored data "
                "— followers, engagement and posting pace."
            ),
        }

    def _row(r):
        return {
            "scanned_at": r[0], "followers": r[1], "engagement_rate": r[2],
            "avg_likes": r[3], "posting_frequency_per_week": r[4],
            "posts_count": r[5], "avg_comments": r[6],
        }

    latest = _row(rows[-1])
    now = datetime.now(timezone.utc)

    # Scan exactly one step back — the most honest "since last search" delta.
    prev = _row(rows[-2])

    # Nearest scan within ±3 days of the target age (week/month ago).
    def _nearest(days: int):
        target = now - timedelta(days=days)
        best, best_gap = None, None
        for r in rows[:-1]:  # baselines must be strictly older than latest
            try:
                t = datetime.fromisoformat(r[0])
            except Exception:
                continue
            if t.tzinfo is None:
                # stored naive — treat as UTC
                t = t.replace(tzinfo=timezone.utc)
            gap = abs((t - target).total_seconds())
            if best_gap is None or gap < best_gap:
                best, best_gap = r, gap
        if best is not None and best_gap <= 3 * 86400:
            return _row(best)
        return None

    week = _nearest(7)
    month = _nearest(30)

    def _delta(cur, base, key):
        if cur is None or base is None:
            return None
        try:
            return round(cur[key] - base[key], 4)
        except (TypeError, KeyError):
        # posts_count/avg_comments may be NULL on pre-migration rows
            return None

    baselines = []
    for label, base, days in (("last scan", prev, None), ("1 week ago", week, 7), ("1 month ago", month, 30)):
        if base is None:
            baselines.append({"label": label, "available": False,
                              "days_back": days,
                              "note": "no stored scan near this date"})
            continue
        d_followers = _delta(latest, base, "followers")
        d_er = _delta(latest, base, "engagement_rate")
        d_likes = _delta(latest, base, "avg_likes")
        d_comments = _delta(latest, base, "avg_comments")
        d_posts = _delta(latest, base, "posts_count")
        d_cadence = _delta(latest, base, "posting_frequency_per_week")
        span_days = None
        try:
            span_days = round((datetime.fromisoformat(latest["scanned_at"]) -
                               datetime.fromisoformat(base["scanned_at"])).total_seconds() / 86400, 1)
        except Exception:
            pass
        baselines.append({
            "label": label,
            "available": True,
            "days_back": days,
            "scanned_at": base["scanned_at"],
            "span_days": span_days,
            "values": {
                "followers": base["followers"], "engagement_rate": base["engagement_rate"],
                "avg_likes": base["avg_likes"], "avg_comments": base["avg_comments"],
                "posts_count": base["posts_count"],
                "posting_frequency_per_week": base["posting_frequency_per_week"],
            },
            "deltas": {
                "followers": d_followers, "engagement_rate": d_er, "avg_likes": d_likes,
                "avg_comments": d_comments, "posts_count": d_posts,
                "posting_frequency_per_week": d_cadence,
            },
            "followers_delta_pct": (
                round(d_followers / base["followers"] * 100, 3)
                if d_followers is not None and base["followers"] else None
            ),
        })

    # Plain-language verdict from the strongest available baseline (month > week > last scan).
    verdict_parts = []
    chosen = next((b for b in reversed(baselines) if b["available"]), None)
    if chosen:
        d = chosen["deltas"]
        label = chosen["label"]
        if d["followers"] is not None and d["followers"] != 0:
            direction = "gained" if d["followers"] > 0 else "lost"
            verdict_parts.append(
                f"vs {label}: {'+' if d['followers'] > 0 else ''}{d['followers']:,} followers ({direction})"
            )
        elif d["followers"] == 0:
            verdict_parts.append(f"vs {label}: follower count unchanged")
        if d["engagement_rate"] is not None and abs(d["engagement_rate"]) >= 0.05:
            verdict_parts.append(
                f"engagement rate {'up' if d['engagement_rate'] > 0 else 'down'} "
                f"{abs(d['engagement_rate']):.2f} pts"
            )
        if d["posting_frequency_per_week"] is not None and abs(d["posting_frequency_per_week"]) >= 0.1:
            verdict_parts.append(
                f"posting {'up' if d['posting_frequency_per_week'] > 0 else 'down'} "
                f"{abs(d['posting_frequency_per_week']):.1f} posts/week"
            )
    verdict = (" — ".join(verdict_parts) if verdict_parts
               else "No measurable change against the stored baseline yet.")

    return {
        "username": uname,
        "enough_history": True,
        "scan_count": len(rows),
        "first_scan": rows[0][0],
        "latest": latest,
        "baselines": baselines,
        "verdict": verdict,
    }


# ---------------------------------------------------------------------------
# Recent searches — the restore-history list for the UI
# ---------------------------------------------------------------------------

def recent_searches(limit: int = 30) -> List[dict]:
    """Most recent search per handle (newest first), from real recorded scans.

    Powers the UI's search-history restore: every stored search of a handle
    shows up once, with the metrics from its latest scan and how many times
    it has been searched. Real recorded data only — never invented."""
    try:
        with _connect() as conn:
            rows = conn.execute(
                "SELECT s.username, s.scanned_at, s.followers, s.engagement_rate, "
                "       s.avg_likes, s.posts_count, s.avg_comments, "
                "       (SELECT COUNT(*) FROM scans s2 "
                "         WHERE s2.username = s.username) AS scan_count "
                "FROM scans s "
                "JOIN (SELECT username, MAX(scanned_at) AS m FROM scans "
                "       GROUP BY username) last "
                "  ON last.username = s.username AND last.m = s.scanned_at "
                "ORDER BY s.scanned_at DESC LIMIT ?",
                (max(1, min(limit, 100)),),
            ).fetchall()
    except Exception:
        return []
    return [
        {
            "username": r[0],
            "last_scanned_at": r[1],
            "followers": r[2],
            "engagement_rate": r[3],
            "avg_likes": r[4],
            "posts_count": r[5],
            "avg_comments": r[6],
            "scan_count": r[7],
        }
        for r in rows
    ]


def clear_history(username: str) -> int:
    """Delete every stored scan for one handle. Returns rows removed.
    Best-effort: a failed delete never raises."""
    uname = (username or "").strip().lstrip("@").lower()
    if not uname:
        return 0
    try:
        with _connect() as conn:
            cur = conn.execute("DELETE FROM scans WHERE username = ?", (uname,))
            return cur.rowcount or 0
    except Exception:
        return 0
