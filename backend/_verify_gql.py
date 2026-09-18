"""One-off verification: run the Instagram GraphQL provider directly.

Executes _fetch_graphql_profile (the in-page GraphQL provider that runs
Instagram's own queries inside a real instagram.com tab via the Chrome
DevTools protocol) and prints what came back, so the provider can be
verified in isolation from the rest of the provider chain.

Usage: python _verify_gql.py <handle> [more handles...]
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
import scraper  # noqa: E402


def _show(p) -> int:
    if p is None:
        print("[verify] RESULT: None — provider returned no profile")
        return 1
    print("[verify] RESULT: profile object returned")
    print(f"  username   : {p.username}")
    print(f"  full_name  : {p.full_name!r}")
    print(f"  followers  : {p.followers:,}")
    print(f"  following  : {p.following:,}")
    print(f"  posts      : {p.posts_count:,}")
    print(f"  verified   : {p.is_verified}")
    print(f"  category   : {p.category}")
    print(f"  posts_in   : {len(p.recent_posts or [])}")
    if p.recent_posts:
        p0 = p.recent_posts[0]
        print(
            f"  latest post: {p0.media_type} id={p0.id} "
            f"likes={p0.likes:,} comments={p0.comments:,}"
        )
    real = p.followers > 0 and (p.recent_posts or p.posts_count > 0)
    precise = p.followers % 1000 != 0  # og-tag parses are round thousands
    print(f"[verify] VERDICT: {'REAL' if real else 'EMPTY'}"
          f"{' + PRECISE (GraphQL-grade)' if precise else ' (abbreviated?)'}")
    return 0 if real else 1


async def main() -> int:
    handles = sys.argv[1:] or ["nasa"]
    rc = 0
    for h in handles:
        print(f"\n=== GraphQL provider test for @{h} ===")
        try:
            profile = await asyncio.wait_for(
                scraper._fetch_graphql_profile(h), timeout=90
            )
        except asyncio.TimeoutError:
            print("[verify] TIMEOUT after 90s")
            rc = 2
            continue
        except Exception as e:  # noqa: BLE001
            print(f"[verify] EXCEPTION: {type(e).__name__}: {e}")
            rc = 2
            continue
        rc = _show(profile) or rc
    return rc


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
