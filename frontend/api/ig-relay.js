// InstaIQ IG relay — Vercel serverless function (deploys automatically with
// the frontend; no extra account needed).
//
// Mirrors cloudflare-worker/ig-relay.js: fetches Instagram's web_profile_info
// JSON from Vercel's edge IPs and streams it back for the backend's gateway
// provider. NOTE: unlike the Cloudflare Worker, Vercel functions run on AWS
// IPs, which Instagram may also rate-limit — treat this as a best-effort
// relay and prefer the Cloudflare Worker or APIFY_TOKEN for reliability.
//
// After this deploys, set on Render:
//   IG_GATEWAY_URLS = https://insta-intel-agent.vercel.app/api/ig-relay?url={q}
//   IG_GATEWAY_TOKEN = <same value as RELAY_SECRET below>

const RELAY_SECRET = "change-me-please"; // mirror as IG_GATEWAY_TOKEN on Render

const IG_UA =
  "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36";

export default async function handler(req, res) {
  const { url: target, token } = req.query;

  if (RELAY_SECRET && token !== RELAY_SECRET) {
    return res.status(401).json({ error: "unauthorized" });
  }
  if (!target || !/^https:\/\/www\.instagram\.com\//.test(target)) {
    return res.status(400).json({ error: "bad target" });
  }

  try {
    const upstream = await fetch(target, {
      headers: {
        "user-agent": IG_UA,
        accept: "application/json",
        "x-ig-app-id": "936619743392459",
        "x-requested-with": "XMLHttpRequest",
        "accept-language": "en-US,en;q=0.9",
      },
      redirect: "follow",
    });
    res.statusCode = upstream.status;
    res.setHeader(
      "content-type",
      upstream.headers.get("content-type") || "application/json"
    );
    res.setHeader("x-relay-upstream-status", String(upstream.status));
    res.send(Buffer.from(await upstream.arrayBuffer()));
  } catch (e) {
    res.status(502).json({ error: "upstream failed", detail: String(e).slice(0, 120) });
  }
}
