# InstaIQ — The Never-Exhausts Free Plan

No Instagram data provider on earth is truly unlimited for free. This app is
architected so that **it never stops working** regardless of quota state:

## The three safety layers (already built in)

| Layer | What it does | Where |
|---|---|---|
| 1. Cache-first | Every analysis is cached 24h (memory + SQLite disk). Re-analyzing the same account costs **zero** provider calls. | `scraper.py` |
| 2. Provider failover | If the active provider is quota-dead, the app automatically tries any other configured provider (RapidAPI ↔ Apify ↔ Graph). | `_get_profile_uncached()` |
| 3. Stale-but-real | If *all* providers are dead, known accounts still analyze instantly from their last real fetch (up to 30 days old), labeled with a `DATA n OLD` badge — never fake data, never an error. | `_disk_profile_get_any()` |

## Your free quota rhythm

| Provider | Free allowance | Resets |
|---|---|---|
| RapidAPI "Instagram Cheapest" | 30 calls/month ≈ 25–30 new accounts | Monthly on your subscribe date (~Oct 12) |
| Apify free plan | $5/month ≈ 5 heavy research runs | Oct 9, then monthly |

Because repeat analyses are free, one month of normal use (a handful of new
accounts + many re-checks) fits comfortably inside the renewing allowances.

## How to operate it sustainably

1. **Default provider stays `rapidapi`** — cheapest per analysis (1 call/profile).
2. When RapidAPI runs dry mid-month, flip one line in `backend/.env`:
   `DATA_PROVIDER=apify` (after Oct 9), then restart the backend.
3. Nothing else to manage — failover + cache handle everything else.
4. Never set `FALLBACK_TO_DEMO=true` unless you want fake data for UI demos.

## Known limits (honesty section)

- Fresh fetches for **brand-new** handles need at least one provider with quota.
- RapidAPI discovery mines mentions/comments (≤8 enriched candidates per run).
- Apify's hashtag-volume research stays off by default (`APIFY_HASHTAG_RESEARCH=false`).
