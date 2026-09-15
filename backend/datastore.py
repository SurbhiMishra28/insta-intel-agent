"""
Profile data store (SQLite, zero-config).

Every Instagram handle the agent searches is persisted here with its FULL
fetched data: profile snapshot, all recent posts (captions, likes, comments,
views, timestamps, hashtags, media type) and a fetch log. This is the
browsable "data which we are going to use" — the raw material behind every
metric the AI reasons over. Written from the scraper's cache path, so the
store always mirrors the real data that produced the analysis.
"""
import json
import os
import sqlite3
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

DB_PATH = os.getenv("PROFILE_DATASTORE_DB", os.path.join(os.path.dirname(__file__), "profile_datastore.db"))

_SCHEMA = """
CREATE TABLE IF NOT EXISTS profiles (
    username TEXT PRIMARY KEY,
    full_name TEXT,
    bio TEXT,
    category TEXT,
    followers INTEGER,
    following INTEGER,
    posts_count INTEGER,
    is_verified INTEGER,
    is_business INTEGER,
    data_age_hours REAL,
    posts_json TEXT,
    posts_count_stored INTEGER,
    fetched_at TEXT NOT NULL,
    fetch_count INTEGER DEFAULT 1
);
CREATE TABLE IF NOT EXISTS fetch_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT NOT NULL,
    fetched_at TEXT NOT NULL,
    source TEXT,
    followers INTEGER,
    posts_stored INTEGER
);
CREATE INDEX IF NOT EXISTS idx_fetch_log_username ON fetch_log(username, fetched_at);
"""


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.executescript(_SCHEMA)
    return conn


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def upsert_profile(profile) -> None:
    """Store/re- store one handle's full snapshot (best-effort, never breaks the API)."""
    try:
        posts = []
        for p in getattr(profile, "recent_posts", None) or []:
            posts.append({
                "id": p.id,
                "caption": p.caption,
                "likes": p.likes,
                "comments": p.comments,
                "views": p.views,
                "media_type": p.media_type,
                "posted_days_ago": p.posted_days_ago,
                "posted_at": p.posted_at,
                "hashtags": p.hashtags,
            })
        with _connect() as conn:
            conn.execute(
                """
                INSERT INTO profiles (
                    username, full_name, bio, category, followers, following,
                    posts_count, is_verified, is_business, data_age_hours,
                    posts_json, posts_count_stored, fetched_at, fetch_count
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
                ON CONFLICT(username) DO UPDATE SET
                    full_name=excluded.full_name,
                    bio=excluded.bio,
                    category=excluded.category,
                    followers=excluded.followers,
                    following=excluded.following,
                    posts_count=excluded.posts_count,
                    is_verified=excluded.is_verified,
                    is_business=excluded.is_business,
                    data_age_hours=excluded.data_age_hours,
                    posts_json=excluded.posts_json,
                    posts_count_stored=excluded.posts_count_stored,
                    fetched_at=excluded.fetched_at,
                    fetch_count=profiles.fetch_count + 1
                """,
                (
                    profile.username.lower(),
                    profile.full_name,
                    profile.bio,
                    profile.category,
                    profile.followers,
                    profile.following,
                    profile.posts_count,
                    int(bool(profile.is_verified)),
                    int(bool(profile.is_business)),
                    profile.data_age_hours,
                    json.dumps(posts, ensure_ascii=False),
                    len(posts),
                    _now(),
                ),
            )
            conn.execute(
                "INSERT INTO fetch_log (username, fetched_at, source, followers, posts_stored) VALUES (?, ?, ?, ?, ?)",
                (
                    profile.username.lower(),
                    _now(),
                    "live" if (profile.data_age_hours is None) else "cache",
                    profile.followers,
                    len(posts),
                ),
            )
    except Exception:
        pass  # the store must never break an analysis


def list_profiles(limit: int = 100) -> List[Dict[str, Any]]:
    """All stored handles, newest first (summary fields only)."""
    try:
        with _connect() as conn:
            rows = conn.execute(
                """
                SELECT username, full_name, category, followers, posts_count,
                       posts_count_stored, is_verified, fetched_at, fetch_count
                FROM profiles ORDER BY fetched_at DESC LIMIT ?
                """,
                (max(1, min(limit, 500)),),
            ).fetchall()
    except Exception:
        return []
    out = []
    for r in rows:
        out.append({
            "username": r[0], "full_name": r[1], "category": r[2],
            "followers": r[3], "posts_count": r[4], "posts_stored": r[5],
            "is_verified": bool(r[6]), "fetched_at": r[7], "fetch_count": r[8],
        })
    return out


def get_profile(username: str) -> Optional[Dict[str, Any]]:
    """One handle's full stored data: profile fields + every stored post."""
    try:
        with _connect() as conn:
            row = conn.execute(
                """
                SELECT username, full_name, bio, category, followers, following,
                       posts_count, is_verified, is_business, data_age_hours,
                       posts_json, posts_count_stored, fetched_at, fetch_count
                FROM profiles WHERE username = ?
                """,
                (username.strip().lower(),),
            ).fetchone()
    except Exception:
        return None
    if not row:
        return None
    try:
        posts = json.loads(row[10])
    except Exception:
        posts = []
    return {
        "username": row[0], "full_name": row[1], "bio": row[2], "category": row[3],
        "followers": row[4], "following": row[5], "posts_count": row[6],
        "is_verified": bool(row[7]), "is_business": bool(row[8]),
        "data_age_hours": row[9], "posts": posts, "posts_stored": row[11],
        "fetched_at": row[12], "fetch_count": row[13],
    }


def fetch_log(username: Optional[str] = None, limit: int = 50) -> List[Dict[str, Any]]:
    """Recent fetch events (all handles or one)."""
    try:
        with _connect() as conn:
            if username:
                rows = conn.execute(
                    "SELECT username, fetched_at, source, followers, posts_stored "
                    "FROM fetch_log WHERE username = ? ORDER BY id DESC LIMIT ?",
                    (username.strip().lower(), max(1, min(limit, 200))),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT username, fetched_at, source, followers, posts_stored "
                    "FROM fetch_log ORDER BY id DESC LIMIT ?",
                    (max(1, min(limit, 200)),),
                ).fetchall()
    except Exception:
        return []
    return [
        {"username": r[0], "fetched_at": r[1], "source": r[2], "followers": r[3], "posts_stored": r[4]}
        for r in rows
    ]


def stats() -> Dict[str, Any]:
    """Store totals for the UI header."""
    try:
        with _connect() as conn:
            handles, posts, fetches = conn.execute(
                """
                SELECT
                  (SELECT COUNT(*) FROM profiles),
                  (SELECT COALESCE(SUM(posts_count_stored), 0) FROM profiles),
                  (SELECT COUNT(*) FROM fetch_log)
                """
            ).fetchone()
    except Exception:
        handles = posts = fetches = 0
    return {"handles": handles, "posts": posts, "fetches": fetches}
