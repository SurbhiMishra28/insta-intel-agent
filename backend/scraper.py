"""
Data acquisition layer for Instagram profile data.

Data modes (DATA_MODE env var):

  - "live" (default): real public profile data fetched via Apify's
    Instagram Scraper actor (apify/instagram-scraper) — profile fields +
    the ~12 latest posts with real likes, comments, timestamps and media
    types. Requires APIFY_TOKEN. Every successful fetch is persisted to
    the local SQLite cache, so repeats are instant and free.
  - "cache": serve only cached real data; unknown handles get
    clearly-badged simulated data (data_age_hours = -1). No network calls.
  - "demo": everything simulated (offline development).

Latency layers in live mode, per handle:

  1. in-memory TTL cache (instant, per-process)
  2. persistent SQLite disk cache (instant, survives restarts)
  3. Apify actor run (10–60s; batched — N rivals cost ONE run)

Live failures fail loudly (RuntimeError → HTTP 503) unless
FALLBACK_TO_DEMO=true, in which case badged simulated data is served.

Competitor discovery (live mode): Instagram's own related-accounts signal
(usually captured free during the main profile fetch), with a keyword
search over Instagram users as fallback — plus local cache mining
(mentions/hashtag overlap), which runs first and is completely free.
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

import httpx
from dotenv import load_dotenv

# Load backend/.env (if present) BEFORE reading env vars below, so this
# module works regardless of import order.
load_dotenv(os.path.join(os.path.dirname(__file__), '.env'))

from models import Post, ProfileData

DATA_MODE = os.getenv("DATA_MODE", "live").lower()  # "live" | "cache" | "demo"
APIFY_TOKEN = os.getenv("APIFY_TOKEN", "").strip()
APIFY_ACTOR_ID = os.getenv("APIFY_ACTOR_ID", "apify/instagram-scraper")
APIFY_SEARCH_ACTOR_ID = os.getenv("APIFY_SEARCH_ACTOR_ID", "apify/instagram-search-scraper")
APIFY_RUN_TIMEOUT = int(os.getenv("APIFY_RUN_TIMEOUT", "300"))  # seconds
# Default false: a failed live fetch must fail loudly (503/502) rather than
# silently serve fake data. Opt in to demo fallback explicitly.
FALLBACK_TO_DEMO = os.getenv("FALLBACK_TO_DEMO", "false").lower() in ("1", "true", "yes")

# Simple in-process TTL cache so repeated handles (e.g. compare mode re-fetching
# the main account) don't re-trigger a paid actor run within the TTL window.
_CACHE_TTL = int(os.getenv("PROFILE_CACHE_TTL", "1800"))  # seconds
_profile_cache: Dict[str, tuple] = {}  # username -> (monotonic_ts, ProfileData)
_related_cache: Dict[str, tuple] = {}  # username -> (monotonic_ts, List[dict])
_CACHE_LOCK = asyncio.Lock()

# ---------------------------------------------------------------------------
# Persistent disk cache (SQLite) — real data survives restarts, so repeat
# analyses and compare-mode refetches never re-trigger a paid actor run.
# ---------------------------------------------------------------------------

_CACHE_DB = os.getenv("PROFILE_CACHE_DB", os.path.join(os.path.dirname(__file__), "profile_cache.db"))

_DISK_TTL_PROFILE = int(os.getenv("PROFILE_DISK_TTL", str(7 * 86400)))   # fresh enough for metrics
_DISK_TTL_DISCOVERY = int(os.getenv("DISCOVERY_DISK_TTL", str(24 * 3600)))  # competitor lists drift slowly


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
    30 days) together with its age. Real data, just possibly stale."""
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

# Shared media-type mapping (kept so cached legacy JSON keeps mapping cleanly).
_MEDIA_TYPE_MAP = {
    "Image": "image", "GraphImage": "image", "image": "image", "IMAGE": "image",
    "Video": "video", "GraphVideo": "video", "video": "video", "VIDEO": "video",
    "Sidecar": "carousel", "GraphSidecar": "carousel", "Carousel": "carousel",
    "carousel": "carousel", "XDTMediaCarousel": "carousel", "album": "carousel",
    "Clip": "reel", "Reel": "reel", "reel": "reel", "REEL": "reel",
    "Clips": "reel", "GraphStoryVideo": "reel",
}


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
        m = re.search(r"ig\.me/(?:m/)?([^/?&#]+)", text, re.IGNORECASE)
        if not m:
            raise ValueError("Could not find a username in that Instagram URL.")
        username = m.group(1)
    else:
        username = text

    username = username.strip().strip("/").lstrip("@")
    username = username.split("?")[0].split("#")[0]

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

    Handles resultsType=details (one profile item, possibly with embedded
    latestPosts), resultsType=posts (post items, no profile fields), and
    mixed output where both appear."""
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
    credit is exhausted; never blocks a run when the check itself fails."""
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


def _require_apify_token() -> None:
    """Fail FAST (no network) when a live fetch is attempted without a token."""
    if not APIFY_TOKEN:
        raise RuntimeError(
            "No Apify token configured — set APIFY_TOKEN in backend/.env "
            "(or switch DATA_MODE=demo for simulated data)."
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
    await asyncio.to_thread(_apify_preflight_check, APIFY_TOKEN)
    async with httpx.AsyncClient(timeout=APIFY_RUN_TIMEOUT + 30) as client:
        resp = await client.post(_actor_url(results_type, results_limit), json=run_input)
    return _require_items(resp, APIFY_ACTOR_ID)


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
# Competitor discovery — related accounts + local cache mining + search
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
    return words[:3]


def _rank_by_relevance(candidates: List[Dict[str, Any]], terms: List[str]) -> List[Dict[str, Any]]:
    """Soft relevance ordering: candidates sharing tokens with the account's
    own words come first; follower count breaks ties."""
    tokens = {t.lower() for q in terms for t in q.split()}

    def score(c: Dict[str, Any]) -> float:
        text = f"{c.get('username', '')} {c.get('full_name', '')} {c.get('bio', '')}".lower()
        overlap = sum(1 for t in tokens if t in text)
        followers = c.get("followers") or 0
        return overlap * 100 + (followers ** 0.25) / 10

    return sorted(candidates, key=score, reverse=True)


def _local_discover_sync(username: str, limit: int) -> List[Dict[str, Any]]:
    """Mine the LOCAL disk cache for competitor candidates — zero network, no
    tokens of any kind. Signals, all from previously fetched REAL data:

      1. @mentions in the target's own cached post captions (collab signal, x3)
      2. @mentions by other cached accounts, when the mentioned account is
         itself locally known (co-niche signal, x1)
      3. niche overlap with other cached accounts (shared hashtags/category)

    Only candidates whose own profile is cached within the profile TTL are
    returned, so the whole competitor-research flow can complete offline when
    the provider is unavailable."""
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
    # actually serve offline — mentioned-but-unknown handles would be dead
    # candidates without a provider).
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
        # Candidate must be cached within the profile TTL so its research
        # stays grounded in reasonably fresh REAL data.
        row_ts = next((ts for key, _v, ts in rows if key == f"profile:{h}"), 0)
        if now - row_ts > _DISK_TTL_PROFILE:
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
      1. Local cache mining (mentions/hashtag overlap) — free, instant.
      2. Instagram's related-accounts signal for the handle — usually free,
         captured during the main profile fetch.
      3. Keyword search over Instagram users, with terms derived from the
         account's username/full name/bio — so accounts with no related-
         accounts data still get competitors.
    """
    username = normalize_username(username)
    if DATA_MODE == "demo":
        return _demo_related(username, limit)

    # Disk cache: discovered competitor lists drift slowly — serve instantly
    # for a day instead of re-running discovery actor calls.
    cache_key = f"related:{username}:{limit}"
    disk = await asyncio.to_thread(_cache_get, cache_key, _DISK_TTL_DISCOVERY)
    if isinstance(disk, list) and disk:
        return disk

    # Provider-free discovery: mine the local profile cache (mentions,
    # hashtag/category overlap). When this yields candidates, competitor
    # research completes with ZERO provider calls — the LLM does the
    # selection and analysis, the numbers come from previously fetched
    # real data. Tried before ANY paid provider run.
    local = await _local_discover(username, limit)
    if local:
        await asyncio.to_thread(_cache_set, cache_key, local)
        return local

    # Live mode without a token: same contract as cache mode — nothing
    # beyond the local cache is possible, but never raise.
    if DATA_MODE != "live" or not APIFY_TOKEN:
        return []

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
# Demo generator (offline dev / FALLBACK_TO_DEMO)
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
        f"Sharing my {category.lower()} journey 🌱 | Est. {rnd.randint(2016, 2024)}",
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
            caption=f"Post about {category.lower()} #{i + 1}",
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


# ---------------------------------------------------------------------------
# Public entrypoints
# ---------------------------------------------------------------------------

def _cached(username: str) -> Optional[ProfileData]:
    entry = _profile_cache.get(username)
    if entry and (time.monotonic() - entry[0]) < _CACHE_TTL:
        return entry[1]
    if entry:
        _profile_cache.pop(username, None)
    return None


def is_demo_row(profile: ProfileData) -> bool:
    """True when a profile row is simulated (data_age_hours == -1)."""
    return profile.data_age_hours == -1


def _cached_profile_pool_sync(exclude: set, limit: int) -> List[Dict[str, Any]]:
    """Snapshot of every REAL account in the disk cache (within the profile
    TTL), shaped like discovery candidates. Used as a fallback rival pool
    when auto-discovery has nothing to offer — comparative readouts stay
    grounded in real data instead of dead-ending."""
    try:
        with _cache_conn() as conn:
            rows = conn.execute(
                "SELECT key, value, cached_at FROM cache WHERE key LIKE 'profile:%'"
                " ORDER BY cached_at DESC",
            ).fetchall()
    except Exception:
        return []
    now = time.time()
    out: List[Dict[str, Any]] = []
    for key, value, ts in rows:
        handle = key.split(":", 1)[1].strip().lower()
        if not handle or handle in exclude or handle in {o["username"] for o in out}:
            continue
        if now - ts > _DISK_TTL_PROFILE:
            continue
        try:
            d = json.loads(value)
        except Exception:
            continue
        if not isinstance(d, dict):
            continue
        out.append({
            "username": handle,
            "full_name": d.get("full_name") or "",
            "bio": (d.get("bio") or "")[:160],
            "followers": d.get("followers") or 0,
            "verified": bool(d.get("is_verified")),
            "private": False,
        })
        if len(out) >= max(limit, 1):
            break
    return out


async def get_cached_profile_pool(exclude: Optional[set] = None, limit: int = 30) -> List[Dict[str, Any]]:
    """Async wrapper: real accounts already cached in this app, freshest
    first. Never raises; empty list means the cache has nothing usable."""
    try:
        return await asyncio.to_thread(_cached_profile_pool_sync, set(exclude or ()), limit)
    except Exception:
        return []


async def get_profiles_batch(usernames: List[str]) -> Dict[str, ProfileData]:
    """Fetch several profiles at once. In live mode the missing handles are
    fetched in a SINGLE actor run. Returns a map of lower-cased username ->
    ProfileData for every profile that could be served (missing keys =
    failed fetches the caller should warn about)."""
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

    # Cache/demo mode (or live without a token): unknown handles fall back to
    # deterministic simulated data (badged via data_age_hours = -1) so
    # pipelines never hard-fail.
    if DATA_MODE != "live" or not APIFY_TOKEN:
        for u in still_missing:
            stale = await asyncio.to_thread(_disk_profile_get_any, u)
            if stale is not None:
                result[u] = stale
                async with _CACHE_LOCK:
                    _profile_cache[u] = (time.monotonic(), stale)
            else:
                demo = generate_demo_profile(u)
                demo.data_age_hours = -1
                result[u] = demo
                async with _CACHE_LOCK:
                    _profile_cache[u] = (time.monotonic(), demo)
        return result

    # LIVE: one batched actor run for every uncached handle.
    try:
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


async def get_profile(username: str) -> ProfileData:
    """Profile fetch with latency layers:

      1. in-memory TTL cache (instant, per-process)
      2. persistent SQLite disk cache (instant, survives restarts)
      3. live Apify actor run (10-60s) — only in live mode with a token
      4. deterministic demo generator (cache/demo modes, badged simulated;
         live mode only when FALLBACK_TO_DEMO=true)
    """
    username = normalize_username(username)

    async with _CACHE_LOCK:
        cached = _cached(username)
    if cached is not None:
        return cached

    if DATA_MODE == "demo":
        demo = generate_demo_profile(username)
        demo.data_age_hours = -1
        async with _CACHE_LOCK:
            _profile_cache[username] = (time.monotonic(), demo)
        return demo

    # Layer 2: disk (offloaded to a thread; SQLite is sync).
    disk = await asyncio.to_thread(_disk_profile_get, username)
    if disk is not None:
        async with _CACHE_LOCK:
            _profile_cache[username] = (time.monotonic(), disk)
        return disk

    live_capable = DATA_MODE == "live" and bool(APIFY_TOKEN)
    if live_capable:
        # Layer 3: live fetch.
        try:
            profile = await _fetch_live_profile(username)
        except (RuntimeError, httpx.HTTPError):
            if FALLBACK_TO_DEMO:
                profile = None
            else:
                raise
        else:
            await asyncio.to_thread(_disk_profile_set, username, profile)
            async with _CACHE_LOCK:
                _profile_cache[username] = (time.monotonic(), profile)
            return profile

    # Live fetch failed (with FALLBACK_TO_DEMO) or non-live mode: last resort
    # is stale REAL data, then a badged simulated row.
    stale = await asyncio.to_thread(_disk_profile_get_any, username)
    if stale is not None:
        async with _CACHE_LOCK:
            _profile_cache[username] = (time.monotonic(), stale)
        return stale

    demo = generate_demo_profile(username)
    demo.data_age_hours = -1
    async with _CACHE_LOCK:
        _profile_cache[username] = (time.monotonic(), demo)
    return demo
