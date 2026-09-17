"""Debug: in-page profile calls with bot signals patched.
Tries both /graphql/query (doc_id POST) and /api/v1/users/web_profile_info (GET),
executed inside the real Chrome page after patching navigator.webdriver."""
import asyncio
import json
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import websockets

import scraper

USERNAME = sys.argv[1] if len(sys.argv) > 1 else "nike"

PATCH = """
Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
window.__igLsd = (document.documentElement.innerHTML.split('"LSD",[],{"token":"')[1] || '').split('"')[0];
'patched:' + (window.__igLsd ? 'lsd-ok' : 'lsd-missing');
"""

GQL_POST = r"""
(async () => {
  try {
    const vars = JSON.stringify({username: "%s"});
    const body = new URLSearchParams({variables: vars, doc_id: "%s", lsd: window.__igLsd || ''});
    const r = await fetch('/graphql/query', {
      method: 'POST', credentials: 'include',
      headers: {'content-type': 'application/x-www-form-urlencoded',
                'x-ig-app-id': '%s', 'x-fb-lsd': window.__igLsd || '',
                'x-requested-with': 'XMLHttpRequest'},
      body: body.toString(),
    });
    const t = await r.text();
    let shape = 'non-json';
    try {
      const j = JSON.parse(t);
      shape = 'status=' + j.status + ' user=' + !!(j.data && j.data.user);
    } catch (e) {}
    return JSON.stringify({call: 'gql-post', http: r.status, shape: shape, head: t.slice(0, 160)});
  } catch (e) { return JSON.stringify({call: 'gql-post', error: String(e)}); }
})()
""" % (USERNAME, "10015901848480474", scraper.IG_WEB_APP_ID)

WPI_GET = r"""
(async () => {
  try {
    const r = await fetch('/api/v1/users/web_profile_info/?username=%s', {
      method: 'GET', credentials: 'include',
      headers: {'accept': '*/*',
                'x-ig-app-id': '%s',
                'x-requested-with': 'XMLHttpRequest'},
    });
    const t = await r.text();
    let shape = 'non-json';
    try {
      const j = JSON.parse(t);
      const u = j.data && j.data.user;
      shape = 'user=' + !!u + (u ? (' followers=' + u.edge_followed_by.count + ' posts=' + (u.edge_owner_to_timeline_media.count || 0) + ' edges=' + ((u.edge_owner_to_timeline_media.edges || []).length)) : '');
    } catch (e) {}
    return JSON.stringify({call: 'wpi-get', http: r.status, shape: shape, head: t.slice(0, 160)});
  } catch (e) { return JSON.stringify({call: 'wpi-get', error: String(e)}); }
})()
""" % (USERNAME, scraper.IG_WEB_APP_ID)


async def main():
    ws_url = await asyncio.to_thread(scraper._cdp_ws_url)
    if not ws_url:
        print("NO CDP TARGET")
        return

    async def rpc(ws, mid, method, params=None):
        await ws.send(json.dumps({"id": mid, "method": method, "params": params or {}}))
        while True:
            m = json.loads(await ws.recv())
            if m.get("id") == mid:
                return m

    async with websockets.connect(ws_url, max_size=64 * 1024 * 1024) as ws:
        # Patch bot signals in EVERY document before any page script runs.
        await rpc(ws, 9, "Page.addScriptToEvaluateOnNewDocument", {"source": PATCH})
        await rpc(ws, 1, "Runtime.enable")
        await rpc(ws, 2, "Page.navigate", {"url": f"https://www.instagram.com/{USERNAME}/"})
        await asyncio.sleep(5)
        chk = await rpc(ws, 3, "Runtime.evaluate", {"expression": PATCH, "returnByValue": True})
        print("patch:", (chk.get("result") or {}).get("result", {}).get("value"))
        for mid, js in ((4, GQL_POST), (5, WPI_GET)):
            ev = await rpc(ws, mid, "Runtime.evaluate", {
                "expression": js, "returnByValue": True, "awaitPromise": True, "timeout": 25000})
            val = ((ev.get("result") or {}).get("result") or {}).get("value")
            if isinstance(val, str):
                d = json.loads(val)
                print(f"{d.get('call')}: HTTP {d.get('http')} | {d.get('shape')}")
                if d.get("http") != 200 or d.get("error"):
                    print("   head:", (d.get("head") or d.get("error") or "")[:140])


asyncio.run(main())
