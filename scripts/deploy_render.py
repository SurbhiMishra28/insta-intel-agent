"""One-command Render deploy + verify for the InstaIQ backend.

Usage:
  python scripts/deploy_render.py --api-key rnd_xxx [--rapidapi-key xxx] [--owner tea-xxx]

Steps performed (httpx is used throughout — curl's TLS is unreliable on some networks):
  1. Find the `instaiq-backend` service under the workspace.
  2. Optionally set RAPIDAPI_KEY (and RAPIDAPI_HOST) in the service environment.
  3. Trigger a deploy of the latest commit on the service's branch.
  4. Poll the deploy until it finishes (prints progress + commit sha).
  5. Verify production: /health, new-code marker /api/usage, then an
     end-to-end POST /api/analyze on a fresh handle (real RapidAPI fetch).
"""
import argparse
import sys
import time

import httpx

API = "https://api.render.com/v1"
SERVICE_HINT = "instaiq-backend"


def _client(key: str) -> httpx.Client:
    return httpx.Client(base_url=API, headers={"Authorization": f"Bearer {key}"}, timeout=45)


def find_service(client: httpx.Client, owner_id: str | None) -> dict:
    params = {"limit": 50}
    if owner_id:
        params["ownerId"] = owner_id
    r = client.get("/services", params=params)
    r.raise_for_status()
    for svc in r.json():
        svc = svc.get("service", svc)
        if SERVICE_HINT in str(svc.get("name", "")).lower():
            return svc
    names = [s.get("service", s).get("name") for s in r.json()]
    raise SystemExit(f"Service '{SERVICE_HINT}' not found. Existing services: {names}")


def set_env(client: httpx.Client, service_id: str, key: str, value: str) -> None:
    r = client.patch(f"/services/{service_id}/env-vars/{key}", json={"value": value})
    if r.status_code == 404:
        r = client.post(f"/services/{service_id}/env-vars", json={"key": key, "value": value})
    r.raise_for_status()
    print(f"  env set: {key}=***")


def trigger_deploy(client: httpx.Client, service_id: str) -> dict:
    r = client.post(f"/services/{service_id}/deploys", json={"clearCache": "do_not_clear"})
    r.raise_for_status()
    d = r.json()
    print(f"  deploy triggered: {d['id']} (commit {d.get('commit', {}).get('id', '?')[:8]})")
    return d


def wait_for_deploy(client: httpx.Client, service_id: str, deploy_id: str, timeout: int = 900) -> str:
    start = time.time()
    while time.time() - start < timeout:
        r = client.get(f"/services/{service_id}/deploys/{deploy_id}")
        r.raise_for_status()
        status = r.json().get("status", "?")
        print(f"  [{int(time.time() - start):>3}s] {status}")
        if status in ("live", "build_failed", "pre_deploy_failed", "canceled", "deactivated"):
            return status
        time.sleep(15)
    return "timeout"


def verify(base_url: str) -> None:
    print("  /health:", end=" ")
    print(httpx.get(f"{base_url}/health", timeout=60).status_code)
    r = httpx.get(f"{base_url}/api/usage", timeout=60)
    print(f"  /api/usage (new-code marker): HTTP {r.status_code}")
    print("  analyze (real fetch):", end=" ")
    r = httpx.post(f"{base_url}/api/analyze", json={"username": "beingsurbhimishra"}, timeout=120)
    body = r.json()
    if r.status_code == 200:
        p = body.get("profile", {})
        print(f"OK @{p.get('username')} ({p.get('followers')} followers)")
    else:
        print(f"HTTP {r.status_code}: {str(body)[:160]}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--api-key", required=True, help="Render API key (rnd_...)")
    ap.add_argument("--rapidapi-key", default=None, help="RapidAPI key to set in the service env")
    ap.add_argument("--owner", default=None, help="Render workspace id (tea-...) to filter by")
    ap.add_argument("--base-url", default="https://instaiq-backend.onrender.com")
    args = ap.parse_args()

    with _client(args.api_key) as c:
        print("1. Finding service...")
        svc = find_service(c, args.owner)
        sid = svc["id"]
        print(f"   found {svc['name']} ({sid})")

        if args.rapidapi_key:
            print("2. Setting environment...")
            set_env(c, sid, "RAPIDAPI_KEY", args.rapidapi_key)
            set_env(c, sid, "RAPIDAPI_HOST", "instagram-cheapest.p.rapidapi.com")

        print("3. Deploying latest commit...")
        dep = trigger_deploy(c, sid)

        print("4. Waiting for deploy...")
        status = wait_for_deploy(c, sid, dep["id"])
        print(f"   final status: {status}")
        if status != "live":
            sys.exit(2)

    print("5. Verifying production...")
    verify(args.base_url)
    print("Done.")


if __name__ == "__main__":
    main()
