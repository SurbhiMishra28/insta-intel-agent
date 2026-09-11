"""Offline sanity checks for scraper.py (no network, no token needed)."""
import asyncio
import os
import time

os.environ["DATA_MODE"] = "live"
os.environ.pop("APIFY_TOKEN", None)

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


def test_map_profile_posts_shape():
    items = [
        {
            "type": "Video",
            "shortCode": "abc123",
            "caption": "New drop tomorrow #sneakers #hypebeast",
            "likesCount": 42000,
            "commentsCount": 512,
            "timestamp": time.time() - 3 * 86400,
            "ownerUsername": "sneakerhead",
        },
        {
            "type": "Image",
            "shortCode": "def456",
            "caption": "Classic fit #ootd",
            "likesCount": 38000,
            "commentsCount": 240,
            "timestamp": time.time() - 8 * 86400,
        },
        {"shortCode": "shell", "caption": "", "likesCount": 0, "commentsCount": 0},  # skipped
    ]
    p = scraper._map_profile(items, "sneakerhead")
    assert isinstance(p, ProfileData)
    assert p.username == "sneakerhead"
    assert len(p.recent_posts) == 2, f"expected 2 posts, got {len(p.recent_posts)}"
    p0 = p.recent_posts[0]
    assert p0.media_type == "video"
    assert p0.likes == 42000 and p0.comments == 512
    assert p0.posted_days_ago == 3
    assert "#sneakers" in p0.hashtags
    print("_map_profile (posts shape): OK")


def test_map_profile_profiles_shape():
    items = [
        {
            "username": "nike",
            "fullName": "Nike",
            "biography": "Just Do It",
            "followersCount": 300_000_000,
            "followsCount": 180,
            "postsCount": 4200,
            "verified": True,
            "businessAccount": True,
            "category": "Sportswear",
            "latestPosts": [
                {
                    "type": "Sidecar",
                    "shortCode": "xyz789",
                    "caption": "Together #team",
                    "likesCount": 900_000,
                    "commentsCount": 5000,
                    "timestamp": time.time() - 1 * 86400,
                }
            ],
        }
    ]
    p = scraper._map_profile(items, "nike")
    assert p.username == "nike" and p.followers == 300_000_000
    assert p.is_verified and p.is_business and p.category == "Sportswear"
    assert p.posts_count == 4200
    assert len(p.recent_posts) == 1
    assert p.recent_posts[0].media_type == "carousel"
    print("_map_profile (profiles shape): OK")


def test_iso_timestamp():
    from datetime import datetime, timezone, timedelta
    iso = (datetime.now(timezone.utc) - timedelta(days=5)).isoformat()
    assert scraper._posted_days_ago({"timestamp": iso}) == 5
    assert scraper._posted_days_ago({"timestamp": time.time() - 2 * 86400}) == 2
    assert scraper._posted_days_ago({"timestamp": (time.time() - 2 * 86400) * 1000}) == 2
    assert scraper._posted_days_ago({}) == 0
    print("_posted_days_ago: OK")


async def test_missing_token_error():
    saved = scraper.APIFY_TOKEN
    scraper.APIFY_TOKEN = ""  # simulate missing token
    try:
        await scraper.get_profile("cristiano")
    except RuntimeError as e:
        assert "APIFY_TOKEN" in str(e), str(e)
        print("missing-token error: OK")
    else:
        raise AssertionError("expected RuntimeError about APIFY_TOKEN")
    finally:
        scraper.APIFY_TOKEN = saved


async def test_cache():
    # Seed the cache and confirm get_profile serves from it without a token.
    demo = ProfileData(username="cacheduser", full_name="Cached", bio="", followers=1,
                       following=1, posts_count=0, is_verified=False, is_business=False,
                       recent_posts=[])
    scraper._profile_cache["cacheduser"] = (time.monotonic(), demo)
    got = await scraper.get_profile("CachedUser")
    assert got.username == "cacheduser"
    print("TTL cache: OK")


if __name__ == "__main__":
    test_normalize()
    test_map_profile_posts_shape()
    test_map_profile_profiles_shape()
    test_iso_timestamp()
    asyncio.run(test_missing_token_error())
    asyncio.run(test_cache())
    print("\nAll offline checks passed.")
