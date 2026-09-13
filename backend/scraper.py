"""
Data acquisition layer for Instagram profile data — NVIDIA-only edition.

This app runs entirely on ONE API key (NVIDIA NIM, for all AI analysis).
There are NO third-party Instagram data providers: no Apify, no RapidAPI,
no Meta Graph tokens. Real profile data comes exclusively from the
persistent local cache (SQLite), populated by past live fetches.

Where profile data comes from now:

  1. Persistent disk cache (SQLite) — real Instagram data previously
     fetched. Fresh for PROFILE_DISK_TTL (default 7 days); still served
     (aged, up to 30 days) as a last resort. Survives restarts.
  2. In-memory TTL cache — instant repeats within one process.
  3. Deterministic demo generator — brand-NEW handles that were never
     cached get simulated data (offline dev). Demo rows carry
     data_age_hours = -1 so the UI can badge them as simulated.

Modes, selected by the DATA_MODE environment variable:

  - "cache" (default): serve cached real data; unknown handles fall back
    to the demo generator. Never touches a paid third-party API.
  - "demo": everything is simulated (offline development).

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
from typing import Any, Dict, List, Optional

from dotenv import load_dotenv

# Load backend/.env (if present) BEFORE reading env vars below, so this
# module works regardless of import order.
load_dotenv(os.path.join(os.path.dirname(__file__), '.env'))

from models import Post, ProfileData

DATA_MODE = os.getenv("DATA_MODE", "cache").lower()  # "cache" | "demo"

# ---------------------------------------------------------------------------
# Persistent disk cache (SQLite) — the app's real-data source of truth.
# ---------------------------------------------------------------------------

_CACHE_DB = os.getenv("PROFILE_CACHE_DB", os.path.join(os.path.dirname(__file__), "profile_cache.db"))

_DISK_TTL_PROFILE = int(os.getenv("PROFILE_DISK_TTL", str(7 * 86400)))   # fresh enough for metrics
_DISK_TTL_DISCOVERY = int(os.getenv("DISCOVERY_DISK_TTL", str(24 * 3600)))  # competitor lists drift slowly

# In-memory TTL cache so repeated handles don't re-read the disk within a process.
_CACHE_TTL = int(os.getenv("PROFILE_CACHE_TTL", "1800"))  # seconds
_profile_cache: Dict[str, tuple] = {}  # username -> (monotonic_ts, ProfileData)
_CACHE_LOCK = asyncio.Lock()


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
# Competitor discovery — mined from the LOCAL cache, zero network
# ---------------------------------------------------------------------------

_STOPWORDS = {
    "the", "and", "for", "with", "your", "you", "our", "this", "that", "from",
    "into", "official", "page", "account", "follow", "posts", "post", "insta",
    "instagram", "https", "http", "www", "com", "dm", "all", "are", "was",
}


def _search_terms_for(username: str, profile: Optional[ProfileData]) -> List[str]:
    """Derive SHORT niche terms from the account's bio/full name/username.
    (Kept for candidate relevance ranking.)"""
    raw: List[str] = []
    if profile:
        raw.extend((profile.bio or "").replace("\n", " ").split(" "))
        name = (profile.full_name or "").strip()
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
    returned, so the whole competitor-research flow completes offline:
    the NVIDIA LLM picks the rivals and writes the market research, and the
    numbers come from the cache.
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
    """Find candidate competitors for an Instagram handle — entirely from the
    local cache (mentions, hashtag/category overlap). No network, no tokens.

    In demo mode, deterministic pseudo-competitors are generated instead.
    """
    username = normalize_username(username)
    if DATA_MODE == "demo":
        return _demo_related(username, limit)

    # Disk cache: discovered competitor lists drift slowly — serve instantly
    # for a day instead of re-mining.
    cache_key = f"related:{username}:{limit}"
    disk = await asyncio.to_thread(_cache_get, cache_key, _DISK_TTL_DISCOVERY)
    if isinstance(disk, list) and disk:
        return disk

    local = await _local_discover(username, limit)
    if local:
        await asyncio.to_thread(_cache_set, cache_key, local)
    return local


# ---------------------------------------------------------------------------
# Demo generator (offline dev / unknown handles in cache mode)
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
# Public entrypoint
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


async def get_profiles_batch(usernames: List[str]) -> Dict[str, ProfileData]:
    """Fetch several profiles at once — cache and/or demo, no network.
    Returns a map of lower-cased username -> ProfileData for every profile
    that could be served (missing keys = unservable)."""
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

    # Disk cache layer.
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

    # Cache-only mode: unknown handles fall back to deterministic simulated
    # data (badged via data_age_hours = -1) so pipelines never hard-fail.
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


async def get_profile(username: str) -> ProfileData:
    """Profile fetch with three latency layers — NO third-party calls:

      1. in-memory TTL cache (instant, per-process)
      2. persistent SQLite disk cache (instant, survives restarts) — the
         app's source of REAL Instagram data
      3. deterministic demo generator (unknown handles; badged simulated)
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

    # Last resort: deterministic simulated data (badged, never presented
    # as real) — or, when available, any older cached real data.
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
