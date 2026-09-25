// InstaIQ keyless Instagram relay — Cloudflare Worker (free tier).
//
// WHY: Instagram hard-throttles datacenter IPs (Vercel/Render get 401/429
// on every call). This Worker fetches instagram.com profile pages from
// Cloudflare's edge and hands the raw HTML back to the InstaIQ backend,
// whose parser extracts the real stats. No API keys, no browser, no login.
//
// Anti-throttle behavior (v2):
//   1. Cookie bootstrap — a warm-up GET to instagram.com collects the
//      anonymous session cookies (csrftoken, ig_did, ...) Instagram expects;
//      the profile fetch then carries them like a real browser.
//   2. Retry with backoff — one 429/401 triggers a short pause and a second
//      attempt (waiting on fetch costs no CPU; still one relay invocation).
//   3. Edge caching — successful profile HTML is cached at Cloudflare's edge
//      for 10 minutes (Cache API, free, no bindings), so bursts of analyses
//      don't multiply Instagram requests.
//
// Deploy (free):  npx wrangler deploy    (from this directory)
// Free tier: 100,000 requests/day.

const ALLOWED_HOST = "www.instagram.com";
const UA =
  "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 " +
  "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36";
const EDGE_CACHE_TTL = 600; // seconds — real data, just edge-cached

function browserHeaders(extra) {
  return {
    "user-agent": UA,
    "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "accept-language": "en-US,en;q=0.9",
    "sec-fetch-dest": "document",
    "sec-fetch-mode": "navigate",
    "sec-fetch-site": "none",
    "upgrade-insecure-requests": "1",
    ...extra,
  };
}

function collectCookies(setCookies) {
  // Anonymous session cookies only — strip attributes, join for the header.
  const out = [];
  for (const c of setCookies || []) {
    const pair = c.split(";")[0];
    if (pair.includes("=")) out.push(pair);
  }
  return out.join("; ");
}

async function fetchProfileHtml(target) {
  // 1) Warm-up: homepage grants the anonymous cookies a browser would have.
  let cookieHeader = "";
  try {
    const warm = await fetch(`https://${ALLOWED_HOST}/`, {
      headers: browserHeaders(),
      redirect: "follow",
      cf: { cacheTtl: 0, cacheEverything: false },
    });
    cookieHeader = collectCookies(warm.headers.getSetCookie());
  } catch {
    // No cookies is not fatal — the direct attempt may still work.
  }

  // 2) Profile fetch with browser context; one retry after a short pause
  //    when Instagram throttles.
  let lastStatus = 0;
  for (let attempt = 0; attempt < 2; attempt++) {
    if (attempt > 0) await new Promise((r) => setTimeout(r, 1500));
    const extra = cookieHeader ? { cookie: cookieHeader } : {};
    const resp = await fetch(target, {
      headers: browserHeaders(extra),
      redirect: "follow",
      cf: { cacheTtl: 0, cacheEverything: false },
    });
    lastStatus = resp.status;
    if (resp.ok) {
      const body = await resp.text();
      if (body && body.length > 2000) {
        return { body, upstreamStatus: lastStatus };
      }
      // Too-small body = challenge/empty shell — treat as throttle.
      lastStatus = 0;
      continue;
    }
  }
  return { body: "", upstreamStatus: lastStatus };
}

export default {
  async fetch(request) {
    const url = new URL(request.url);

    if (request.method === "OPTIONS") {
      return new Response(null, { status: 204, headers: cors() });
    }

    const target = url.searchParams.get("url");
    if (!target) return json({ error: "missing ?url= parameter" }, 400);
    let parsed;
    try {
      parsed = new URL(target);
    } catch {
      return json({ error: "invalid url" }, 400);
    }
    // Relay is not an open proxy: only Instagram profile pages may be fetched.
    if (parsed.hostname !== ALLOWED_HOST) {
      return json({ error: `only ${ALLOWED_HOST} is allowed` }, 403);
    }

    // Edge cache first (successful HTML only; misses pass through).
    const cache = caches.default;
    let cached = await cache.match(request);
    if (cached) {
      const headers = new Headers(cached.headers);
      headers.set("x-relay-cache", "hit");
      return new Response(cached.body, { status: 200, headers });
    }

    let result;
    try {
      result = await fetchProfileHtml(parsed.toString());
    } catch (err) {
      return json({ error: "upstream fetch failed", detail: String(err) }, 502);
    }

    const headers = cors();
    headers.set("x-relay-status", String(result.upstreamStatus));
    headers.set("content-type", "text/plain; charset=utf-8");
    if (result.body) {
      // Cache the good page at the edge for EDGE_CACHE_TTL.
      const cacheable = new Response(result.body, {
        status: 200,
        headers: {
          "content-type": "text/plain; charset=utf-8",
          "cache-control": `public, max-age=${EDGE_CACHE_TTL}`,
        },
      });
      await cache.put(request, cacheable.clone());
      headers.set("x-relay-cache", "miss");
      return new Response(result.body, { status: 200, headers });
    }

    // Upstream throttled — report honestly; the backend's ladder moves on.
    return new Response("", { status: 200, headers });
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
