# InstaIQ — The One-Key Free Plan

This app runs entirely on **one API key**: an NVIDIA NIM key (`nvapi-...`,
free at build.nvidia.com) for all AI analysis. There are **no third-party
Instagram data providers** — no Apify, no RapidAPI, no Meta Graph tokens,
so nothing can ever hit a usage wall or expire.

## Where the data comes from

| Source | What | Freshness |
|---|---|---|
| SQLite disk cache (`backend/profile_cache.db`) | Real Instagram data from past fetches — the app's source of truth | Fresh 7 days (`PROFILE_DISK_TTL`); served aged up to 30 days with a "data age" badge |
| In-memory TTL cache | Instant repeats within a running backend | 30 minutes |
| Keyless Playwright Chromium | Unknown handles are fetched live with zero credentials | Real-time |

Competitor discovery mines the same cache (caption mentions, hashtag and
category overlap) first; the LLM (OpenRouter by default) picks rivals and
writes the market research over real numbers.

## Operating it

- The app serves REAL data only: cached rows when fresh, live fetches
  otherwise, honest errors for handles that do not exist or when every
  provider is blocked. Simulated/demo data does not exist.
- `CACHE_DIR`/`PROFILE_CACHE_DB` can relocate the store; commit-friendly
  caches are the user's choice — the file is git-ignored by default.

## Known limits (honesty section)

- Brand-new Instagram handles are fetched live via the keyless Playwright
  Chromium provider; if Instagram blocks the host, the request fails with
  an honest 503 instead of serving invented numbers.
- Trend detection uses the built-in archetype catalog over cached captions.
