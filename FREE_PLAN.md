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
| Demo generator | Unknown handles get deterministic **simulated** data, badged via `data_age_hours = -1` | n/a (never presented as real) |

Competitor discovery mines the same cache (caption mentions, hashtag and
category overlap), so full competitor research completes offline — the
NVIDIA LLM picks rivals and writes the market research over cached numbers.

## Operating it

- `DATA_MODE=cache` (default): cached real data; unknown handles get
  clearly-badged simulated data. **Never errors on quota.**
- `DATA_MODE=demo`: everything simulated (UI work / demos).
- `CACHE_DIR`/`PROFILE_CACHE_DB` can relocate the store; commit-friendly
  caches are the user's choice — the file is git-ignored by default.

## Known limits (honesty section)

- Brand-new Instagram handles that were never fetched get simulated data
  (clearly badged), not real numbers. Populate the cache by running the app
  once with any data provider enabled, then remove the provider — the data
  stays.
- Trend detection uses the built-in archetype catalog over cached captions.
