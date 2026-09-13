"""
Data acquisition layer for Instagram profile data.

Instagram's official Graph API only exposes data for accounts you own/manage
via a connected Facebook Business Page — it does NOT let you pull arbitrary
public competitor profiles. To analyze *any* public handle, this module uses
Apify's Instagram Scraper actor (https://apify.com/apify/instagram-scraper)
as the live data source.

Modes, selected by the DATA_MODE environment variable:

  - "live" (default): real profile data via the Apify actor. Requires
    APIFY_TOKEN in the environment (Apify console → Settings → API &
    Integrations). Optionally falls back to demo data if FALLBACK_TO_DEMO=true.

  - "demo": deterministic seeded fake data (no external dependency). Kept
    for offline development and UI work.

Both modes return the same ProfileData shape, so the AI engine and API
never care which one ran.
"""
import asyncio
import hashlib
import json
import os
import random
import re
import sqlite3
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from urllib.parse import quote

from dotenv import load_dotenv
import httpx

# Load backend/.env (if present) BEFORE reading env vars below, so this
# module works regardless of import order.
load_dotenv(os.path.join(os.path.dirname(__file__), '.env'))

from models import Post, ProfileData

DATA_MODE = os.getenv("DATA_MODE", "live")
APIFY_TOKEN = os.getenv("APIFY_TOKEN", "").strip()
# Optional second Apify account: when the primary account's monthly credit
# is exhausted, fetches automatically rotate to this one (and back next cycle).
APIFY_TOKEN_2 = os.getenv("APIFY_TOKEN_2", "").strip()
APIFY_ACTOR_ID = os.getenv("APIFY_ACTOR_ID", "apify/instagram-scraper")
APIFY_SEARCH_ACTOR_ID = os.getenv("APIFY_SEARCH_ACTOR_ID", "apify/instagram-search-scraper")
APIFY_HASHTAG_ACTOR_ID = os.getenv("APIFY_HASHTAG_ACTOR_ID", "apify/instagram-hashtag-analytics-scraper")
APIFY_RUN_TIMEOUT = int(os.getenv("APIFY_RUN_TIMEOUT", "300"))  # seconds
# Default false: a failed live fetch must fail loudly (503/502) rather than
# silently serve fake data. Opt in to demo fallback explicitly.
FALLBACK_TO_DEMO = os.getenv("FALLBACK_TO_DEMO", "false").lower() in ("1", "true", "yes")

# --- Meta Instagram Graph API (free, official) provider ---
# Set DATA_PROVIDER=graph to fetch data via graph.facebook.com instead of
# paid Apify actors. See GRAPH_API_SETUP.md for the free token setup.
DATA_PROVIDER = os.getenv("DATA_PROVIDER", "apify").lower()  # "apify" | "graph"
# Paid hashtag-volume research costs one actor run per growth-plan call.
# Default off to conserve free-plan credits; set APIFY_HASHTAG_RESEARCH=true to enable.
APIFY_HASHTAG_RESEARCH = os.getenv("APIFY_HASHTAG_RESEARCH", "false").lower() in ("1", "true", "yes")
GRAPH_ACCESS_TOKEN = os.getenv("GRAPH_ACCESS_TOKEN", "").strip()
GRAPH_IG_USER_ID = os.getenv("GRAPH_IG_USER_ID", "").strip()
GRAPH_API_VERSION = os.getenv("GRAPH_API_VERSION", "v21.0")
GRAPH_BASE = f"https://graph.facebook.com/{GRAPH_API_VERSION}"

# --- RapidAPI provider (free tier: 30 calls/month on 'Instagram Cheapest') ---
# Set DATA_PROVIDER=rapidapi. Real-time public-data API; no login needed.
RAPIDAPI_KEY = os.getenv("RAPIDAPI_KEY", "").strip()
RAPIDAPI_HOST = os.getenv("RAPIDAPI_HOST", "instagram-cheapest.p.rapidapi.com").strip()

# Simple in-process TTL cache so repeated handles (e.g. compare mode re-fetching
# the main account) don't re-trigger a paid actor run within the TTL window.
_CACHE_TTL = int(os.getenv("PROFILE_CACHE_TTL", "1800"))  # seconds
_profile_cache: Dict[str, tuple] = {}  # username -> (monotonic_ts, ProfileData)
_related_cache: Dict[str, tuple] = {}  # username -> (monotonic_ts, List[dict])
_CACHE_LOCK = asyncio.Lock()

# In-flight request coalescing: concurrent get_profile calls for the same
# handle share ONE actor run instead of stacking duplicate 20-60s fetches.
_inflight: Dict[str, asyncio.Task] = {}

# ---------------------------------------------------------------------------
# Persistent disk cache (SQLite) — survives backend restarts, so a handle
# fetched once is served instantly forever after (until its TTL expires).
# ---------------------------------------------------------------------------

_CACHE_DB = os.getenv("PROFILE_CACHE_DB", os.path.join(os.path.dirname(__file__), "profile_cache.db"))

_DISK_TTL_PROFILE = int(os.getenv("PROFILE_DISK_TTL", str(6 * 3600)))        # fresh enough for metrics
_DISK_TTL_DISCOVERY = int(os.getenv("DISCOVERY_DISK_TTL", str(24 * 3600)))  # competitor lists drift slowly
_DISK_TTL_HASHTAG = int(os.getenv("HASHTAG_DISK_TTL", str(7 * 86400)))      # hashtag volumes move slowly


def _cache_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(_CACHE_DB)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS cache ("
        " key TEXT PRIMARY KEY, value TEXT NOT NULL, cached_at REAL NOT NULL)"
    )
    return conn


def _cache_get(key: str, ttl: int) -> Optional[Any]:
    try:
        with _cache_conn() as conn:
            row = conn.execute(
                "SELECT value, cached_at FROM cache WHERE key = ?", (key,)
            ).fetchone()
    except Exception:
        return None
    if not row or (time.time() - row[1]) > ttl:
        return None
    try:
        return json.loads(row[0])
    except Exception:
        return None


# --- Local RapidAPI quota tracking (the provider has no usage API) ---
# Counts every billable call this month so the app knows whether a 429 is a
# monthly exhaustion (wait for reset / fail over) or a 1 req/sec blip (retry).
_RAPID_MONTHLY_LIMIT = int(os.getenv("RAPIDAPI_MONTHLY_LIMIT", "30"))


def _rapid_usage_increment() -> None:
    """Count one billable RapidAPI call for local quota tracking."""
    try:
        month = time.strftime("%Y-%m")
        with _cache_conn() as conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS rapidapi_usage ("
                " month TEXT PRIMARY KEY, count INTEGER NOT NULL, first_day INTEGER)"
            )
            conn.execute(
                "INSERT INTO rapidapi_usage (month, count, first_day) VALUES (?, 1, ?) "
                "ON CONFLICT(month) DO UPDATE SET count = count + 1",
                (month, time.strftime("%d")),
            )
    except Exception:
        pass  # tracking must never break the data path


def _rapid_usage_status() -> Dict[str, Any]:
    """Local view of RapidAPI consumption this month + reset estimate
    (billing day = the day of this month's first tracked call)."""
    month = time.strftime("%Y-%m")
    used, first_day = 0, None
    try:
        with _cache_conn() as conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS rapidapi_usage ("
                " month TEXT PRIMARY KEY, count INTEGER NOT NULL, first_day INTEGER)"
            )
            row = conn.execute(
                "SELECT count, first_day FROM rapidapi_usage WHERE month = ?", (month,)
            ).fetchone()
        if row:
            used, first_day = int(row[0]), int(row[1])
    except Exception:
        pass

    reset_estimate = None
    if first_day and 1 <= first_day <= 31:
        now = time.gmtime()
        year, mon = now.tm_year, now.tm_mon + 1
        if mon > 12:
            year, mon = year + 1, 1
        reset_estimate = f"{year}-{mon:02d}-{int(first_day):02d}"
    return {
        "used": used,
        "limit": _RAPID_MONTHLY_LIMIT,
        "reset_estimate": reset_estimate,
        "exhausted": used >= _RAPID_MONTHLY_LIMIT,
    }


def _cache_set(key: str, value: Any) -> None:
    try:
        with _cache_conn() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO cache (key, value, cached_at) VALUES (?, ?, ?)",
                (key, json.dumps(value), time.time()),
            )
    except Exception:
        pass  # cache must never break the data path


def _profile_to_json(p: ProfileData) -> dict:
    d = p.model_dump()
    for post in d["recent_posts"]:
        for k in ("likes", "comments", "views", "posted_days_ago"):
            post[k] = int(post.get(k) or 0)
        post["caption"] = str(post.get("caption") or "")
        post["hashtags"] = list(post.get("hashtags") or [])
    return d


def _profile_from_json(d: dict) -> ProfileData:
    d["recent_posts"] = d.get("recent_posts") or []
    return ProfileData(**d)


def _disk_profile_get(username: str) -> Optional[ProfileData]:
    raw = _cache_get(f"profile:{username}", _DISK_TTL_PROFILE)
    try:
        return _profile_from_json(raw) if isinstance(raw, dict) else None
    except Exception:
        return None


def _disk_profile_get_any(username: str) -> Optional[ProfileData]:
    """Last-resort lookup: return the cached profile regardless of TTL (up to
    30 days) together with its age. Data is real, just possibly stale —
    served only when every live provider is unavailable."""
    try:
        with _cache_conn() as conn:
            row = conn.execute(
                "SELECT value, cached_at FROM cache WHERE key = ?",
                (f"profile:{username}",),
            ).fetchone()
    except Exception:
        return None
    if not row:
        return None
    age_h = (time.time() - row[1]) / 3600
    if age_h > 24 * 30:
        return None
    try:
        p = _profile_from_json(json.loads(row[0]))
    except Exception:
        return None
    if p is not None:
        p.data_age_hours = round(age_h, 1)
    return p


def _disk_profile_set(username: str, p: ProfileData) -> None:
    _cache_set(f"profile:{username}", _profile_to_json(p))

# ---------------------------------------------------------------------------
# Username / URL normalization
# ---------------------------------------------------------------------------

_USERNAME_RE = re.compile(r"^[A-Za-z0-9._]{1,30}$")


def normalize_username(raw: str) -> str:
    """Accepts a bare handle, an @handle, or any Instagram profile URL
    (instagram.com/username, with or without trailing slash / query string)
    and returns the canonical lowercase handle."""
    text = (raw or "").strip()
    if not text:
        raise ValueError("Username is required.")

    lower = text.lower()
    if "instagram.com" in lower or "instagr.am" in lower:
        m = re.search(r"(?:instagram\.com|instagr\.am)/([^/?&#]+)", text, re.IGNORECASE)
        if not m:
            raise ValueError("Could not find a username in that Instagram URL.")
        username = m.group(1)
    elif "ig.me" in lower:
        # Instagram's official short link for messages/profiles.
        m = re.search(r"ig\.me/(?:m/)?([^/?&#]+)", text, re.IGNORECASE)
        if not m:
            raise ValueError("Could not find a username in that Instagram URL.")
        username = m.group(1)
    else:
        username = text

    username = username.strip().strip("/").lstrip("@")
    username = username.split("?")[0].split("#")[0]

    # Reject known non-profile paths so "instagram.com/explore" fails loudly.
    if username.lower() in {"p", "reel", "reels", "explore", "stories", "tv", "about", "accounts"}:
        raise ValueError(f"'{username}' is not an Instagram profile URL.")

    if not _USERNAME_RE.match(username):
        raise ValueError(
            f"'{username}' doesn't look like a valid Instagram handle "
            "(letters, numbers, dots and underscores only)."
        )
    return username.lower()


# ---------------------------------------------------------------------------
# Apify live fetch
# ---------------------------------------------------------------------------

# Apify Instagram actor field names vary slightly by resultsType / version;
# normalize the common variants here.
_PROFILE_FIELDS = {
    "username": ("username", "handle", "ownerUsername"),
    "full_name": ("fullName", "full_name", "name"),
    "biography": ("biography", "bio", "description"),
    "followers": ("followersCount", "followerCount", "edge_followed_by.count", "followers"),
    "following": ("followsCount", "followingCount", "edge_follow.count", "following", "follows"),
    "posts_count": ("postsCount", "mediaCount", "edge_owner_to_timeline_media.count", "postsCountNumber"),
    "is_verified": ("verified", "isVerified"),
    "is_business": ("businessAccount", "isBusinessAccount"),
    "category": ("category", "businessCategoryName", "categoryName"),
}

_MEDIA_TYPE_MAP = {
    "Image": "image", "GraphImage": "image", "image": "image", "IMAGE": "image",
    "Video": "video", "GraphVideo": "video", "video": "video", "VIDEO": "video",
    "Sidecar": "carousel", "GraphSidecar": "carousel", "Carousel": "carousel",
    "carousel": "carousel", "XDTMediaCarousel": "carousel", "album": "carousel",
    "Clip": "reel", "Reel": "reel", "reel": "reel", "REEL": "reel",
    "Clips": "reel", "GraphStoryVideo": "reel",
}


def _dig(item: Any, dotted_key: str) -> Any:
    """Walks nested dicts/lists, e.g. 'edge_followed_by.count' or
    'edge_owner_to_timeline_media.edges.0.node'."""
    cur = item
    for part in dotted_key.split("."):
        if isinstance(cur, list) and part.isdigit():
            idx = int(part)
            cur = cur[idx] if idx < len(cur) else None
        elif isinstance(cur, dict):
            cur = cur.get(part)
        else:
            return None
        if cur is None:
            return None
    return cur


def _pick(item: Dict[str, Any], keys) -> Any:
    for k in keys:
        v = _dig(item, k)
        if v is not None:
            return v
    return None


def _clean_str(v: Any) -> Optional[str]:
    """Normalize actor string fields. The Instagram actor serializes JSON
    nulls as the literal string 'None'/'null' (seen in businessCategoryName
    and similar fields), so those and blanks count as missing."""
    if v is None:
        return None
    s = str(v).strip()
    if not s or s.lower() in ("none", "null", "nil"):
        return None
    return s


def _as_bool(v: Any) -> bool:
    if isinstance(v, bool):
        return v
    if isinstance(v, str):
        return v.lower() in ("1", "true", "yes")
    return bool(v) if v is not None else False


def _to_int(v: Any) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return 0


def _posted_days_ago(item: Dict[str, Any]) -> int:
    ts_raw = _pick(item, ("timestamp", "takenAtTimestamp", "taken_at_timestamp"))
    if ts_raw is None:
        return 0
    try:
        if isinstance(ts_raw, (int, float)) or (isinstance(ts_raw, str) and ts_raw.replace(".", "").isdigit()):
            ts = float(ts_raw)
            if ts > 1e12:  # epoch milliseconds
                ts /= 1000.0
        else:
            dt = datetime.fromisoformat(str(ts_raw).replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            ts = dt.timestamp()
        return max(0, int((time.time() - ts) / 86400))
    except (TypeError, ValueError, OverflowError):
        return 0


def _map_post(p: Dict[str, Any], idx: int, owner: str) -> Optional[Post]:
    """Normalize one post/reel item from any of the actor's result shapes."""
    if not isinstance(p, dict):
        return None

    likes = _to_int(_pick(p, ("likesCount", "likeCount", "edge_media_preview_like.count", "edge_liked_by.count")))
    comments = _to_int(_pick(p, ("commentsCount", "commentCount", "edge_media_to_comment.count")))
    views = _to_int(_pick(p, ("videoViewCount", "playCount", "videoPlayCount")))

    # Exact ISO timestamp (kept for best-time analytics; falls back to None).
    posted_at_iso: Optional[str] = None
    ts_raw = _pick(p, ("timestamp", "takenAtTimestamp", "taken_at_timestamp"))
    if ts_raw is not None:
        try:
            if isinstance(ts_raw, (int, float)) or (isinstance(ts_raw, str) and ts_raw.replace(".", "").isdigit()):
                ts = float(ts_raw)
                if ts > 1e12:
                    ts /= 1000.0
                posted_at_iso = datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()
            else:
                dt = datetime.fromisoformat(str(ts_raw).replace("Z", "+00:00"))
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                posted_at_iso = dt.isoformat()
        except (TypeError, ValueError, OverflowError):
            posted_at_iso = None
    if likes == 0 and comments == 0 and views == 0:
        # Shell/placeholder items sometimes appear in results; skip them.
        return None

    caption = _pick(p, ("caption", "edge_media_to_caption.edges.0.node.text")) or ""
    if isinstance(caption, list):
        caption = " ".join(
            (e.get("node", {}).get("text", "") if isinstance(e, dict) else str(e))
            for e in caption
        )
    caption = _clean_str(caption) or ""

    raw_type = str(_pick(p, ("type", "media_type", "productType", "__typename")) or "image")
    media_type = _MEDIA_TYPE_MAP.get(raw_type, "image")
    if media_type == "video" and "clip" in raw_type.lower():
        media_type = "reel"

    hashtags = sorted({f"#{h.lower()}" for h in re.findall(r"#(\w+)", caption)})[:10]

    return Post(
        id=str(_pick(p, ("id", "shortCode", "shortcode", "url"))) or f"{owner}_{idx}",
        caption=caption[:600],
        likes=likes,
        comments=comments,
        posted_days_ago=_posted_days_ago(p),
        hashtags=hashtags,
        media_type=media_type,
        views=views,
        posted_at=posted_at_iso,
    )


def _map_profile(items: List[Dict[str, Any]], requested: str) -> ProfileData:
    """Turn the actor's dataset items into a ProfileData.

    Handles resultsType=profiles (one profile item, possibly with embedded
    latestPosts), resultsType=posts (post items, no profile fields), and
    mixed output where both appear.
    """
    profile_item: Optional[Dict[str, Any]] = None
    post_items: List[Dict[str, Any]] = []

    for it in items:
        if not isinstance(it, dict):
            continue
        if profile_item is None and (
            it.get("followersCount") is not None or it.get("biography") is not None
        ):
            profile_item = it
        elif it.get("likesCount") is not None or it.get("shortCode") is not None:
            post_items.append(it)

    if profile_item is not None:
        for key in ("latestPosts", "posts", "edge_owner_to_timeline_media.edges"):
            embedded = profile_item.get(key)
            if isinstance(embedded, list) and embedded:
                unwrapped = [
                    e.get("node") if isinstance(e, dict) and isinstance(e.get("node"), dict) else e
                    for e in embedded
                ]
                post_items = [p for p in unwrapped if isinstance(p, dict)] or post_items
                break

    if profile_item is None and not post_items:
        raise ValueError(
            f"Instagram profile '@{requested}' not found or returned no data. "
            "Check the spelling of the handle."
        )

    if profile_item is not None:
        pi = profile_item
        username = str(_pick(pi, _PROFILE_FIELDS["username"]) or requested)
        recent_posts = [p for p in (_map_post(x, i, username) for i, x in enumerate(post_items)) if p]
        return ProfileData(
            username=username.lower(),
            full_name=_clean_str(_pick(pi, _PROFILE_FIELDS["full_name"]))
            or username.replace("_", " ").replace(".", " ").title(),
            bio=_clean_str(_pick(pi, _PROFILE_FIELDS["biography"])) or "",
            followers=_to_int(_pick(pi, _PROFILE_FIELDS["followers"])),
            following=_to_int(_pick(pi, _PROFILE_FIELDS["following"])),
            posts_count=_to_int(_pick(pi, _PROFILE_FIELDS["posts_count"])),
            is_verified=_as_bool(_pick(pi, _PROFILE_FIELDS["is_verified"])),
            is_business=_as_bool(_pick(pi, _PROFILE_FIELDS["is_business"])),
            category=_clean_str(_pick(pi, _PROFILE_FIELDS["category"])),
            recent_posts=recent_posts,
        )

    # Posts-only output (no profile fields): derive what we can from posts.
    recent_posts = [p for p in (_map_post(x, i, requested) for i, x in enumerate(post_items)) if p]
    if not recent_posts:
        raise ValueError(f"No usable posts found for '@{requested}'.")
    return ProfileData(
        username=requested.lower(),
        full_name=requested.replace("_", " ").replace(".", " ").title(),
        bio="",
        followers=0,
        following=0,
        posts_count=len(recent_posts),
        is_verified=False,
        is_business=False,
        category=None,
        recent_posts=recent_posts,
    )


def _actor_url(results_type: str, results_limit: int) -> str:
    # "user/name" → "user~name"; numeric actor ids pass through untouched.
    actor_path = APIFY_ACTOR_ID.replace("/", "~") if "~" not in APIFY_ACTOR_ID else APIFY_ACTOR_ID
    return (
        f"https://api.apify.com/v2/acts/{quote(actor_path, safe='~')}"
        f"/run-sync-get-dataset-items?token={quote(APIFY_TOKEN, safe='')}"
    )


_apify_preflight_ok_until: Dict[str, float] = {}  # token -> ts until which credit was confirmed
PREFLIGHT_RECHECK_SECS = 300  # re-probe free limits API at most every 5 min / token


def _apify_preflight_check(token: str) -> None:
    """Free, unmetered quota gate BEFORE starting a paid actor run.

    GET /users/me/limits is an account-management endpoint — it works even
    when platform credit is spent. Checking it here means a fresh-handle
    request fails in ~1s with a precise message instead of hanging ~15s on
    a doomed actor run. Raises RuntimeError when the account's monthly
    credit is exhausted; never blocks a run when the check itself fails.
    """
    now = time.time()
    if now < _apify_preflight_ok_until.get(token, 0):
        return  # credit confirmed recently; proceed straight to the run
    try:
        r = httpx.get(
            "https://api.apify.com/v2/users/me/limits",
            headers={"Authorization": f"Bearer {token}"},
            timeout=8,
        )
        d = r.json().get("data", {})
        used = float((d.get("current") or {}).get("monthlyUsageUsd") or 0)
        cap = float((d.get("limits") or {}).get("maxMonthlyUsageUsd") or 0)
        if cap > 0 and used >= cap:
            end = str((d.get("monthlyUsageCycle") or {}).get("endAt") or "")
            reset = end[:10] or "the next billing cycle"
            raise RuntimeError(
                f"Apify monthly free credit exhausted (${used:.2f} of ${cap:.2f} used). "
                f"Actor runs resume automatically when the cycle resets on {reset} — "
                "or raise the limit now: Apify Console → Settings → Usage & Billing."
            )
        _apify_preflight_ok_until[token] = now + PREFLIGHT_RECHECK_SECS
    except RuntimeError:
        raise
    except Exception:
        pass  # preflight must never block a legitimate run


def _usage_limit_message() -> str:
    """Best-effort dynamic message for the usage-limit block: pull the real
    spend, cap and reset date from Apify's limits API so the error says
    exactly when runs resume. Falls back to a static hint if the API call
    fails (this path must never mask the original error)."""
    try:
        r = httpx.get(
            "https://api.apify.com/v2/users/me/limits",
            headers={"Authorization": f"Bearer {APIFY_TOKEN}"},
            timeout=10,
        )
        d = r.json().get("data", {})
        used = float((d.get("current") or {}).get("monthlyUsageUsd") or 0)
        cap = float((d.get("limits") or {}).get("maxMonthlyUsageUsd") or 0)
        end = str((d.get("monthlyUsageCycle") or {}).get("endAt") or "")
        reset = end[:10] or "the next billing cycle"
        return (
            f"Apify monthly free credit exhausted (${used:.2f} of ${cap:.2f} used). "
            f"Actor runs resume automatically when the cycle resets on {reset} — "
            "or raise the limit now: Apify Console → Settings → Usage & Billing."
        )
    except Exception:
        return (
            "Apify account blocked this actor run: monthly usage hard limit "
            "exceeded. Raise/remove the limit or wait for the billing reset "
            "(Apify console → Settings → Usage & Billing), then retry."
        )


def _require_items(resp: httpx.Response, actor_label: str) -> List[Dict[str, Any]]:
    """Shared status-code handling + JSON parsing for actor run endpoints."""
    if resp.status_code in (401, 403):
        detail = ""
        try:
            err = resp.json().get("error", {})
            detail = f"{err.get('type', '')}: {err.get('message', '')}".strip(": ")
        except Exception:
            detail = (resp.text or "")[:200]
        low = detail.lower()
        if "usage" in low or "limit" in low or "platform-feature-disabled" in low:
            raise RuntimeError(_usage_limit_message())
        raise RuntimeError(
            f"Apify rejected the request (401/403). {detail or 'Double-check APIFY_TOKEN.'}"
        )
    if resp.status_code == 402:
        raise RuntimeError(
            "Apify account needs a paid plan or has exhausted its free "
            "platform credits for this actor (402)."
        )
    if resp.status_code == 404:
        raise RuntimeError(
            f"Apify actor '{actor_label}' not found (404). Check the actor id env var."
        )
    if resp.status_code in (408, 504):
        raise RuntimeError("Apify actor run timed out. Try again in a moment.")
    resp.raise_for_status()
    items = resp.json()
    if not isinstance(items, list):
        raise RuntimeError("Apify returned an unexpected response shape.")
    return items


# ---------------------------------------------------------------------------
# Meta Instagram Graph API provider (free, official) — DATA_PROVIDER=graph
# ---------------------------------------------------------------------------
_graph_user_ids: Dict[str, str] = {}  # username -> ig user id (per process)


def _graph_headers() -> Dict[str, str]:
    return {"Authorization": f"Bearer {GRAPH_ACCESS_TOKEN}"}


async def _graph_get(path: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """GET against graph.facebook.com with normalized error handling."""
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(f"{GRAPH_BASE}{path}", params=params or {}, headers=_graph_headers())
    try:
        data = resp.json()
    except Exception:
        data = {}
    if resp.status_code != 200 or data.get("error"):
        err = data.get("error") or {}
        msg = err.get("message") or f"HTTP {resp.status_code}"
        if err.get("code") == 190:
            raise RuntimeError(
                "Graph API rejected GRAPH_ACCESS_TOKEN (code 190). Generate a "
                "fresh long-lived token — see GRAPH_API_SETUP.md step 6."
            )
        if resp.status_code == 404 or err.get("code") == 803:
            raise ValueError(msg)
        raise RuntimeError(f"Graph API error: {msg}")
    return data


async def _graph_business_discovery(username: str, fields: str) -> Dict[str, Any]:
    """Business-discovery lookup for ANY professional account, resolved via
    the configured own account (GRAPH_IG_USER_ID)."""
    if not GRAPH_ACCESS_TOKEN or not GRAPH_IG_USER_ID:
        raise RuntimeError(
            "DATA_PROVIDER=graph but GRAPH_ACCESS_TOKEN or GRAPH_IG_USER_ID is "
            "not set — see backend/GRAPH_API_SETUP.md for the free setup."
        )
    data = await _graph_get(
        f"/{GRAPH_IG_USER_ID}",
        {"fields": f"business_discovery.username({{{username}}}){{{fields}}}"},
    )
    bd = data.get("business_discovery") or {}
    if not bd.get("id"):
        raise ValueError(f"Instagram profile '@{username}' not found via Graph API.")
    return bd


async def _graph_profile_for(username: str, posts_limit: int = 12) -> ProfileData:
    """Fetch one profile (+ latest posts) via the official Graph API.
    Business discovery only reaches professional (business/creator) accounts;
    personal accounts raise a clear ValueError."""
    uname = username.lower()
    fields = (
        "username,full_name,biography,followers_count,follows_count,"
        "media_count,profile_photo_url,website"
    )
    media_fields = (
        f"media.limit({posts_limit}){{caption,like_count,comments_count,"
        "timestamp,media_type,media_product_type,id}"
    )
    bd = await _graph_business_discovery(uname, f"{fields},{media_fields}")

    items: List[Dict[str, Any]] = []
    for it in (bd.get("media") or {}).get("data") or []:
        if not isinstance(it, dict):
            continue
        raw_type = str(it.get("media_type") or "IMAGE")
        media_type = {"VIDEO": "video", "CAROUSEL_ALBUM": "carousel"}.get(raw_type, "image")
        if str(it.get("media_product_type") or "").upper() == "REELS":
            media_type = "reel"
        items.append({
            "id": it.get("id"),
            "caption": it.get("caption") or "",
            "likesCount": _to_int(it.get("like_count")),
            "commentsCount": _to_int(it.get("comments_count")),
            "timestamp": it.get("timestamp"),
            "type": media_type,
        })
    # Reuse the existing Post mapper so time/hashtag handling stays identical.
    recent_posts = [p for p in (_map_post(x, i, uname) for i, x in enumerate(items)) if p]

    return ProfileData(
        username=str(bd.get("username") or uname).lower(),
        full_name=_clean_str(bd.get("full_name"))
        or str(bd.get("username") or uname).replace("_", " ").replace(".", " ").title(),
        bio=_clean_str(bd.get("biography")) or "",
        followers=_to_int(bd.get("followers_count")),
        following=_to_int(bd.get("follows_count")),
        posts_count=_to_int(bd.get("media_count")),
        is_verified=False,  # not exposed by business discovery
        is_business=True,   # business discovery only reaches professional accounts
        category="",
        recent_posts=recent_posts,
    )


# ---------------------------------------------------------------------------
# RapidAPI provider (DATA_PROVIDER=rapidapi) — free tier, 30 calls/month
# ---------------------------------------------------------------------------


def _rapid_headers() -> Dict[str, str]:
    return {"x-rapidapi-host": RAPIDAPI_HOST, "x-rapidapi-key": RAPIDAPI_KEY}


async def _rapid_get(path: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    if not RAPIDAPI_KEY:
        raise RuntimeError(
            "DATA_PROVIDER=rapidapi but RAPIDAPI_KEY is not set. Subscribe (free) "
            "to 'Instagram Cheapest' on rapidapi.com and put the key in backend/.env "
            "as RAPIDAPI_KEY=..."
        )
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(f"https://{RAPIDAPI_HOST}{path}", params=params or {}, headers=_rapid_headers())
    if resp.status_code == 429:
        usage = _rapid_usage_status()
        if usage["used"] == 0:
            # Tracking started mid-month (or quota died before tracking) —
            # seed today as the billing day so the reset estimate is sane.
            _rapid_usage_increment()
            usage = _rapid_usage_status()
        if "MONTHLY" in (resp.text or "").upper() or usage["exhausted"]:
            reset = usage.get("reset_estimate") or "your monthly billing date"
            raise RuntimeError(
                f"RapidAPI monthly quota exhausted ({usage['used']}/{usage['limit']} "
                f"free calls used this month). Fresh fetches resume on {reset}; cached "
                "accounts still analyze instantly, and the app fails over to other "
                "configured providers automatically."
            )
        raise RuntimeError(
            "RapidAPI rate limit hit (1 req/sec on the free plan) — retry in a "
            "few seconds. Monthly calls used: "
            f"{usage['used']}/{usage['limit']}."
        )
    if resp.status_code in (401, 403):
        raise RuntimeError("RapidAPI rejected the key (401/403). Check RAPIDAPI_KEY and that the free 'Basic' plan is subscribed.")
    try:
        data = resp.json()
    except Exception:
        raise RuntimeError(f"RapidAPI returned non-JSON (HTTP {resp.status_code}).")
    if isinstance(data, dict) and data.get("message") and not (data.get("data") or data.get("user")):
        # Some error envelopes come back 200 with a message field.
        raise RuntimeError(f"RapidAPI error: {str(data.get('message'))[:200]}")
    _rapid_usage_increment()  # billable call succeeded
    return data


def _rapid_user_node(raw: Dict[str, Any]) -> Dict[str, Any]:
    """Unwrap the raw user object from the API's envelope(s):
    {data:{user:{...}}} (seen in production), {user:{...}}, or flat {...}."""
    if not isinstance(raw, dict):
        return {}
    node = raw.get("data") if isinstance(raw.get("data"), dict) else raw
    if isinstance(node.get("user"), dict):
        node = node["user"]
    return node


def _rapid_profile_from_raw(raw: Dict[str, Any], requested: str) -> ProfileData:
    """Map the raw Instagram user object (API returns IG's own shapes, which
    vary between the modern and legacy formats) into ProfileData."""
    u = _rapid_user_node(raw)

    def n(key_options: tuple, default=None):
        # _pick supports dotted paths (edge_followed_by.count etc.) and
        # tolerates missing keys — reuse it instead of plain .get().
        return _pick(u, key_options) if key_options else default

    full_name = _clean_str(n(("full_name", "fullName"))) or requested.replace("_", " ").replace(".", " ").title()
    posts = []
    for i, it in enumerate((u.get("edge_owner_to_timeline_media") or {}).get("edges") or []):
        node = it.get("node") if isinstance(it, dict) and isinstance(it.get("node"), dict) else it
        if isinstance(node, dict):
            p = _map_post(node, i, requested)
            if p:
                posts.append(p)
    return ProfileData(
        username=str(n(("username",)) or requested).lower(),
        full_name=full_name,
        bio=_clean_str(n(("biography", "bio"))) or "",
        followers=_to_int(n(("edge_followed_by.count", "follower_count", "followers"))),
        following=_to_int(n(("edge_follow.count", "following_count", "following"))),
        posts_count=_to_int(n(("edge_owner_to_timeline_media.count", "media_count", "posts_count"))),
        is_verified=_as_bool(n(("is_verified", "verified"))),
        is_business=_as_bool(n(("is_business_account", "is_business"))),
        category=_clean_str(n(("category_name", "category", "business_category_name"))) or "",
        recent_posts=posts,
    )


def _normalize_private_media(item: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """user_media returns IG private-API items (like_count, caption object,
    taken_at epoch, numeric media_type). Normalize into the camelCase keys
    _map_post understands."""
    if not isinstance(item, dict):
        return None
    caption = item.get("caption")
    if isinstance(caption, dict):
        caption = caption.get("text") or ""
    media_type_num = item.get("media_type")
    product = str(item.get("product_type") or "").lower()
    typename = str(item.get("__typename") or "")
    if product == "clips" or typename == "graphstoryshortformvideo":
        mtype = "reel"
    elif media_type_num == 2 or typename == "graphvideo" or item.get("video_duration"):
        mtype = "video"
    elif media_type_num == 8 or typename == "graphsidecar":
        mtype = "carousel"
    else:
        mtype = "image"
    ts = item.get("taken_at") or item.get("taken_at_timestamp")
    return {
        "id": str(item.get("id") or item.get("pk") or ""),
        "caption": str(caption or ""),
        "likeCount": _to_int(item.get("like_count")),
        "commentsCount": _to_int(item.get("comment_count")),
        "takenAtTimestamp": ts,
        "type": mtype,
    }


async def _rapid_discover(username: str, limit: int = 20) -> List[Dict[str, Any]]:
    """Competitor discovery for the RapidAPI provider — community mining.

    The account's own recent posts name who they collab with (mentions in
    captions) and who engages with them (commenters) — both are live
    same-niche account signals. All calls are disk-cached, so repeat
    discovery is free.

    Call budget per fresh discovery: 1 (profile/posts) + up to 2 (comments
    on the two top posts) = ≤3 of the 30 monthly free calls.
    """
    uname = username.lower()
    cache_key = f"related:{uname}:{limit}"
    disk = await asyncio.to_thread(_cache_get, cache_key, _DISK_TTL_DISCOVERY)
    if isinstance(disk, list) and disk:
        return disk

    # Cache-aware: if the account was analyzed before, this is free.
    profile = await get_profile(uname)
    raw = await _rapid_get(f"/api/v1/instagram/user/{quote(uname, safe='')}")
    node = _rapid_user_node(raw)

    found: Dict[str, int] = {}   # handle -> signal weight
    seen_codes: List[str] = []

    def add_handle(h: str, weight: int) -> None:
        h = (h or "").strip().lstrip("@").lower()
        if not h or h == uname or not _USERNAME_RE.match(h):
            return
        if h in {"p", "reel", "explore", "stories", "tv"}:
            return
        found[h] = found.get(h, 0) + weight

    # Signal 1: caption mentions — collaborators, partners, community tags.
    for p in profile.recent_posts or []:
        for h in re.findall(r"@([A-Za-z0-9._]{2,30})", p.caption or ""):
            add_handle(h, 3)

    # Signal 2: commenters on the two highest-engagement posts.
    top = sorted(
        node.get("edge_owner_to_timeline_media", {}).get("edges") or [],
        key=lambda e: ((e.get("node") or {}).get("edge_media_preview_like", {}) or {}).get("count", 0),
        reverse=True,
    )[:2]
    for e in top:
        code = (e.get("node") or {}).get("shortcode")
        if not code or code in seen_codes:
            continue
        seen_codes.append(code)
        try:
            cdata = await _rapid_get("/api/v1/instagram/media_comments", {"code": code})
        except RuntimeError:
            continue  # comments are additive; never block discovery
        edges = (
            (((cdata.get("data") or {}).get("xdt_api__v1__media__media_id__comments__connection") or {}).get("edges"))
            or []
        )
        for ce in edges[:25]:
            cu = ((ce.get("node") or {}).get("user") or {})
            add_handle(cu.get("username"), 1)

    # Follower-scale tiebreak happens later in pick_competitors; here we
    # rank by signal weight (mentions > commenters).
    ranked = [h for h, _ in sorted(found.items(), key=lambda kv: kv[1], reverse=True)]

    # Enrich the top candidates with follower counts so the selector can
    # judge scale fit. Capped at 8 to protect the 30-calls/month budget;
    # the ones the selector finally picks come back free from cache.
    candidates: List[Dict[str, Any]] = []
    for h in ranked[:min(8, max(limit, 1))]:
        row = {"username": h, "full_name": "", "bio": "", "followers": 0,
               "verified": False, "private": False}
        try:
            cp = await get_profile(h)
            row.update({
                "full_name": cp.full_name or "",
                "bio": (cp.bio or "")[:160],
                "followers": cp.followers,
                "verified": bool(cp.is_verified),
            })
        except Exception:
            pass  # candidate stays with unknown stats; selector handles it
        await asyncio.sleep(1.1)  # free plan rate limit: 1 req/sec
        candidates.append(row)

    # Prefer candidates whose stats resolved; keep unknowns only if needed.
    known = [c for c in candidates if c.get("followers", 0) > 0]
    final = known if len(known) >= 3 else candidates
    if final:
        await asyncio.to_thread(_cache_set, cache_key, final)
    return final


async def _rapid_fetch_profile(username: str) -> ProfileData:
    """One free call per profile when the user endpoint embeds recent posts;
    falls back to a second (user_media) call only when it doesn't."""
    uname = username.lower()
    raw = await _rapid_get(f"/api/v1/instagram/user/{quote(uname, safe='')}")
    profile = _rapid_profile_from_raw(raw, uname)

    if not profile.recent_posts:
        node = _rapid_user_node(raw)
        user_id = node.get("id") or node.get("pk")
        if user_id:
            try:
                media = await _rapid_get("/api/v1/instagram/user_media", {"user_id": str(user_id)})
                items = media.get("items") if isinstance(media, dict) else None
                if not isinstance(items, list):
                    items = []
                normalized = [n for n in (_normalize_private_media(it) for it in items[:12]) if n]
                posts = [p for p in (_map_post(x, i, uname) for i, x in enumerate(normalized)) if p]
                if posts:
                    profile.recent_posts = posts
            except RuntimeError:
                pass  # profile-only result is still usable; posts are additive
    if not profile.followers and not profile.recent_posts:
        raise ValueError(f"Instagram profile '@{uname}' not found or returned no data via RapidAPI.")
    return profile


def _require_apify_token() -> None:
    """Fail FAST (no network) when Apify is configured but has no token.
    With the token removed (cache-only operation), an uncached handle must
    return an actionable error in milliseconds instead of hanging on a
    doomed HTTP call."""
    if not APIFY_TOKEN:
        raise RuntimeError(
            "No Apify token configured — the app is running in cache-only "
            "mode on cached real data. This handle isn't cached yet: analyze "
            "it once while a data provider is configured, or set DATA_MODE=demo."
        )


async def _run_actor(results_type: str, username: str, results_limit: int) -> List[Dict[str, Any]]:
    _require_apify_token()
    run_input = {
        "directUrls": [f"https://www.instagram.com/{username}/"],
        "resultsType": results_type,
        "resultsLimit": results_limit,
        "addParentData": True,
    }
    await asyncio.to_thread(_apify_preflight_check, APIFY_TOKEN)
    async with httpx.AsyncClient(timeout=APIFY_RUN_TIMEOUT + 30) as client:
        resp = await client.post(_actor_url(results_type, results_limit), json=run_input)
    return _require_items(resp, APIFY_ACTOR_ID)


async def _run_actor_multi(usernames: List[str], results_type: str = "details", results_limit: int = 13) -> List[Dict[str, Any]]:
    """One actor run for SEVERAL profiles (the actor accepts multiple
    directUrls). This is the key latency win: N rivals cost one run instead
    of N sequential 20-60s fetches."""
    _require_apify_token()
    run_input = {
        "directUrls": [f"https://www.instagram.com/{u}/" for u in usernames],
        "resultsType": results_type,
        "resultsLimit": results_limit,
        "addParentData": True,
    }
    async with httpx.AsyncClient(timeout=APIFY_RUN_TIMEOUT + 30) as client:
        resp = await client.post(_actor_url(results_type, results_limit), json=run_input)
    return _require_items(resp, APIFY_ACTOR_ID)


async def get_profiles_batch(usernames: List[str]) -> Dict[str, ProfileData]:
    """Fetch several profiles in a SINGLE actor run. Returns a map of
    lower-cased username -> ProfileData for every profile that returned
    usable data (missing keys = failed fetches the caller should warn about).
    Cached profiles are served without any network call."""
    wanted: List[str] = []
    for u in usernames:
        try:
            wanted.append(normalize_username(u))
        except ValueError:
            continue

    result: Dict[str, ProfileData] = {}
    missing: List[str] = []
    async with _CACHE_LOCK:
        for u in wanted:
            c = _cached(u)
            if c is not None:
                result[u] = c
            else:
                missing.append(u)

    if DATA_MODE == "demo":
        for u in missing:
            result[u] = generate_demo_profile(u)
        return result

    if not missing:
        return result

    # Disk cache layer — profiles fetched in a previous process (or a previous
    # backend restart) come back instantly, no actor run, no cost.
    still_missing: List[str] = []
    for u in missing:
        disk = await asyncio.to_thread(_disk_profile_get, u)
        if disk is not None:
            result[u] = disk
            async with _CACHE_LOCK:
                _profile_cache[u] = (time.monotonic(), disk)
        else:
            still_missing.append(u)

    if not still_missing:
        return result

    # Cache-only safety net: when NO provider has credentials there is no
    # live path for these handles — a doomed fetch would hang/err for every
    # handle. Serve week-old REAL data instead (aged via data_age_hours) so
    # rival research completes offline.
    if not (APIFY_TOKEN or RAPIDAPI_KEY or (GRAPH_ACCESS_TOKEN and GRAPH_IG_USER_ID)):
        for u in list(still_missing):
            stale = await asyncio.to_thread(_disk_profile_get_any, u)
            if stale is not None:
                result[u] = stale
                async with _CACHE_LOCK:
                    _profile_cache[u] = (time.monotonic(), stale)
                still_missing.remove(u)
        if not still_missing:
            return result
        raise RuntimeError(
            "Cache-only mode: no data provider token is configured and these "
            "handles have no cached data: " + ", ".join(f"@{u}" for u in still_missing) +
            ". Analyze them once while a provider is configured, or switch "
            "DATA_MODE=demo for simulated data."
        )

    if DATA_PROVIDER == "rapidapi":
        # 1 req/sec on the free plan — sequential fetches; cache absorbs repeats.
        outcomes: List[Any] = []
        for u in still_missing:
            try:
                outcomes.append(await _rapid_fetch_profile(u))
            except Exception as e:
                outcomes.append(e)
        for u, pr in zip(still_missing, outcomes):
            if isinstance(pr, BaseException) or pr is None:
                continue  # caller surfaces this as a warning
            await asyncio.to_thread(_disk_profile_set, u, pr)
            async with _CACHE_LOCK:
                _profile_cache[u] = (time.monotonic(), pr)
            result[u] = pr
        return result
    try:
        if DATA_PROVIDER == "graph":
            # Graph API: one free business-discovery call per handle.
            outcomes = await asyncio.gather(
                *(_graph_profile_for(u) for u in still_missing), return_exceptions=True
            )
            for u, pr in zip(still_missing, outcomes):
                if isinstance(pr, BaseException) or pr is None:
                    continue  # caller surfaces this as a warning
                await asyncio.to_thread(_disk_profile_set, u, pr)
                async with _CACHE_LOCK:
                    _profile_cache[u] = (time.monotonic(), pr)
                result[u] = pr
            return result
        await asyncio.to_thread(_apify_preflight_check, APIFY_TOKEN)
        items = await _run_actor_multi(still_missing, "details", results_limit=13)
    except (RuntimeError, httpx.HTTPError):
        if FALLBACK_TO_DEMO:
            for u in still_missing:
                result[u] = generate_demo_profile(u)
            return result
        raise

    # Group dataset rows per profile: profile rows by their username, post
    # rows by their owner.
    groups: Dict[str, List[Dict[str, Any]]] = {}
    for it in items:
        if not isinstance(it, dict):
            continue
        if it.get("followersCount") is not None or it.get("biography") is not None:
            owner = str(it.get("username") or "").strip().lower()
        else:
            owner = str(it.get("ownerUsername") or it.get("username") or "").strip().lower()
        if owner:
            groups.setdefault(owner, []).append(it)

    for u in still_missing:
        group = groups.get(u, [])
        if not group:
            continue  # caller surfaces this as a warning
        try:
            profile = _map_profile(group, u)
        except ValueError:
            continue
        _stash_related(u, group)
        await asyncio.to_thread(_disk_profile_set, u, profile)  # persist across restarts
        async with _CACHE_LOCK:
            _profile_cache[u] = (time.monotonic(), profile)
        result[u] = profile

    return result


async def _run_search_actor(queries: List[str], limit: int) -> List[Dict[str, Any]]:
    """Run the Instagram search scraper for user accounts matching the query
    terms. Returns normalized candidate rows (username/full_name/bio/
    followers/verified/private)."""
    _require_apify_token()
    actor_path = (
        APIFY_SEARCH_ACTOR_ID.replace("/", "~")
        if "~" not in APIFY_SEARCH_ACTOR_ID
        else APIFY_SEARCH_ACTOR_ID
    )
    url = (
        f"https://api.apify.com/v2/acts/{quote(actor_path, safe='~')}"
        f"/run-sync-get-dataset-items?token={quote(APIFY_TOKEN, safe='')}"
    )
    run_input = {
        # NOTE: this actor takes ALL keywords in one 'search' field,
        # comma-separated (searchQueries is silently ignored).
        "search": ", ".join(queries[:3]),
        "searchType": "user",
        "searchLimit": max(5, min(limit, 15)),
    }
    async with httpx.AsyncClient(timeout=APIFY_RUN_TIMEOUT + 30) as client:
        resp = await client.post(url, json=run_input)
    items = _require_items(resp, APIFY_SEARCH_ACTOR_ID)

    rows: List[Dict[str, Any]] = []
    for it in items:
        if not isinstance(it, dict):
            continue
        username = str(it.get("username") or "").strip()
        if not username or not _USERNAME_RE.match(username):
            continue  # search output mixes in place/hashtag rows
        rows.append({
            "username": username,
            "full_name": _clean_str(it.get("fullName")) or "",
            "bio": _clean_str(it.get("biography")) or "",
            "followers": _to_int(it.get("followersCount")),
            "verified": _as_bool(it.get("verified")),
            "private": _as_bool(it.get("private") or it.get("isPrivate")),
        })
    return rows


async def _fetch_live_profile(username: str) -> ProfileData:
    """Fetch a real profile. Primary: one 'details' run, which returns the
    profile fields plus its ~12 latest posts with real engagement. Fallback:
    a 'posts' run if the details run yielded no usable posts."""
    items = await _run_actor("details", username, results_limit=13)
    profile = _map_profile(items, username)
    _stash_related(username, items)

    # Transient scrape glitches can yield an empty profile shell; retry once.
    if profile.followers == 0 and not profile.recent_posts:
        try:
            items = await _run_actor("details", username, results_limit=13)
            retry = _map_profile(items, username)
            if retry.followers > 0 or retry.recent_posts:
                profile = retry
                _stash_related(username, items)
        except (RuntimeError, httpx.HTTPError):
            pass

    if not profile.recent_posts:
        try:
            items2 = await _run_actor("posts", username, results_limit=12)
            profile2 = _map_profile(items2, username)
        except (ValueError, RuntimeError, httpx.HTTPError):
            profile2 = None
        if profile2 and profile2.recent_posts:
            profile.recent_posts = profile2.recent_posts

    return profile


# ---------------------------------------------------------------------------
# Competitor discovery (Instagram's own related-accounts signal)
# ---------------------------------------------------------------------------

def _candidate_row(r: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Normalize one relatedProfiles entry into a candidate dict."""
    if not isinstance(r, dict):
        return None
    username = str(r.get("username") or "").strip()
    if not username or not _USERNAME_RE.match(username):
        return None
    return {
        "username": username,
        "full_name": r.get("fullName") or "",
        "bio": r.get("biography") or "",
        "followers": _to_int(r.get("followersCount")),
        "verified": _as_bool(r.get("verified")),
        "private": _as_bool(r.get("private")),
    }


def _stash_related(username: str, items: List[Dict[str, Any]]) -> None:
    """Opportunistically remember relatedProfiles from a details run so
    discovery usually costs zero extra actor runs."""
    try:
        if items and isinstance(items[0], dict):
            rp = items[0].get("relatedProfiles")
            if isinstance(rp, list) and rp:
                _related_cache[username.lower()] = (time.monotonic(), rp)
    except Exception:
        pass


def _demo_related(username: str, limit: int) -> List[Dict[str, Any]]:
    """Deterministic pseudo-competitors for demo mode / offline dev."""
    rnd = _seed_from_username(username + "::related")
    stems = [username.replace(".", ""), username.split(".")[0], username.replace("_", "")]
    suffixes = ["hq", "daily", "official", "hub", "world", "central", "lab", "co", "media", "plus"]
    out: List[Dict[str, Any]] = []
    seen = set()
    for i in range(limit):
        name = f"{rnd.choice(stems)}_{rnd.choice(suffixes)}{rnd.randint(2, 99) if rnd.random() > 0.6 else ''}"
        if name in seen:
            continue
        seen.add(name)
        out.append({
            "username": name,
            "full_name": name.replace("_", " ").title(),
            "bio": "",
            "followers": rnd.randint(1_000, 500_000),
            "verified": False,
            "private": False,
        })
    return out


_STOPWORDS = {
    "the", "and", "for", "with", "your", "you", "our", "this", "that", "from",
    "into", "official", "page", "account", "follow", "posts", "post", "insta",
    "instagram", "https", "http", "www", "com", "dm", "all", "are", "was",
}


def _search_terms_for(username: str, profile_item: Optional[Dict[str, Any]]) -> List[str]:
    """Derive SHORT competitor-search terms from the account itself. Bio words
    carry the strongest niche signal, then full-name words, then username-stem
    words. Instagram user search works best on 1-word queries — long phrases
    return fuzzy junk. Works for ANY account — this is what makes discovery
    universal."""
    raw: List[str] = []
    if profile_item:
        bio = str(profile_item.get("biography") or "")
        raw.extend(bio.replace("\n", " ").split(" "))
        name = str(profile_item.get("fullName") or "").strip()
        if name and 2 <= len(name) <= 40:
            raw.extend(name.split())
    stem = username.split(".")[0].replace("_", " ").replace("-", " ").strip()
    raw.extend(stem.split())

    words: List[str] = []
    seen: set = set()
    for w in raw:
        w = w.strip("#|.,!?").strip()
        if (
            len(w) >= 4
            and w.lower() not in _STOPWORDS
            and not w.startswith(("@", "http", "www"))
            and w.isalpha()
            and w.lower() not in seen
        ):
            seen.add(w.lower())
            words.append(w.lower())
        if len(words) >= 8:
            break

    # Up to 3 single-word queries (one actor run handles the list) — breadth
    # across niche terms beats one long phrase.
    return words[:3]


def _rank_by_relevance(candidates: List[Dict[str, Any]], terms: List[str]) -> List[Dict[str, Any]]:
    """Soft relevance ordering: candidates sharing tokens with the account's
    own words come first; follower count breaks ties. Unrelated candidates are
    kept at the tail (never dropped) so discovery always yields competitors."""
    tokens = {t.lower() for q in terms for t in q.split()}

    def score(c: Dict[str, Any]) -> float:
        text = f"{c.get('username', '')} {c.get('full_name', '')} {c.get('bio', '')}".lower()
        overlap = sum(1 for t in tokens if t in text)
        followers = c.get("followers") or 0
        return overlap * 100 + (followers ** 0.25) / 10

    return sorted(candidates, key=score, reverse=True)


async def research_hashtags(hashtags: List[str], limit: int = 12) -> List[Dict[str, Any]]:
    """Real volume data for hashtags via apify/instagram-hashtag-analytics-scraper.
    Returns rows: name, posts_count, tier buckets (rare/average/frequent/related).
    Returns empty list in demo mode or when Apify is unavailable (additive feature)."""
    if DATA_MODE == "demo":
        return []
    if DATA_PROVIDER == "graph":
        return []  # hashtag volume analytics is a paid actor; skipped in graph mode
    if DATA_PROVIDER == "rapidapi":
        return []  # no hashtag endpoint on this API; skipped in rapidapi mode
    if not APIFY_HASHTAG_RESEARCH:
        return []  # disabled to conserve free-plan credits (APIFY_HASHTAG_RESEARCH=true to enable)
    _require_apify_token()  # no token → additive feature returns [] instead of raising
    tags = [h.strip().lstrip("#").lower() for h in hashtags if h.strip()][:8]
    if not tags:
        return []

    # Disk cache: hashtag volumes move slowly — a week of freshness is plenty.
    cache_key = f"hashtags:{','.join(tags)}:{limit}"
    disk = await asyncio.to_thread(_cache_get, cache_key, _DISK_TTL_HASHTAG)
    if isinstance(disk, list) and disk:
        return disk

    actor_path = (
        APIFY_HASHTAG_ACTOR_ID.replace("/", "~")
        if "~" not in APIFY_HASHTAG_ACTOR_ID
        else APIFY_HASHTAG_ACTOR_ID
    )
    url = (
        f"https://api.apify.com/v2/acts/{quote(actor_path, safe='~')}"
        f"/run-sync-get-dataset-items?token={quote(APIFY_TOKEN, safe='')}"
    )
    try:
        async with httpx.AsyncClient(timeout=APIFY_RUN_TIMEOUT + 30) as client:
            resp = await client.post(url, json={"hashtags": tags})
        items = _require_items(resp, APIFY_HASHTAG_ACTOR_ID)
    except (RuntimeError, httpx.HTTPError):
        if FALLBACK_TO_DEMO:
            return []  # hashtag research is additive; never block the pipeline
        raise

    out: List[Dict[str, Any]] = []
    seen = set()
    for it in items:
        if not isinstance(it, dict):
            continue
        name = str(it.get("name") or "").strip().lstrip("#").lower()
        if not name or name in seen:
            continue
        seen.add(name)
        row = {
            "name": name,
            "posts_count": _to_int(it.get("postsCount")),
            "rare": [],
            "average": [],
            "frequent": [],
            "related": [],
        }
        for bucket in ("rare", "average", "frequent", "related"):
            vals = it.get(bucket)
            if isinstance(vals, list):
                for v in vals:
                    if isinstance(v, dict) and v.get("hash"):
                        h = str(v["hash"]).strip().lstrip("#").lower()
                        if h and h not in seen:
                            seen.add(h)
                            row[bucket].append(h)
        out.append(row)
        if len(out) >= limit:
            break
    if out:
        await asyncio.to_thread(_cache_set, cache_key, out)
    return out


def _local_discover_sync(username: str, limit: int) -> List[Dict[str, Any]]:
    """Mine the LOCAL disk cache for competitor candidates — zero network, no
    provider token of any kind. Signals, all from previously fetched REAL
    data:

      1. @mentions in the target's own cached post captions (collab signal, ×3)
      2. @mentions by other cached accounts, when the mentioned account is
         itself locally known (co-niche signal, ×1)
      3. niche overlap with other cached accounts (shared hashtags/category)

    Only candidates whose own profile is cached (<20h) are returned, so the
    whole competitor-research flow completes offline: NVIDIA's LLM picks the
    rivals and writes the market research, the numbers come from the cache.
    """
    uname = username.lower()
    profiles: Dict[str, Dict[str, Any]] = {}
    try:
        with _cache_conn() as conn:
            rows = conn.execute(
                "SELECT key, value, cached_at FROM cache WHERE key LIKE 'profile:%'"
            ).fetchall()
    except Exception:
        return []
    now = time.time()
    for key, value, ts in rows:
        try:
            d = json.loads(value)
        except Exception:
            continue
        if not isinstance(d, dict):
            continue
        h = key.split(":", 1)[1].strip().lower()
        if h:
            profiles[h] = d

    if uname not in profiles:
        return []  # nothing locally known about this account to mine from

    def _captions(d: Dict[str, Any]):
        for p in d.get("recent_posts") or []:
            if isinstance(p, dict):
                yield p.get("caption") or ""

    weights: Dict[str, int] = {}

    def add(h: str, w: int) -> None:
        h = (h or "").strip().lstrip("@").lower()
        if not h or h == uname or not _USERNAME_RE.match(h):
            return
        if h in {"p", "reel", "reels", "explore", "stories", "tv"}:
            return
        weights[h] = weights.get(h, 0) + w

    # Signal 1: the target's own captions — who they tag/collab with.
    for cap in _captions(profiles[uname]):
        for h in re.findall(r"@([A-Za-z0-9._]{2,30})", cap):
            add(h, 3)

    # Signal 2: mentions by other cached accounts (only for handles we can
    # actually serve offline — mentioned-but-unknown handles can't be
    # researched without a provider, so they'd be dead candidates).
    for h0, d in profiles.items():
        if h0 == uname:
            continue
        for cap in _captions(d):
            for h in re.findall(r"@([A-Za-z0-9._]{2,30})", cap):
                if h.lower() in profiles:
                    add(h, 1)

    # Signal 3: niche overlap with any other cached account.
    tgt_tags = set()
    for p in profiles[uname].get("recent_posts") or []:
        if isinstance(p, dict):
            tgt_tags.update(p.get("hashtags") or [])
    tgt_cat = (profiles[uname].get("category") or "").lower()
    for h0, d in profiles.items():
        if h0 == uname:
            continue
        overlap = 0
        for p in d.get("recent_posts") or []:
            if isinstance(p, dict):
                overlap += len(tgt_tags & set(p.get("hashtags") or []))
        if tgt_cat and (d.get("category") or "").lower() == tgt_cat:
            overlap += 2
        if overlap > 0:
            weights[h0] = weights.get(h0, 0) + overlap

    ranked = sorted(weights.items(), key=lambda kv: kv[1], reverse=True)
    out: List[Dict[str, Any]] = []
    for h, _w in ranked:
        if h not in profiles or h == uname:
            continue
        # Candidate must be cached so its research needs NO provider. Matches
        # PROFILE_DISK_TTL (7 days when cache-only) — Instagram numbers move
        # slowly enough that week-old real data still supports benchmarking.
        row_ts = next((ts for key, _v, ts in rows if key == f"profile:{h}"), 0)
        if now - row_ts > 7 * 86400:
            continue
        d = profiles[h]
        out.append({
            "username": h,
            "full_name": d.get("full_name") or "",
            "bio": (d.get("bio") or "")[:160],
            "followers": d.get("followers") or 0,
            "verified": bool(d.get("is_verified")),
            "private": False,
        })
        if len(out) >= max(limit, 1):
            break
    return out


async def _local_discover(username: str, limit: int) -> List[Dict[str, Any]]:
    """Async wrapper; never raises — an empty list means 'nothing local'."""
    try:
        return await asyncio.to_thread(_local_discover_sync, username, limit)
    except Exception:
        return []


async def discover_related_profiles(username: str, limit: int = 30) -> List[Dict[str, Any]]:
    """Find candidate competitors for ANY Instagram handle.

    Strategy (in order):
      1. Instagram's related-accounts signal for the handle — usually free,
         captured during the main profile fetch.
      2. Keyword search over Instagram users, with terms derived from the
         account's username/full name/bio — so accounts with no related-
         accounts data still get competitors.
    """
    username = normalize_username(username)
    if DATA_MODE == "demo":
        return _demo_related(username, limit)
    if DATA_PROVIDER == "graph":
        # Graph API has no related-accounts or keyword-search surface.
        # Discovery stays Apify-only; in graph mode use Compare mode with
        # explicitly typed rival handles (fully supported, fully free).
        return []
    if DATA_PROVIDER == "rapidapi":
        # No related-accounts/search surface on the free tier — mine the
        # account's own community instead (works, costs ~3 cached calls).
        return await _rapid_discover(username, limit)

    # Disk cache: discovered competitor lists drift slowly — serve instantly
    # for a day instead of re-running discovery actor calls.
    cache_key = f"related:{username}:{limit}"
    disk = await asyncio.to_thread(_cache_get, cache_key, _DISK_TTL_DISCOVERY)
    if isinstance(disk, list) and disk:
        return disk

    # Provider-free discovery: mine the local profile cache (mentions,
    # hashtag/category overlap). When this yields candidates, competitor
    # research completes with ZERO provider calls — NVIDIA's LLM does the
    # selection and analysis, the numbers come from previously fetched
    # real data. Tried before ANY paid provider run.
    local = await _local_discover(username, limit)
    if local:
        await asyncio.to_thread(_cache_set, cache_key, local)
        return local

    profile_item: Optional[Dict[str, Any]] = None
    async with _CACHE_LOCK:
        entry = _related_cache.get(username)
    rp: Optional[List[Any]] = None
    if entry and (time.monotonic() - entry[0]) < _CACHE_TTL:
        rp = entry[1]
    elif entry:
        _related_cache.pop(username, None)

    if rp is None:
        try:
            items = await _run_actor("details", username, results_limit=1)
            if items and isinstance(items[0], dict):
                profile_item = items[0]
                _stash_related(username, items)
                rp = items[0].get("relatedProfiles")
        except (RuntimeError, httpx.HTTPError):
            if FALLBACK_TO_DEMO:
                return _demo_related(username, limit)
            raise
        rp = rp if isinstance(rp, list) else []

    candidates: List[Dict[str, Any]] = []
    for r in rp or []:
        row = _candidate_row(r)
        if row and row["username"].lower() != username.lower():
            candidates.append(row)
        if len(candidates) >= limit:
            await asyncio.to_thread(_cache_set, cache_key, candidates)
            return candidates

    # Fallback: keyword search — makes discovery work for every account.
    queries = _search_terms_for(username, profile_item)
    if queries:
        try:
            search_rows = await _run_search_actor(queries, limit=limit)
        except (RuntimeError, httpx.HTTPError):
            if FALLBACK_TO_DEMO:
                return candidates  # return whatever related accounts we already have
            raise
        fresh = [
            row for row in search_rows
            if row["username"].lower() != username.lower()
            and row["username"].lower() not in {c["username"].lower() for c in candidates}
        ]
        fresh = _rank_by_relevance(fresh, queries)
        candidates.extend(fresh[: max(0, limit - len(candidates))])
    if candidates:
        await asyncio.to_thread(_cache_set, cache_key, candidates)
    return candidates


# ---------------------------------------------------------------------------
# Demo generator (kept for offline dev / FALLBACK_TO_DEMO)
# ---------------------------------------------------------------------------

CATEGORIES = ["Fashion", "Fitness", "Food & Beverage", "Tech", "Beauty",
              "Travel", "Finance", "Education", "Gaming", "Home & Decor"]

HASHTAG_POOL = {
    "Fashion": ["#ootd", "#style", "#fashionista", "#trending", "#newdrop"],
    "Fitness": ["#fitfam", "#gains", "#workout", "#healthylifestyle", "#gymlife"],
    "Food & Beverage": ["#foodie", "#recipe", "#yum", "#eatlocal", "#foodstagram"],
    "Tech": ["#tech", "#innovation", "#startup", "#ai", "#gadgets"],
    "Beauty": ["#skincare", "#makeup", "#glowup", "#beautytips", "#selfcare"],
    "Travel": ["#wanderlust", "#travelgram", "#explore", "#vacay", "#adventure"],
    "Finance": ["#investing", "#moneytips", "#personalfinance", "#wealth", "#fintech"],
    "Education": ["#learning", "#edtech", "#study", "#knowledge", "#growth"],
    "Gaming": ["#gaming", "#gamer", "#esports", "#gameplay", "#twitch"],
    "Home & Decor": ["#interiordesign", "#homedecor", "#diy", "#cozyhome", "#renovation"],
}

MEDIA_TYPES = ["image", "carousel", "reel", "reel", "video"]  # reels weighted higher


def _seed_from_username(username: str) -> random.Random:
    """Deterministic per-username RNG so the same handle always returns the
    same demo numbers (reproducible for offline development)."""
    h = hashlib.sha256(username.lower().encode()).hexdigest()
    return random.Random(int(h[:12], 16))


def generate_demo_profile(username: str) -> ProfileData:
    rnd = _seed_from_username(username)
    category = rnd.choice(CATEGORIES)

    followers = rnd.randint(2_000, 850_000)
    following = rnd.randint(150, 3_000)
    posts_count = rnd.randint(40, 1200)
    is_business = rnd.random() > 0.35
    is_verified = followers > 500_000 and rnd.random() > 0.5

    bio_templates = [
        f"{category} content creator | Turning ideas into visuals ✨",
        f"{category} brand | DM for collabs 📩",
        f"Sharing my {category.lower()} journey 🌱 | Est. {rnd.randint(2016,2024)}",
        f"{category} | Helping you level up, one post at a time",
    ]
    bio = rnd.choice(bio_templates)

    # Engagement tends to shrink as % once follower count grows (realistic)
    base_engagement = max(0.008, 0.09 - (followers / 10_000_000))
    posts: List[Post] = []
    tags = HASHTAG_POOL[category]
    for i in range(12):
        er = max(0.002, rnd.gauss(base_engagement, base_engagement * 0.4))
        likes = int(followers * er)
        comments = int(likes * rnd.uniform(0.01, 0.06))
        posts.append(Post(
            id=f"{username}_{i}",
            caption=f"Post about {category.lower()} #{i+1}",
            likes=max(1, likes),
            comments=max(0, comments),
            posted_days_ago=i * rnd.randint(2, 6),
            hashtags=rnd.sample(tags, k=min(3, len(tags))),
            media_type=rnd.choice(MEDIA_TYPES),
        ))

    return ProfileData(
        username=username,
        full_name=username.replace("_", " ").replace(".", " ").title(),
        bio=bio,
        followers=followers,
        following=following,
        posts_count=posts_count,
        is_verified=is_verified,
        is_business=is_business,
        category=category,
        recent_posts=posts,
    )


# ---------------------------------------------------------------------------
# Public entrypoint
# ---------------------------------------------------------------------------

def _cached(username: str) -> Optional[ProfileData]:
    entry = _profile_cache.get(username)
    if entry and (time.monotonic() - entry[0]) < _CACHE_TTL:
        return entry[1]
    if entry:
        _profile_cache.pop(username, None)
    return None


_apify_exhausted_until: Dict[str, float] = {}  # token -> epoch to retry after


def _apify_token_candidates() -> List[str]:
    """All configured Apify account tokens, freshest-credit first."""
    toks: List[str] = []
    for t in (APIFY_TOKEN, APIFY_TOKEN_2):
        t = (t or "").strip()
        if t and t not in toks:
            toks.append(t)
    now = time.time()
    fresh = [t for t in toks if _apify_exhausted_until.get(t, 0) <= now]
    return fresh or toks  # all marked? retry them anyway (cycles roll over)


def _is_usage_limit_error(e: Exception) -> bool:
    low = str(e).lower()
    return "usage hard limit" in low or "monthly free credit exhausted" in low


async def _fetch_via_provider(provider: str, username: str) -> ProfileData:
    """Dispatch a live fetch to a specific provider."""
    if provider == "rapidapi":
        return await _rapid_fetch_profile(username)
    if provider == "graph":
        return await _graph_profile_for(username)
    return await _fetch_live_profile(username)


def _provider_candidates() -> List[str]:
    """Providers with credentials ACTUALLY configured, in priority order:
    the configured DATA_PROVIDER first (when usable), then any other with
    credentials. Prevents doomed attempts (e.g. DATA_PROVIDER=apify with no
    token) from shadowing a working provider and polluting the error.
    Empty list = no provider configured = cache-only operation."""
    candidates: List[str] = []
    if DATA_PROVIDER == "rapidapi" and RAPIDAPI_KEY:
        candidates.append("rapidapi")
    elif DATA_PROVIDER == "graph" and GRAPH_ACCESS_TOKEN and GRAPH_IG_USER_ID:
        candidates.append("graph")
    elif DATA_PROVIDER == "apify" and _apify_token_candidates():
        candidates.append("apify")
    # Failover: any other provider with credentials, in cost order.
    if _apify_token_candidates() and "apify" not in candidates:
        candidates.append("apify")
    if RAPIDAPI_KEY and "rapidapi" not in candidates:
        candidates.append("rapidapi")
    if GRAPH_ACCESS_TOKEN and GRAPH_IG_USER_ID and "graph" not in candidates:
        candidates.append("graph")
    return candidates


async def _get_profile_uncached(username: str) -> ProfileData:
    """Live/demo fetch with NO cache layers — called at most once per handle
    per TTL window thanks to the layers above.

    Provider failover: providers with configured credentials are tried in
    order; the first success wins. With no credentials anywhere, one clean
    actionable error is raised (cached handles never reach this path)."""
    if DATA_MODE == "demo":
        return generate_demo_profile(username)

    providers: List[str] = _provider_candidates()
    if not providers:
        raise RuntimeError(
            "No Instagram data provider is configured and this handle isn't "
            "cached yet. Cached accounts analyze instantly without any "
            "provider; to fetch NEW handles add one of: APIFY_TOKEN (Apify, "
            "renews monthly), RAPIDAPI_KEY (RapidAPI free tier: subscribe to "
            "'Instagram Cheapest', 30 calls/month, no card), or "
            "GRAPH_ACCESS_TOKEN + GRAPH_IG_USER_ID (free official Meta API) "
            "to backend/.env — or set DATA_MODE=demo for simulated data."
        )

    last_err: Optional[Exception] = None
    errors: List[str] = []
    for provider in providers:
        # Apify may have several accounts (APIFY_TOKEN, APIFY_TOKEN_2):
        # rotate through them, sticking with whichever one works.
        attempts: List[Optional[str]] = [None]
        if provider == "apify":
            attempts = list(_apify_token_candidates()) or [None]
        for tok in attempts:
            prev = APIFY_TOKEN
            try:
                if tok:
                    globals()["APIFY_TOKEN"] = tok
                return await _fetch_via_provider(provider, username)
            except Exception as e:
                if tok and _is_usage_limit_error(e):
                    # Mark this account's credit as spent; retry it after a day.
                    _apify_exhausted_until[tok] = time.time() + 24 * 3600
                msg = f"{provider}: {str(e)[:160]}" if tok is None else f"{provider}[{tok[:10]}…]: {str(e)[:140]}"
                errors.append(msg)
                last_err = e
                globals()["APIFY_TOKEN"] = prev
                continue

    if FALLBACK_TO_DEMO:
        return generate_demo_profile(username)
    if errors:
        raise RuntimeError("All data providers failed — " + " | ".join(errors))
    raise RuntimeError("No data provider configured.")


async def get_profile(username: str) -> ProfileData:
    """Profile fetch with three latency layers:

      1. in-memory TTL cache (instant, per-process)
      2. persistent SQLite disk cache (instant, survives restarts)
      3. live actor run — de-duplicated via in-flight coalescing, so N
         concurrent requests for the same handle share ONE run instead of
         stacking N duplicate 20-60s fetches.
    """
    username = normalize_username(username)

    async with _CACHE_LOCK:
        cached = _cached(username)
    if cached is not None:
        return cached

    # Layer 2: disk (offloaded to a thread; SQLite is sync).
    disk = await asyncio.to_thread(_disk_profile_get, username)
    if disk is not None:
        async with _CACHE_LOCK:
            _profile_cache[username] = (time.monotonic(), disk)
        return disk

    # Layer 3: single shared live fetch per handle — with a stale-cache
    # safety net: if every provider fails (quota, network), serve the last
    # REAL data we have for this handle instead of erroring.
    async with _CACHE_LOCK:
        task = _inflight.get(username)
        if task is None or task.done():
            task = asyncio.ensure_future(_get_profile_uncached(username))
            _inflight[username] = task
    try:
        profile = await asyncio.shield(task)
    except Exception:
        stale = await asyncio.to_thread(_disk_profile_get_any, username)
        if stale is not None:
            async with _CACHE_LOCK:
                _profile_cache[username] = (time.monotonic(), stale)
            return stale
        raise
    finally:
        async with _CACHE_LOCK:
            if _inflight.get(username) is task:
                _inflight.pop(username, None)

    await asyncio.to_thread(_disk_profile_set, username, profile)
    async with _CACHE_LOCK:
        _profile_cache[username] = (time.monotonic(), profile)
    return profile
