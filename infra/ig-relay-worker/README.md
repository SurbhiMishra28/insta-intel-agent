# InstaIQ keyless Instagram relay (free, zero API tokens)

Instagram hard-throttles datacenter IPs — Vercel/Render deployments get
`401/429` on every Instagram call — while the same requests succeed from
residential IPs and Cloudflare's edge. This Worker is the fix that needs
**no API token anywhere**: it fetches `instagram.com/<handle>/` from
Cloudflare's IPs (Instagram serves logged-out pages to them) and returns
the raw HTML to the backend, whose parser extracts the real stats and
posts.

## Deploy it (2 minutes, free)

1. Open **dash.cloudflare.com** → **Workers & Pages** → **Create** → pick
   the "Hello world" starter.
2. Replace the editor's code with the contents of
   [`worker.js`](./worker.js) → **Deploy**.
3. Copy your Worker URL:
   `https://<name>.<account>.workers.dev`
4. Add it to the **backend's environment** (Vercel / Render dashboard →
   Environment Variables), then redeploy the backend:

   ```
   IG_RELAY_URL=https://<name>.<account>.workers.dev
   ```

That's it. The backend tries your Worker **first** in its keyless relay
ladder whenever Instagram throttles the host's own IP.

## How the backend uses it

- `backend/scraper.py` reads `IG_RELAY_URL` (a plain URL — not a secret,
  not an API token) and calls `<IG_RELAY_URL>/?url=https://www.instagram.com/<handle>/`.
- The returned HTML goes through the same exact-stats parser used for the
  direct fetch (GraphQL blob → exact follower/following/post counts and
  embedded posts when present).
- The Worker only relays `www.instagram.com` URLs — it is not an open
  proxy.

## Free tier

Cloudflare Workers: **100,000 requests/day** free — orders of magnitude
more than this app needs. No credit card required.

## Troubleshooting

- `x-relay-status` response header shows what Instagram returned to the
  Worker (200 is what you want; 429 would mean even Cloudflare's IPs are
  throttled for that handle — rare, and the backend's other ladder
  entries still run).
- If the Worker URL is unset, the backend simply skips it and uses the
  public relays — nothing breaks.
