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

    All data in this system is real (fetched from Instagram); the
    `data_age_hours == -1` simulated badge is a legacy no-op guard kept so a
    stray badged row can never enter growth tracking."""
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

def get_daily_snapshots(username: str) -> List[dict]:
    """Collapse one handle's raw scan rows into one snapshot per day.

    Repeated analyses of the same handle within a single day (re-analyze
    clicks, auto-discovery refetches, chat grounding) record one raw row
    each. Growth is a day-scale story, so all of a day's scans collapse to
    ONE snapshot: the LAST scan of the day (most complete: Instagram's own
    counts at that moment, plus posts_count/avg_comments may only exist on
    later rows). Raw rows stay untouched — every real measurement survives;
    this is a read-time view for the tracker.
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
        return []

    buckets: dict = {}
    for ts, f, er, al, cad, pc, ac in rows:
        try:
            t = datetime.fromisoformat(ts)
        except (TypeError, ValueError):
            continue  # unparseable timestamp — never let one bad row kill the view
        if t.tzinfo is None:
            t = t.replace(tzinfo=timezone.utc)
        day = t.date().isoformat()
        bucket = buckets.get(day)
        if bucket is None:
            buckets[day] = {
                "day": day,
                "scanned_at": ts,
                "scan_count": 1,
                "followers": f, "engagement_rate": er, "avg_likes": al,
                "posting_frequency_per_week": cad,
                "posts_count": pc, "avg_comments": ac,
            }
        else:
            bucket["scan_count"] += 1
            # Last scan of the day wins (rows arrive in ascending time order).
            bucket["scanned_at"] = ts
            bucket["followers"] = f
            bucket["engagement_rate"] = er
            bucket["avg_likes"] = al
            bucket["posting_frequency_per_week"] = cad
            # Optional columns may be NULL on early rows: keep the first
            # non-NULL value ever seen for the day.
            if bucket["posts_count"] is None:
                bucket["posts_count"] = pc
            if bucket["avg_comments"] is None:
                bucket["avg_comments"] = ac
    return [buckets[d] for d in sorted(buckets)]


def get_growth_comparison(username: str) -> dict:
    """Growth comparison for one handle, computed over DAILY SNAPSHOTS.

    Baselines are snapshot-to-snapshot: yesterday's snapshot (the previous
    DAY — a same-day burst of re-analyses can never make every delta read
    ±0), plus the nearest snapshots to 7 and 30 days back. When a baseline
    is missing the response says so honestly instead of inventing data.
    """
    snaps = get_daily_snapshots(username)

    if len(snaps) < 2:
        only = snaps[0] if snaps else None
        return {
            "username": (username or "").strip().lstrip("@").lower(),
            "enough_history": False,
            "scan_count": only["scan_count"] if only else 0,
            "snapshot_count": len(snaps),
            "first_scan": only["scanned_at"] if only else None,
            "latest": only,
            "baselines": [],
            "series": [],
            "verdict": (
                "Growth tracking compares one day against another. Every scan "
                "of this handle so far is from a single day, so there is "
                "nothing to compare yet — analyze it again tomorrow (or any "
                "later day) and this section will show exactly what changed "
                "against today's stored numbers."
            ),
        }

    latest = snaps[-1]

    def _parse(ts: str):
        try:
            t = datetime.fromisoformat(ts)
        except (TypeError, ValueError):
            return None
        return t if t.tzinfo else t.replace(tzinfo=timezone.utc)

    def _nearest(days: int, tol_days: float = 2.0):
        # Nearest snapshot aged within [days-tol, days+tol] — for "1 week
        # ago" a 9-day-old snapshot beats a 5-day-old one, so only the
        # older side of the window is considered.
        best, best_gap = None, None
        for s in snaps[:-1]:
            t = _parse(s["scanned_at"])
            if t is None:
                continue
            age = (datetime.now(timezone.utc) - t).total_seconds() / 86400
            if age < days - tol_days or age > days + tol_days:
                continue
            gap = abs(age - days)
            if best_gap is None or gap < best_gap:
                best, best_gap = s, gap
        return best

    def _delta(cur, base, key):
        try:
            a, b = cur.get(key), base.get(key)
            if a is None or b is None:
                return None
            return round(a - b, 4)
        except (TypeError, KeyError):
            return None  # posts_count/avg_comments may be NULL on old rows

    baselines = []
    for label, base, days in (
        ("previous day", _nearest(1), 1),
        ("1 week ago", _nearest(7), 7),
        ("1 month ago", _nearest(30), 30),
    ):
        if base is None:
            baselines.append({
                "label": label, "available": False, "days_back": days,
                "note": "no stored scan near this date",
            })
            continue
        d = {k: _delta(latest, base, k) for k in (
            "followers", "engagement_rate", "avg_likes", "avg_comments",
            "posts_count", "posting_frequency_per_week",
        )}
        bt, lt = _parse(base["scanned_at"]), _parse(latest["scanned_at"])
        span_days = round((lt - bt).total_seconds() / 86400, 1) if bt and lt else None
        baselines.append({
            "label": label,
            "available": True,
            "days_back": days,
            "scanned_at": base["scanned_at"],
            "span_days": span_days,
            "values": {k: base.get(k) for k in (
                "followers", "engagement_rate", "avg_likes", "avg_comments",
                "posts_count", "posting_frequency_per_week",
            )},
            "deltas": d,
            "followers_delta_pct": (
                round(d["followers"] / base["followers"] * 100, 3)
                if d["followers"] is not None and base.get("followers") else None
            ),
        })

    # Plain-language verdict from the strongest available baseline (month > week > yesterday).
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
        "username": (username or "").strip().lstrip("@").lower(),
        "enough_history": True,
        "scan_count": sum(s["scan_count"] for s in snaps),
        "snapshot_count": len(snaps),
        "first_scan": snaps[0]["scanned_at"],
        "latest": latest,
        "baselines": baselines,
        "series": get_growth_series(username),
        "verdict": verdict,
    }


def get_growth_series(username: str, max_points: int = 30) -> List[dict]:
    """Day-scale trend series (oldest → newest) for the growth chart: one
    point per daily snapshot with its day-over-day follower delta."""
    snaps = get_daily_snapshots(username)[-max_points:]
    series: List[dict] = []
    for i, s in enumerate(snaps):
        prev = snaps[i - 1] if i > 0 else None
        df = None
        if prev is not None and s.get("followers") is not None and prev.get("followers") is not None:
            df = s["followers"] - prev["followers"]
        series.append({
            "day": s["day"],
            "scanned_at": s["scanned_at"],
            "followers": s["followers"],
            "engagement_rate": s["engagement_rate"],
            "avg_likes": s["avg_likes"],
            "avg_comments": s["avg_comments"],
            "posting_frequency_per_week": s["posting_frequency_per_week"],
            "followers_delta": df,
            "scan_count": s["scan_count"],
        })
    return series


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
