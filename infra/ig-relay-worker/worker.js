// InstaIQ keyless Instagram relay — Cloudflare Worker (free tier).
//
// WHY: Instagram hard-throttles datacenter IPs (Vercel/Render get 401/429
// on every call), but serves logged-out pages to Cloudflare's edge IPs.
// This Worker fetches an Instagram profile page from the Worker's egress
// (Cloudflare) and hands the raw HTML back to the InstaIQ backend, whose
// parser extracts the real stats. No API keys, no browser, no login.
//
// Deploy (2 minutes, free):
//   1. dash.cloudflare.com -> Workers & Pages -> Create -> "Hello world"
//   2. Replace the code with this file's contents -> Deploy
//   3. Copy the URL (https://<name>.<account>.workers.dev)
//   4. Set it in the backend env:  IG_RELAY_URL=https://<name>.<account>.workers.dev
//      (Vercel/Render dashboard -> environment variables, then redeploy)
//
// Free tier: 100,000 requests/day — far more than this app needs.

const ALLOWED_HOST = "www.instagram.com";
const UA =
  "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 " +
  "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36";

export default {
  async fetch(request) {
    const url = new URL(request.url);

    // CORS preflight (the backend may call this from server-side only, but
    // keeping CORS open costs nothing and aids debugging from a browser).
    if (request.method === "OPTIONS") {
      return new Response(null, { status: 204, headers: cors() });
    }

    const target = url.searchParams.get("url");
    if (!target) {
      return json({ error: "missing ?url= parameter" }, 400);
    }

    let parsed;
    try {
      parsed = new URL(target);
    } catch {
      return json({ error: "invalid url" }, 400);
    }
    // Relay is not an open proxy: only the Instagram profile pages the
    // backend needs may be fetched through it.
    if (parsed.hostname !== ALLOWED_HOST) {
      return json({ error: `only ${ALLOWED_HOST} is allowed` }, 403);
    }

    try {
      const upstream = await fetch(parsed.toString(), {
        headers: {
          "user-agent": UA,
          "accept":
            "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
          "accept-language": "en-US,en;q=0.9",
        },
        redirect: "follow",
        cf: { cacheTtl: 0 },
      });
      const body = await upstream.text();
      const headers = cors();
      headers.set("content-type", "text/plain; charset=utf-8");
      headers.set("x-relay-status", String(upstream.status));
      return new Response(body, { status: 200, headers });
    } catch (err) {
      return json({ error: "upstream fetch failed", detail: String(err) }, 502);
    }
  },
};

function cors() {
  const h = new Headers();
  h.set("access-control-allow-origin", "*");
  h.set("access-control-allow-methods", "GET,OPTIONS");
  return h;
}

function json(obj, status) {
  return new Response(JSON.stringify(obj), {
    status,
    headers: { "content-type": "application/json", ...Object.fromEntries(cors()) },
  });
}
