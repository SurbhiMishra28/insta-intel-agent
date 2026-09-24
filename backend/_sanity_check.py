"""Offline sanity checks for the REAL-ONLY data layer (no network, no keys).

All data in the system is real; these tests verify the provider wiring and
that failures surface honestly instead of ever serving simulated rows.
"""
import asyncio
import os
import time

for _k in ("APIFY_TOKEN", "APIFY_TOKENS", *[(f"APIFY_TOKEN_{i}") for i in range(2, 10)], "RAPIDAPI_KEY", "IG_ACCESS_TOKEN", "IG_BUSINESS_ID"):
    os.environ.pop(_k, None)

import scraper  # noqa: E402
from models import Post, ProfileData  # noqa: E402


def test_normalize():
    cases = {
        "cristiano": "cristiano",
        " @Cristiano ": "cristiano",
        "https://www.instagram.com/cristiano/": "cristiano",
        "instagram.com/nike?hl=en": "nike",
        "https://www.instagram.com/glow.beauty.co/": "glow.beauty.co",
    }
    for raw, expected in cases.items():
        got = scraper.normalize_username(raw)
        assert got == expected, f"normalize({raw!r}) = {got!r}, want {expected!r}"

    for bad in ["", "instagram.com/explore/", "instagram.com/p/Cxyz/", "has space!"]:
        try:
            scraper.normalize_username(bad)
        except ValueError:
            pass
        else:
            raise AssertionError(f"normalize({bad!r}) should have raised ValueError")
    print("normalize_username: OK")


def _make_profile(username: str, followers: int = 1000) -> ProfileData:
    return ProfileData(
        username=username,
        full_name=username.replace("_", " ").title(),
        bio=f"{username} bio",
        followers=followers,
        following=100,
        posts_count=3,
        is_verified=False,
        is_business=False,
        category="Tech",
        recent_posts=[
            Post(id=f"{username}_{i}", caption=f"post {i} @friend{i} #tech",
                 likes=100 + i, comments=5, posted_days_ago=i + 1,
                 hashtags=["#tech"], media_type="image")
            for i in range(3)
        ],
    )


def test_cache_roundtrip():
    p = _make_profile("cacheuser")
    scraper._disk_profile_set(p.username, p)
    got = scraper._disk_profile_get(p.username)
    assert got is not None and got.username == "cacheuser"
    assert got.followers == 1000
    assert got.recent_posts[0].likes == 100
    # Fresh lookup (inside TTL) must NOT be stamped as stale.
    assert got.data_age_hours is None
    print("disk cache roundtrip: OK")


async def test_get_profile_cache_first():
    scraper._profile_cache.clear()
    p = _make_profile("cacheduser")
    scraper._disk_profile_set(p.username, p)
    got = await scraper.get_profile("CachedUser")
    assert got.username == "cacheduser"
    print("get_profile serves real cached data: OK")


async def test_batch_mixed():
    """Cached handles are served; unknown handles simply stay missing —
    the caller surfaces them as errors/warnings. No simulated rows are
    invented. (Providers are stubbed so the test stays offline.)"""
    scraper._profile_cache.clear()
    known = _make_profile("batchknown")
    scraper._disk_profile_set("batchknown", known)

    async def not_found_direct(username):
        raise ValueError(f"Instagram profile '@{username}' not found (stubbed)")

    real_direct = scraper._fetch_direct_profile
    scraper._fetch_direct_profile = not_found_direct
    try:
        res = await scraper.get_profiles_batch(["batchknown", "batchunknown_99"])
    finally:
        scraper._fetch_direct_profile = real_direct
        scraper._profile_cache.clear()
    assert res["batchknown"].username == "batchknown"
    assert "batchunknown_99" not in res, "unknown handles must not get invented rows"
    print("batch fetch (cached real; unknown stays honestly missing): OK")


def test_local_discovery():
    import sqlite3
    # Seed cache with a target and two same-niche accounts that mention each other.
    # NOTE: write via _disk_profile_set FIRST (it manages its own connections),
    # then refresh timestamps in a separate short-lived connection. Holding an
    # open write transaction across the _disk_profile_set calls would lock the
    # DB and silently drop the seed rows (writes are best-effort by design).
    for uname in ("disco_main", "disco_rival1", "disco_rival2"):
        p = _make_profile(uname, followers=5000)
        p.recent_posts[0].caption = "collab with @disco_rival1 #tech"
        scraper._disk_profile_set(uname, p)
    conn = sqlite3.connect(scraper._CACHE_DB)
    now = time.time()
    for uname in ("disco_main", "disco_rival1", "disco_rival2"):
        conn.execute("UPDATE cache SET cached_at = ? WHERE key = ?", (now, f"profile:{uname}"))
    conn.commit()
    conn.close()

    rows = scraper._local_discover_sync("disco_main", 10)
    names = [r["username"] for r in rows]
    assert "disco_rival1" in names and "disco_rival2" in names, names
    print("local discovery mines cached mentions: OK")


async def test_live_mode_failover_to_direct_provider():
    """Provider wiring: no Apify tokens must call the keyless direct provider
    (provider #2) — not silently serve fake data."""
    scraper._profile_cache.clear()
    _purge_handle("directonlyhandle")

    old = list(scraper.APIFY_TOKENS)
    scraper.APIFY_TOKENS = []
    try:
        calls = {"direct": 0}

        async def fake_direct(username):
            calls["direct"] += 1
            return _make_profile(username, followers=4242)

        real_direct = scraper._fetch_direct_profile
        scraper._fetch_direct_profile = fake_direct
        got = await scraper.get_profile("DirectOnlyHandle")
        assert calls["direct"] == 1
        assert got.username == "directonlyhandle" and got.followers == 4242
        print("no-token -> keyless direct provider: OK")
    finally:
        scraper._fetch_direct_profile = real_direct
        scraper.APIFY_TOKENS = old
        scraper._profile_cache.clear()
        _purge_handle("directonlyhandle")


async def test_live_mode_pool_exhausted_falls_over_to_direct():
    """Simulated pool exhaustion: tokens EXIST but every run fails at the
    token level -> get_profile must fall through to the direct provider."""
    scraper._profile_cache.clear()
    _purge_handle("exhaustedhandle")

    old = list(scraper.APIFY_TOKENS)
    scraper.APIFY_TOKENS = ["fake-exhausted-token"]
    try:
        calls = {"apify": 0, "direct": 0}

        async def fake_apify(username):
            calls["apify"] += 1
            raise RuntimeError("No Apify token in the pool could serve this request (simulated)")

        async def fake_direct(username):
            calls["direct"] += 1
            return _make_profile(username, followers=777)

        real_apify, real_direct = scraper._fetch_live_profile, scraper._fetch_direct_profile
        scraper._fetch_live_profile = fake_apify
        scraper._fetch_direct_profile = fake_direct
        got = await scraper.get_profile("exhaustedhandle")
        assert calls["apify"] == 1 and calls["direct"] == 1, calls
        assert got.followers == 777
        print("simulated pool exhaustion -> direct fallback: OK")
    finally:
        scraper._fetch_live_profile, scraper._fetch_direct_profile = real_apify, real_direct
        scraper.APIFY_TOKENS = old
        scraper._profile_cache.clear()
        _purge_handle("exhaustedhandle")


async def test_live_mode_honest_error_when_all_providers_fail():
    """All providers failing must raise — NEVER fall back to a simulated row
    (the bug that showed fake numbers for real handles)."""
    scraper._profile_cache.clear()
    _purge_handle("bothfailhandle")

    old = list(scraper.APIFY_TOKENS)
    scraper.APIFY_TOKENS = []
    try:
        async def failing_direct(username):
            raise RuntimeError("Instagram rate-limiting this IP (simulated)")

        async def failing_direct(username):
            raise RuntimeError("Keyless HTTP fetch failed (simulated)")

        scraper._fetch_direct_profile = failing_direct
        scraper._fetch_direct_profile = failing_direct
        try:
            await scraper.get_profile("bothfailhandle")
        except RuntimeError:
            print("all-providers-failed -> honest RuntimeError: OK")
        else:
            raise AssertionError("expected RuntimeError, got a (fake) profile instead")
    finally:
        scraper._fetch_direct_profile = _restore_direct
        scraper._fetch_direct_profile = failing_direct  # stays failing (provider removed)
        scraper.APIFY_TOKENS = old
        scraper._profile_cache.clear()
        _purge_handle("bothfailhandle")


def _purge_handle(handle: str) -> None:
    import sqlite3
    try:
        conn = sqlite3.connect(scraper._CACHE_DB)
        conn.execute("DELETE FROM cache WHERE key LIKE ?", (f"profile:{handle}%",))
        conn.execute("DELETE FROM cache WHERE key LIKE ?", (f"related:{handle}%",))
        conn.commit()
        conn.close()
    except Exception:
        pass


def _restore_direct(username: str):
    raise RuntimeError("direct provider not stubbed in this test")




def test_no_demo_machinery():
    """The demo/simulated subsystem must not exist anymore."""
    assert not hasattr(scraper, "generate_demo_profile"), "demo generator must be removed"
    assert not hasattr(scraper, "_demo_related"), "demo competitor generator must be removed"
    assert not hasattr(scraper, "is_demo_row"), "demo-row badge helper must be removed"
    src = open(scraper.__file__, encoding="utf-8").read()
    assert "FALLBACK_TO_DEMO" not in src, "demo fallback flag must be removed"
    assert "DATA_MODE" not in src, "data-mode switch must be removed (live is the only mode)"
    print("no demo/simulated machinery in scraper: OK")


def _cleanup_test_rows():
    """Remove test rows so the real-data cache stays tidy."""
    import sqlite3
    conn = sqlite3.connect(scraper._CACHE_DB)
    for prefix in ("cacheuser", "cacheduser", "batchknown", "batchunknown",
                   "disco_main", "disco_rival1", "disco_rival2",
                   "directonlyhandle", "exhaustedhandle", "bothfailhandle"):
        conn.execute("DELETE FROM cache WHERE key LIKE ?", (f"profile:{prefix}%",))
        conn.execute("DELETE FROM cache WHERE key LIKE ?", (f"related:{prefix}%",))
    conn.commit()
    conn.close()


if __name__ == "__main__":
    test_normalize()
    test_cache_roundtrip()
    asyncio.run(test_get_profile_cache_first())
    asyncio.run(test_batch_mixed())
    asyncio.run(test_live_mode_failover_to_direct_provider())
    asyncio.run(test_live_mode_pool_exhausted_falls_over_to_direct())
    asyncio.run(test_live_mode_honest_error_when_all_providers_fail())
    test_local_discovery()
    test_no_demo_machinery()
    _cleanup_test_rows()
    print("\nAll offline checks passed.")
