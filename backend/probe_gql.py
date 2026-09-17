"""Live probe: which Instagram GraphQL entry points return REAL data right now?
Run once, wire in what works, then delete. Prints status + shape summary only."""
import json
import httpx

import scraper

UA = scraper._DIRECT_HEADERS["user-agent"]
APP_ID = scraper.IG_WEB_APP_ID

cookies, lsd = scraper._bootstrap_direct_session()
csrf = (cookies.get("csrftoken") if cookies is not None else "") or ""
print("bootstrap: cookies:", "yes" if cookies else "NO", "| lsd:", (lsd or "")[:12], "| csrf:", (csrf or "")[:12])

headers = {
    "user-agent": UA,
    "accept": "*/*",
    "accept-language": "en-US,en;q=0.9",
    "content-type": "application/x-www-form-urlencoded",
    "x-ig-app-id": APP_ID,
    "x-requested-with": "XMLHttpRequest",
    "x-fb-lsd": lsd or "",
    "x-csrftoken": csrf,
    "x-asbd-id": "129477",
    "referer": "https://www.instagram.com/nike/",
    "origin": "https://www.instagram.com",
}

U = "nike"


def summarize(tag, status, payload):
    if status != 200:
        print(f"{tag}: HTTP {status}")
        return
    if not isinstance(payload, dict):
        print(f"{tag}: non-dict payload")
        return
    st = payload.get("status")
    data = payload.get("data") or {}
    user = data.get("user") if isinstance(data, dict) else None
    msg = payload.get("message")
    print(f"{tag}: HTTP 200 status={st} message={msg}")
    if isinstance(user, dict):
        print("   user keys:", list(user.keys())[:10])
        f = (((user.get("edge_followed_by") or {}).get("count"))
             or ((user.get("xdt_api__v1__user__edge_followed_by") or {}).get("count")))
        print("   username:", user.get("username"), "| followers:", f)
        posts = (user.get("edge_owner_to_timeline_media")
                 or user.get("xdt_api__v1__feed__user_timeline_graphql_connection")
                 or user.get("xdt_api__v1__user__timeline_connection") or {})
        edges = posts.get("edges") or []
        print("   posts edges:", len(edges))
        if edges:
            n0 = edges[0].get("node") or {}
            print("   first post keys:", list(n0.keys())[:12])
            print("   first post like count:", (n0.get("edge_liked_by") or {}).get("count"),
                  "| display_url:", bool(n0.get("display_url")))


with httpx.Client(timeout=25, follow_redirects=True, cookies=cookies) as c:
    # 1) classic web_profile_info (known-flaky)
    try:
        r = c.get("https://www.instagram.com/api/v1/users/web_profile_info/",
                  params={"username": U}, headers=headers)
        try:
            summarize("web_profile_info", r.status_code, r.json())
        except Exception:
            print("web_profile_info: HTTP", r.status_code, "| non-JSON")
    except Exception as e:
        print("web_profile_info: ERR", str(e)[:80])

    # 2) classic feed query_id (was 401 earlier)
    try:
        r = c.get("https://www.instagram.com/graphql/query/",
                  params={"query_id": "17842794232208280",
                          "variables": json.dumps({"id": "17908266479", "first": 12})},
                  headers=headers)
        try:
            summarize("gql query_id feed", r.status_code, r.json())
        except Exception:
            print("gql query_id feed: HTTP", r.status_code, "| non-JSON")
    except Exception as e:
        print("gql query_id feed: ERR", str(e)[:80])

    # 3) persisted-query doc_ids used by the real web frontend
    doc_ids = [
        ("10015901848480474", {"username": U}),                     # PolarisProfileRootQuery (profile by username)
        ("9510064595728286", {"id": "17908266479", "first": 12}),   # PolarisProfilePostsQuery (timeline by user id)
        ("8845758582119845", {"shortcode": "C_1test"}),             # post detail (shape probe)
    ]
    for doc_id, variables in doc_ids:
        tag = f"gql doc_id {doc_id}"
        try:
            r = c.post(
                "https://www.instagram.com/graphql/query",
                data={"variables": json.dumps(variables), "doc_id": doc_id, "lsd": lsd or ""},
                headers=headers,
            )
            try:
                summarize(tag, r.status_code, r.json())
            except Exception:
                print(tag, "HTTP", r.status_code, "| non-JSON:", r.text[:80])
        except Exception as e:
            print(tag, "ERR", str(e)[:80])
