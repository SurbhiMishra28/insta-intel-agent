// InstaIQ IG relay — deploy as a free Cloudflare Worker (100k req/day free).
//
// Why: Instagram hard-blocks datacenter IPs (Render free tier gets 401/429 on
// every endpoint and bot-challenges headless Chrome). This worker fetches
// Instagram's own web_profile_info JSON from Cloudflare's edge IPs and
// streams it back — the exact data.user payload the backend's gateway
// provider parses: exact follower counts + 12 recent posts with real
// likes/comments. No tokens, no login.
//
// Deploy (5 min):
//   1. dash.cloudflare.com → Workers & Pages → Create application → Worker
//   2. Name it (e.g. "ig-relay") → Deploy → Edit code → paste this file → Deploy
//   3. Copy your URL: https://ig-relay.<your-subdomain>.workers.dev
//   4. Render dashboard → insta-iq-backend service → Environment → add:
//        IG_GATEWAY_URLS = https://ig-relay.<your-subdomain>.workers.dev/?url={q}
//      (Save → backend auto-redeploys)
//
// Security note: keep the SECRET_TOKEN below set and mirror it in Render as
// IG_GATEWAY_TOKEN so only your backend can use your relay.

const SECRET_TOKEN = "change-me-please"; // set the same value in Render as IG_GATEWAY_TOKEN

export default {
  async fetch(request) {
    const url = new URL(request.url);

    if (request.method === "OPTIONS") {
      return new Response(null, {
        status: 204,
        headers: {
          "access-control-allow-origin": "*",
          "access-control-allow-methods": "GET,OPTIONS",
          "access-control-allow-headers": "*",
        },
      });
    }

    if (SECRET_TOKEN && url.searchParams.get("token") !== SECRET_TOKEN) {
      return new Response(JSON.stringify({ error: "unauthorized" }), {
        status: 401,
        headers: { "content-type": "application/json" },
      });
    }

    const target = url.searchParams.get("url");
    if (!target || !/^https:\/\/www\.instagram\.com\//.test(target)) {
      return new Response(JSON.stringify({ error: "bad target" }), {
        status: 400,
        headers: { "content-type": "application/json" },
      });
    }

    const upstream = await fetch(target, {
      headers: {
        "user-agent":
          "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        accept: "application/json",
        "x-ig-app-id": "936619743392459",
        "x-requested-with": "XMLHttpRequest",
        "accept-language": "en-US,en;q=0.9",
      },
      cf: { cacheTtl: 0 },
    });

    return new Response(upstream.body, {
      status: upstream.status,
      headers: {
        "content-type": upstream.headers.get("content-type") || "application/json",
        "access-control-allow-origin": "*",
        "x-relay-upstream-status": String(upstream.status),
      },
    });
  },
};
