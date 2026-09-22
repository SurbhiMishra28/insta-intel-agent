// InstaIQ IG relay — Cloudflare Worker. Free tier: 100k req/day.
//
// Why: Instagram hard-blocks datacenter IPs (Render free tier gets 401/429 on
// every endpoint and bot-challenges headless Chrome). This worker fetches
// Instagram's own web_profile_info JSON from Cloudflare's edge IPs and
// streams it back — the exact data.user payload the backend's gateway
// provider parses: exact follower counts + 12 recent posts with real
// likes/comments. No tokens, no login.
//
// Deploy (one command from the repo root, wrangler must be logged in):
//   cd cloudflare-worker
//   npx wrangler deploy
//   echo "<random-secret>" | npx wrangler secret put RELAY_TOKEN
//
// Then set on the Render backend service (mirror the same secret):
//   IG_GATEWAY_URLS  = https://ig-relay.<your-subdomain>.workers.dev/?url={q}
//   IG_GATEWAY_TOKEN = <random-secret>
//
// Security model:
//   - RELAY_TOKEN (Wrangler secret) must be sent as ?token=... — mirrors
//     IG_GATEWAY_TOKEN on the backend, which appends it automatically.
//   - Only https://www.instagram.com/api/v1/users/web_profile_info/ is
//     proxyable — the worker is NOT an open proxy.

export default {
  async fetch(request, env) {
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
    if (request.method !== "GET") {
      return json({ error: "method not allowed" }, 405);
    }

    // --- Auth -----------------------------------------------------------------
    const token = (env && env.RELAY_TOKEN) || "";
    if (token && url.searchParams.get("token") !== token) {
      return json({ error: "unauthorized" }, 401);
    }

    // --- Target allowlist -------------------------------------------------------
    // The backend gateway provider only ever calls web_profile_info with a
    // username query; refuse everything else so this can't be used as a
    // general-purpose open proxy.
    const ALLOWED_PREFIX = "https://www.instagram.com/api/v1/users/web_profile_info/?username=";
    // searchParams already percent-decodes once — do NOT decode again.
    const target = url.searchParams.get("url") || "";
    let parsed;
    try {
      parsed = new URL(target);
    } catch {
      return json({ error: "bad target" }, 400);
    }
    if (parsed.origin !== "https://www.instagram.com") {
      return json({ error: "host not allowed" }, 400);
    }
    if (!parsed.pathname.startsWith("/api/v1/users/web_profile_info")) {
      return json({ error: "path not allowed" }, 400);
    }
    const uname = parsed.searchParams.get("username") || "";
    // Instagram handles: 1-30 chars of letters, digits, dots, underscores.
    if (!/^[A-Za-z0-9._]{1,30}$/.test(uname)) {
      return json({ error: "bad username" }, 400);
    }

    // --- Fetch from the edge ----------------------------------------------------
    // Anonymous browsers bootstrap device cookies before calling web_profile_info;
    // sending a stable synthetic session (ig_did/csrftoken/x-csrftoken) clears the
    // "require_login" nudge Instagram returns to bare requests from shared IPs.
    // --- Fetch from the edge ----------------------------------------------------
    // Two upstream strategies, tried in order:
    //   A. i.instagram.com/api/v1/users/web_profile_info — mobile-API host, same
    //      data.user payload, different challenge rules; often answers where the
    //      www host returns 401 require_login for shared egress IPs.
    //   B. www.instagram.com with synthetic device cookies (anonymous browsers
    //      bootstrap ig_did/csrftoken before calling; bare requests get nudged).
    const did = crypto.randomUUID();
    const WEB_HEADERS = {
      "user-agent":
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
      accept: "application/json",
      "accept-language": "en-US,en;q=0.9",
      referer: "https://www.instagram.com/",
      "x-ig-app-id": "936619743392459",
      "x-requested-with": "XMLHttpRequest",
      "x-csrftoken": did,
      cookie: `ig_did=${did}; mid=${did}; csrftoken=${did}`,
    };
    const MOBILE_UA =
      "Instagram 275.0.0.27.98 Android (33/13; 420dpi; 1080x2400; samsung; SM-G991B; o1s; exynos2100; en_US; 314910256)";
    const MOBILE_HEADERS = {
      "user-agent": MOBILE_UA,
      accept: "*/*",
      "accept-language": "en-US,en;q=0.9",
      "x-ig-app-id": "936619743392459",
      "x-fb-lsd": "AVqbxf3J_Cw",
      "x-csrftoken": did,
      cookie: `ig_did=${did}; mid=${did}; csrftoken=${did}`,
    };

    const mobileUrl =
      "https://i.instagram.com" + parsed.pathname + parsed.search;
    let upstream = null;
    let lastStatus = 0;
    try {
      upstream = await fetch(mobileUrl, { headers: MOBILE_HEADERS, cf: { cacheTtl: 0 } });
      lastStatus = upstream.status;
      if (upstream.status === 401 || upstream.status === 403 || upstream.status === 429) {
        // Challenged on the mobile host — fall back to the web host before giving up.
        const web = await fetch(parsed.toString(), { headers: WEB_HEADERS, cf: { cacheTtl: 0 } });
        lastStatus = web.status;
        if (upstream.status !== 401 || (web.status !== 401 && web.status !== 403 && web.status !== 429)) {
          upstream = web;
        }
        // else keep the mobile response — both challenged; surface mobile's body.
      }
    } catch (err) {
      // Network-level failure reaching IG: fail cleanly so the backend's
      // gateway ladder moves on to the next gateway instead of hanging.
      return json({ error: "upstream fetch failed", detail: String(err) }, 502);
    }
    // Mark which host answered so debugging doesn't require reading bodies.
    const servedBy = upstream.url.startsWith("https://i.instagram") ? "mobile" : "web";

    return new Response(upstream.body, {
      status: upstream.status,
      headers: {
        "content-type": upstream.headers.get("content-type") || "application/json",
        "access-control-allow-origin": "*",
        "x-relay-upstream-status": String(upstream.status),
        "x-relay-served-by": servedBy,
      },
    });
  },
};

function json(body, status) {
  return new Response(JSON.stringify(body), {
    status,
    headers: {
      "content-type": "application/json",
      "access-control-allow-origin": "*",
    },
  });
}
