# InstaIQ — AI Instagram Profile & Competitor Intelligence Agent

An AI agent that analyzes an Instagram account (engagement rate, posting
cadence, content mix, hashtag strategy), **auto-discovers and researches its
5–10 most relevant competitors**, and produces an AI-written competitive
intelligence report with strengths, weaknesses, competitive gaps, content
gaps, and concrete opportunities.

The natural-language layer is built on **LangChain**: structured-output
chains reason over metrics computed from real profile data (the LLM narrates
numbers, it never invents them). With no LLM key configured, deterministic
rule-based engines produce the same output shape, so the app always works.

```
┌─────────────┐      REST/JSON       ┌──────────────────┐
│  React UI   │  ───────────────▶   │   FastAPI backend  │
│ (Vite)      │  ◀───────────────   │                     │
└─────────────┘                     │  scraper.py  ──▶ profile data
                                     │  ai_engine.py ──▶ metrics + AI insights
                                     └──────────────────┘
```

## 1. What it actually does (read this first)

Instagram's official API (Graph API) only returns data for accounts *you*
manage via a connected Facebook Page — it does not allow pulling arbitrary
public competitor profiles. Every "Instagram competitor scraper" you'll
find is either (a) working around this against Instagram's Terms of
Service, or (b) reselling access from a licensed data partner. This project
is built honestly around that constraint:

- **Real data only, by design.** Every number comes from a real source:
  the official Instagram Graph API (when configured), Apify's Instagram
  Scraper actor (when `APIFY_TOKEN` is set), or — with zero credentials —
  the **keyless direct Instagram HTTP layer**: bootstrapped browser-like
  headers + cookies, then Instagram's own `web_profile_info` / GraphQL
  endpoints over plain HTTP (no browser, no Playwright, no tokens).
  Followers, bio, verification, and the 12 most
  recent posts with real likes, comments, timestamps and media types.
  Failures are honest errors (400 = handle does not exist, 503 = every
  provider blocked); simulated/demo data does not exist in this system.

**Competitor auto-discovery (works for every account):** the agent reads
Instagram's own related-accounts signal for the handle (30 candidates).
If an account has no related-accounts data, it falls back to keyword
search over Instagram users, with short query terms derived from the
account's bio, name and username — so any account gets competitors.
Candidates are ranked by topical relevance and follower scale, the 5–10
most relevant are selected (LangChain chain when an LLM key is set;
deterministic scoring otherwise), then fetched and analyzed with real
data. Discovery usually costs no extra actor run — related profiles are
captured during the main profile fetch.
The metrics engine, the comparison logic, and the whole UI are fully
functional on real data at all times.

**What the "AI" part is:** `backend/ai_engine.py` computes real statistics
(engagement rate, posting frequency, top hashtags, best-performing content
type) and then generates natural-language analysis two ways:
- The default provider is **OpenRouter** (`LLM_BASE_URL=https://openrouter.ai/api/v1`,
  default model `google/gemma-4-31b-it:free` with free-tier failovers).
  Any OpenAI-compatible endpoint works via `LLM_BASE_URL` / `LLM_MODEL`
  (including NVIDIA NIM with an `nvapi-...` key). LangChain chains write
  the per-profile reports, pick the most relevant competitors from the
  candidate pool, and produce the market research as structured JSON.
- If no key is set, a rule-based engine produces the same shape of output
  from the same metrics.

## 2. Project structure

```
insta-intel-agent/
├── backend/
│   ├── main.py          FastAPI app: /api/analyze, /api/discover,
│   │                    /api/competitor-research, /api/compare
│   ├── scraper.py        Data layer (Graph API / Apify / keyless direct
│   │                     Instagram HTTP, competitor discovery via related accounts)
│   ├── ai_engine.py       LangChain chains: insights, competitor
│   │                     selection, market research (+ rule fallbacks)
│   ├── models.py          Pydantic schemas
│   ├── requirements.txt
│   ├── Dockerfile
│   └── .env.example
├── frontend/
│   ├── src/
│   │   ├── App.jsx
│   │   ├── index.css
│   │   └── components/ (ProfileReadout, RankingBars, EngagementChart, Report)
│   ├── package.json
│   ├── vite.config.js
│   ├── Dockerfile
│   └── nginx.conf
├── docker-compose.yml
└── README.md   (this file)
```

## 3. Run it locally (fastest path — no Docker needed)

**One-click scripts (Windows / Git Bash):**

| Script | What it does |
|---|---|
| `start.bat` / `start.sh` | Installs dependencies if missing, starts backend + frontend, waits, health-checks both, opens the browser. Safe to re-run — already-running services are skipped. |
| `stop.bat` / `stop.sh` | Stops both services by port (8000, 5173). |

Logs land in `backend/uvicorn.log` and `frontend/vite.log`.

**Manual setup:**

**Backend:**
```bash
cd backend
python -m venv venv && source venv/bin/activate   # optional but recommended
pip install -r requirements.txt
cp .env.example .env      # set APIFY_TOKEN=... for real data (see .env.example)
uvicorn main:app --reload --port 8000
```
Backend is now live at `http://localhost:8000`. Check `http://localhost:8000/health`.

**Frontend (new terminal):**
```bash
cd frontend
npm install
echo "VITE_API_URL=http://localhost:8000" > .env.local
npm run dev
```
Open `http://localhost:5173`. Type any handle (e.g. `glowbeauty.co`) or
paste a full profile URL (`https://instagram.com/glowbeauty.co`) and click
**Run analysis**, or switch to **Compare vs competitors** and add a couple
more handles.

Note: the first live fetch takes 10–60s while the Apify actor runs (the
frontend shows "Scanning…"). Results are cached for 30 minutes per handle,
so repeat analyses and compare-mode refetches are instant and free.

## 4. Run it with Docker (one command)

```bash
docker compose up --build
```
- Frontend: `http://localhost:3000`
- Backend: `http://localhost:8000`

Set `APIFY_TOKEN` before running (`export APIFY_TOKEN=apify_api_...`) so
the backend can fetch real Instagram data — without it, live-mode requests
fail with a clear error. Set `LLM_API_KEY` if you want LLM-generated
summaries instead of the rule-based ones — everything else works
identically without it.

## 5. Deploying it for free (so you have a live demo link)

**Backend → Render.com (free tier):**
1. Push this repo to GitHub.
2. On Render: New → Web Service → connect the repo, set root directory to `backend`.
3. Build command: `pip install -r requirements.txt`
4. Start command: `uvicorn main:app --host 0.0.0.0 --port $PORT`
5. Add env vars `LLM_API_KEY` (optional) and `APIFY_TOKEN` (optional — mark as secret).
6. Deploy. You'll get a URL like `https://your-app.onrender.com`.

**Recommended for Render: set `IG_PROXY_URL`.** The keyless fetch layer is
pure HTTP (no browser), but Instagram hard-blocks datacenter IPs
(Render/Railway/Fly get 401/429 on every call). Setting `IG_PROXY_URL`
(e.g. a Webshare/IPRoyal residential proxy) routes every Instagram request
through it and restores live fetching. Locally, no proxy is needed.

**Free token-free alternative: your own Cloudflare Worker relay.** Deploy
`infra/ig-relay-worker/` (one command: `npx wrangler deploy`, free tier =
100k req/day) and set the backend env
`IG_RELAY_URL=https://ig-relay.<you>.workers.dev`. The fetch ladder tries
it FIRST whenever Instagram throttles the host's IP — see
`infra/ig-relay-worker/README.md`.

**Frontend → Vercel or Netlify (free tier):**
1. New project → import the repo, set root directory to `frontend`.
2. Build command: `npm run build`, output directory: `dist`.
3. Add env var `VITE_API_URL=https://your-app.onrender.com` (your Render URL from above).
4. Deploy. You'll get a URL like `https://your-app.vercel.app`.

That's a complete, publicly-reachable live demo with two free-tier services.

**Alternative — single-host deploy (Railway/a VPS):** just run `docker
compose up --build -d` on any machine with Docker; both services come up
together, and you put a reverse proxy (Caddy/Nginx) in front if you want
one domain for both.

## 6. How this AI agent was actually built — the process, step by step

If you're asked to explain how you built this (interview, viva, PR
description), this is the real build process, in order:

1. **Scope the problem honestly.** Realized "scrape any Instagram
   competitor" isn't something the official API allows, so designed the
   data layer as a provider ladder from day one (`get_profile()` tries
   Graph API → Apify → keyless direct Instagram HTTP before failing with an
   honest error). This is a legitimate, common pattern in production
   systems that depend on third-party data.

2. **Design the schema first.** Wrote `models.py` (Pydantic) defining
   `ProfileData`, `Post`, `ProfileMetrics`, `ProfileInsight` — this is the
   contract every other layer builds against, so backend and frontend can
   be developed in parallel against a fixed shape.

3. **Build the metrics engine.** `compute_metrics()` in `ai_engine.py`
   turns raw post data into engagement rate, posting cadence, top
   hashtags, and best content format — all deterministic, explainable
   math, no AI needed here. This matters because a good AI agent grounds
   its language in real numbers instead of hallucinating them.

4. **Layer the AI on top of the metrics, not instead of them.** The LLM
   (or the rule-based fallback) is only ever asked to *narrate* numbers
   that were already computed — it never invents the underlying stats.
   This is the core design principle that keeps the "AI agent" trustworthy.

5. **Build the comparison/ranking logic.** `composite_score()` and
   `build_market_summary()` turn several individual analyses into a
   relative ranking and a list of competitive gaps — this is what makes it
   a *competitor intelligence* tool rather than just a single-profile
   analyzer.

6. **Expose it via a clean REST API.** `main.py` — two endpoints
   (`/api/analyze`, `/api/compare`), FastAPI auto-generates interactive
   docs at `/docs` for free, which is worth showing off in a demo.

7. **Build the UI to match the domain.** The frontend treats this as an
   analyst's dashboard rather than a generic SaaS template — data-dense
   readouts, a ranked bar comparison, and a written report, styled with a
   dark "intelligence report" aesthetic rather than default rounded cards.

8. **Containerize and document.** Dockerfiles for both services plus
   `docker-compose.yml` so the whole thing runs identically on any machine,
   and this README so someone else (or future-you) can pick it up cold.

## 7. Extending it further

- Live mode already ships via Apify; to use a different provider, replace
  `_run_actor()` / `_fetch_live_profile()` in `backend/scraper.py` —
  everything downstream (metrics, LangChain chains, API, UI) stays unchanged.
- Swap any LangChain chat model into `_get_llm()` in `ai_engine.py`
  (Anthropic, Groq, Ollama, ...) — the three chains are model-agnostic.
- Add a persistence layer (SQLAlchemy is already in `requirements.txt`) to
  save analysis history per user and chart trends over time.
- Add scheduled re-analysis (e.g. APScheduler or a cron job hitting
  `/api/analyze`) to track an account's metrics week over week.
- Swap the rule-based fallback's templates for a fine-tuned prompt if you
  want more stylistic control over the AI-generated report.
