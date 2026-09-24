"""InstaIQ — Streamlit front-end for the FastAPI agent (full parity with the React UI).

Everything the React app shows lives here, powered by the same endpoints:
  Scanner      — inline @handle input + "Do everything" (calls /api/growth-plan — the full pipeline)
  Hero card    — score ring, handle, badge, bio (React .dash-hero)
  Dashboard    — stat blocks, score drivers/drainers, AI report
  Growth plan  — summary, pillars, post ideas, weekly schedule, format mix, hashtag sets
  Timing       — best slots, weekday×hour cadence heatmap, reel timing
  Toolkit      — bio rewrite, hashtag research, hashtag suggestions
  Trends       — trending formats/topics + alerts + ready-to-make suggestions
  Deep intel   — audience, trending topics, rival content, hooks, top content, rival growth
  Deep dives   — competitor research, whitespace & captions, monthly review
  History      — recent searches, restore, forget, JSON/PDF export
  Chat         — analyst chat, live-grounded when the question mentions a handle

The UI talks to the FastAPI backend at BACKEND_URL (env, default http://127.0.0.1:8000).
Run: streamlit run streamlit_app.py  (backend must be on :8000)
"""
import json
import os
import threading
import time
from datetime import datetime

import httpx
import pandas as pd
import streamlit as st

# Backend URL resolution order: env var → Streamlit secrets (cloud deploy) →
# local default. st.secrets raises when no secrets file exists, hence the guard.
def _backend_url() -> str:
    url = os.getenv("BACKEND_URL", "").strip()
    if not url:
        try:
            url = str(st.secrets.get("BACKEND_URL", "")).strip()
        except Exception:
            url = ""
    return (url or "http://127.0.0.1:8000").rstrip("/")

BACKEND = _backend_url()
UA = {"User-Agent": "InstaIQ-Streamlit/1.0"}

st.set_page_config(
    page_title="InstaIQ — AI Instagram Intelligence Agent",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="collapsed",
)

# ----------------------------------------------------------------------------
# Design system — mirrors the React UI (frontend/src/index.css)
# ----------------------------------------------------------------------------
_CSS = """
@import url('https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@500;600;700&family=Inter:wght@400;500;600;700&display=swap');

:root {
  --ink: #15171A; --panel: #1D2023; --panel-raised: #23272B;
  --hairline: #33383D; --paper: #ECEAE4; --paper-dim: #9CA1A6;
  --signal: #C6FF4E; --signal-dim: #7C9A3A; --alert: #FF5C72;
  --font-display: 'Space Grotesk', sans-serif;
  --font-body: 'Inter', sans-serif;
}

html, body, .stApp {
  background: var(--ink) !important;
  color: var(--paper);
  font-family: var(--font-body) !important;
  font-feature-settings: 'tnum' 1;
}
::selection { background: var(--signal); color: var(--ink); }
h1, h2, h3, h4 { font-family: var(--font-display) !important; font-weight: 600; }
hr { border-color: var(--hairline) !important; opacity: 1; }

#MainMenu, footer { visibility: hidden; }
header[data-testid="stHeader"] { background: transparent; }
.block-container { padding: 2.2rem 2.4rem 5rem; max-width: 1240px; }

/* No sidebar — the React app is one full-width column */
section[data-testid="stSidebar"],
[data-testid="stSidebarCollapsedControl"] { display: none !important; }

/* Scanner row: input + lime button share one visual line */
[data-testid="stTextInput"] input {
  background: var(--panel-raised) !important;
  border: 1px solid var(--hairline) !important;
  border-radius: 3px !important;
  color: var(--paper) !important;
  min-height: 46px; font-size: 15px;
}
[data-testid="stTextInput"] input:focus {
  border-color: var(--signal) !important;
  box-shadow: 0 0 0 3px rgba(198, 255, 78, 0.08) !important;
}
[data-testid="stBaseButton-primary"] { min-height: 46px; }

/* Metrics = React .stat blocks */
[data-testid="stMetric"] {
  border-left: 2px solid var(--hairline);
  padding-left: 14px;
}
[data-testid="stMetricValue"] {
  font-family: var(--font-display) !important;
  font-size: 27px !important;
  font-weight: 600; line-height: 1.1;
  font-variant-numeric: tabular-nums;
}
[data-testid="stMetricLabel"] p { color: var(--paper-dim) !important; font-size: 12.5px !important; }

/* Tabs = React dash-nav pill row */
[data-testid="stTabs"] [role="tablist"] { gap: 6px; border-bottom: 1px solid var(--hairline); }
[data-testid="stTabs"] button {
  color: var(--paper-dim) !important;
  font-weight: 600; font-size: 13.5px;
}
[data-testid="stTabs"] button[aria-selected="true"] {
  color: var(--paper) !important;
  border-bottom: 2px solid var(--signal) !important;
}

/* Buttons */
.stButton > button, .stDownloadButton > button {
  border-radius: 3px; font-weight: 600;
}
button[kind="primary"], .stDownloadButton > button {
  background: var(--signal) !important; color: var(--ink) !important; border: none !important;
}
button[kind="primary"]:hover { filter: brightness(1.08); color: var(--ink) !important; }
button[kind="secondary"] {
  background: var(--panel-raised) !important; color: var(--paper) !important;
  border: 1px solid var(--hairline) !important;
}
button[kind="secondary"]:hover { border-color: var(--signal-dim) !important; color: var(--signal) !important; }

/* Panels */
[data-testid="stExpander"] {
  border: 1px solid var(--hairline) !important;
  border-radius: 8px !important; background: var(--panel);
}
[data-testid="stDataFrame"] { border: 1px solid var(--hairline); border-radius: 6px; }
[data-testid="stChatMessage"] {
  background: var(--panel); border: 1px solid var(--hairline); border-radius: 8px;
}
div[data-testid="stAlert"] { border-radius: 6px; border: 1px solid var(--hairline); }
[data-testid="stCaptionContainer"], [data-testid="stWidgetLabel"] p { color: var(--paper-dim) !important; }

/* Hero + dashboard hero card (React .hero / .dash-hero) */
.hero-eyebrow {
  display: flex; align-items: center; gap: 10px;
  color: var(--signal); font-weight: 600; font-size: 13px;
  letter-spacing: 0.02em; margin: 10px 0 18px;
}
.hero-eyebrow .dot {
  width: 7px; height: 7px; border-radius: 50%; background: var(--signal);
  box-shadow: 0 0 0 3px rgba(198,255,78,0.15);
  animation: rp-pulse 2.6s ease-in-out infinite;
}
.hero-title {
  font-family: var(--font-display); font-size: clamp(32px, 5vw, 50px);
  line-height: 1.08; font-weight: 600; margin: 0 0 16px; max-width: 20ch;
}
.hero-sub {
  color: var(--paper-dim); font-size: 16px; line-height: 1.6;
  max-width: 58ch; margin: 0 0 10px;
}
.dash-hero {
  display: flex; gap: 36px; align-items: center; flex-wrap: wrap;
  padding: 32px 28px; margin: 4px 0 20px;
  background:
    radial-gradient(1200px 400px at 85% -60%, rgba(198, 255, 78, 0.06), transparent 60%),
    linear-gradient(180deg, var(--panel-raised), var(--panel));
  border: 1px solid var(--hairline); border-radius: 18px;
}
.dash-hero .handle {
  font-family: var(--font-display); font-size: clamp(26px, 3.4vw, 34px);
  font-weight: 600; margin: 0 0 4px; display: flex; align-items: center;
  gap: 10px; flex-wrap: wrap;
}
.badge {
  font-size: 11px; background: var(--signal); color: var(--ink);
  padding: 3px 9px; border-radius: 999px; font-weight: 700; letter-spacing: 0.03em;
}
.dash-hero .category { color: var(--signal); font-size: 13px; font-weight: 600; margin: 2px 0 10px; }
.dash-hero .bio { color: var(--paper-dim); font-size: 14px; line-height: 1.6; margin: 0; max-width: 60ch; }
.ring-num {
  position: absolute; top: 30px; left: 0; width: 120px; text-align: center;
  font-family: var(--font-display); font-size: 30px; font-weight: 700;
}
.ring-num span { font-size: 13px; color: var(--paper-dim); font-weight: 500; }
.ring-label {
  margin-top: 4px; font-size: 10.5px; letter-spacing: 0.06em;
  text-transform: uppercase; color: var(--paper-dim); text-align: center;
}

/* Research/analysis progress — mirrors React ResearchProgress.jsx */
.rp-root { margin: 6px 0 14px; }
.rp-line { display: flex; align-items: center; gap: 10px; margin: 0; color: var(--paper); font-size: 14px; }
.rp-dot {
  width: 7px; height: 7px; border-radius: 50%; background: var(--signal);
  box-shadow: 0 0 0 3px rgba(198,255,78,0.15);
  animation: rp-pulse 2.6s ease-in-out infinite; flex-shrink: 0; display: inline-block;
}
@keyframes rp-pulse {
  0%, 100% { box-shadow: 0 0 0 3px rgba(198, 255, 78, 0.15); }
  50%      { box-shadow: 0 0 0 6px rgba(198, 255, 78, 0.07); }
}
.rp-elapsed { color: var(--paper-dim); font-size: 12px; font-variant-numeric: tabular-nums; }
.rp-track {
  max-width: 420px; height: 3px; background: var(--hairline);
  border-radius: 2px; overflow: hidden; margin-top: 10px;
}
.rp-fill { height: 100%; background: var(--signal); border-radius: 2px; transition: width 1.2s ease; }
.rp-note { color: var(--paper-dim); font-size: 11.5px; margin: 6px 0 0; max-width: 420px; }
"""

st.markdown(f"<style>{_CSS}</style>", unsafe_allow_html=True)

# ----------------------------------------------------------------------------
# HTTP helpers (single shared client; honest error surfacing)
# ----------------------------------------------------------------------------
_client = httpx.Client(base_url=BACKEND, timeout=httpx.Timeout(600.0), headers=UA)


def api_get(path: str, params: dict | None = None):
    r = _client.get(path, params=params or {})
    if r.status_code >= 400:
        detail = ""
        try:
            detail = r.json().get("detail", "")
        except Exception:
            detail = r.text[:160]
        raise RuntimeError(f"HTTP {r.status_code}: {detail}")
    return r.json()


def api_post(path: str, body: dict, params: dict | None = None):
    r = _client.post(path, json=body, params=params or {})
    if r.status_code >= 400:
        try:
            detail = r.json().get("detail", "")
        except Exception:
            detail = r.text[:160]
        raise RuntimeError(f"HTTP {r.status_code}: {detail}")
    return r.json()


# ----------------------------------------------------------------------------
# Session state
# ----------------------------------------------------------------------------
for _k, _v in {
    "dash": None,            # full GrowthPlanResponse dict (the "Do everything" pipeline)
    "handle": "",            # lowercase handle the dashboard belongs to
    "chat": [],              # [(role, text)]
    "confirm_delete": None,  # handle pending a "forget" confirmation
    "busy": {},              # deep-dive -> True while its request runs
    "research": None,
    "whitespace": None,
    "review": None,
}.items():
    st.session_state.setdefault(_k, _v)


# ----------------------------------------------------------------------------
# Background runner + React-style progress (ResearchProgress parity)
# ----------------------------------------------------------------------------
RP_STAGES = [
    "Fetching the profile with real data…",
    "Discovering related accounts in this niche…",
    "Selecting the most relevant competitors…",
    "Researching all competitors in one batch…",
    "Computing engagement metrics…",
    "Writing the intelligence report…",
]


def _run_with_progress(label: str, fn, *args):
    """Run fn in a thread; animate the React ResearchProgress while it works."""
    result: dict = {"value": None, "error": None, "done": False}
    t = threading.Thread(target=lambda: _worker(result, fn, *args), daemon=True)
    t.start()

    root = st.container()
    with root:
        st.markdown('<div class="rp-root">', unsafe_allow_html=True)
        line = st.empty()
        bar = st.empty()
        note = st.empty()
        note.markdown('<p class="rp-note">Live data takes 30–90s the first time — results are cached afterwards.</p>', unsafe_allow_html=True)

    t0 = time.time()
    i = 0
    while t.is_alive():
        stage = RP_STAGES[min(i, len(RP_STAGES) - 1)]
        pct = ((min(i, len(RP_STAGES) - 1) + 1) / len(RP_STAGES)) * 100
        line.markdown(
            f'<div class="rp-line"><span class="rp-dot"></span><span>{label or stage}</span>'
            f'<span class="rp-elapsed">{int(time.time() - t0)}s</span></div>',
            unsafe_allow_html=True,
        )
        bar.markdown(
            f'<div class="rp-track"><div class="rp-fill" style="width:{pct:.0f}%"></div></div>',
            unsafe_allow_html=True,
        )
        i += 1
        time.sleep(6)
    if result["error"]:
        st.error(result["error"])
        return None
    line.empty(); bar.empty(); note.empty()
    return result["value"]


def _worker(result: dict, fn, *args):
    try:
        result["value"] = fn(*args)
    except Exception as e:
        result["error"] = str(e)
    finally:
        result["done"] = True


def run_analysis(raw: str) -> None:
    h = (raw or "").strip().lstrip("@").strip()
    if not h:
        st.warning("Enter a handle first.")
        return
    def _call():
        return api_post("/api/growth-plan", {"username": h}, params={"count": 4})
    dash = _run_with_progress("", _call)
    if dash:
        st.session_state.dash = dash
        st.session_state.handle = dash["main"]["profile"]["username"]
        for k in ("research", "whitespace", "review"):
            st.session_state.pop(k, None)
        st.toast(f"@{st.session_state.handle} loaded", icon="✅")
        st.rerun()


# ----------------------------------------------------------------------------
# Render helpers
# ----------------------------------------------------------------------------
def _km(n) -> str:
    try:
        n = float(n)
    except (TypeError, ValueError):
        return "—"
    for div, suf in ((1_000_000_000, "B"), (1_000_000, "M"), (1_000, "K")):
        if abs(n) >= div:
            return f"{n / div:.1f}{suf}"
    return f"{int(n):,}"


def _posts_df(profile: dict) -> pd.DataFrame:
    rows = []
    for p_ in profile.get("recent_posts", []) or []:
        rows.append({
            "type": p_.get("media_type") or "?",
            "likes": p_.get("likes", 0),
            "comments": p_.get("comments", 0),
            "days ago": p_.get("posted_days_ago", 0),
            "caption": (p_.get("caption") or "")[:90],
        })
    return pd.DataFrame(rows, columns=["type", "likes", "comments", "days ago", "caption"])


def _section_label(text: str):
    st.markdown(f'<p style="font-family:var(--font-display);font-size:12px;letter-spacing:0.14em;text-transform:uppercase;color:var(--paper-dim);margin:28px 0 6px;">{text}</p>', unsafe_allow_html=True)


def _render_insight(ins: dict, show_posts: bool = True):
    """Compact readout for one insight dict (main or rival) — React ProfileReadout parity."""
    p_, m_ = ins["profile"], ins["metrics"]
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Followers", _km(p_.get("followers")))
    c2.metric("Engagement", f"{m_.get('engagement_rate', 0)}%")
    c3.metric("Avg likes", _km(m_.get("avg_likes")))
    c4.metric("Cadence", f"{m_.get('posting_frequency_per_week', 0)}/wk")
    if ins.get("ai_summary"):
        st.write(ins["ai_summary"])
    if show_posts:
        df = _posts_df(p_)
        if not df.empty:
            st.dataframe(df, use_container_width=True, hide_index=True)


def _render_timing(d: dict):
    bt, cm, rt = d.get("best_times"), d.get("cadence_map"), d.get("reel_timing")
    if bt:
        st.markdown("#### Best time slots")
        st.write(bt.get("summary") or "")
        rows = [{
            "day": s.get("day"), "hour (UTC)": f"{s.get('hour')}:00",
            "avg engagement": round(s.get("avg_engagement", 0)),
            "samples": s.get("samples"),
        } for s in (bt.get("slots") or [])]
        if rows:
            st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
    if cm:
        st.markdown("#### Cadence map — weekday × hour (UTC)")
        st.write(cm.get("summary") or "")
        heat = cm.get("heatmap") or []
        if heat:
            dfh = pd.DataFrame([{"day": c["day"], **{f"{c2['hour']}": c2["engagement"] for c2 in heat if c2["day"] == c["day"]}} for c in heat])
            dfh = dfh.loc[:, ~dfh.columns.duplicated()].set_index("day")
            st.dataframe(dfh, use_container_width=True)
        st.caption(
            f"Posts/week {cm.get('posts_per_week', 0)} · sample {cm.get('sample_days', 0)} days · "
            f"active days: {', '.join(cm.get('active_days') or []) or '—'}"
        )
    if rt:
        st.markdown("#### Reel timing")
        st.write(rt.get("summary") or "")
        for s in rt.get("slots") or []:
            st.markdown(f"- **{s.get('day')} {s.get('hour')}:00 UTC** — {s.get('rationale', '')} _(rival activity: {s.get('competitor_activity', '?')})_")


def _render_toolkit(d: dict):
    bio, hr, hs = d.get("bio"), d.get("hashtags"), d.get("hashtag_suggestions")
    if bio:
        st.markdown("#### Bio optimizer")
        st.markdown(f"**Current:** {bio.get('current_bio', '')}")
        st.markdown(f"**Suggested:** {bio.get('suggested_bio', '')}")
        for n_ in bio.get("notes") or []:
            st.markdown(f"- {n_}")
    if hr:
        st.markdown("#### Hashtag research")
        st.write(hr.get("summary") or "")
        st.markdown(" ".join(f"`#{t['name']}`" for t in (hr.get("tiered") or [])[:24]))
    if hs:
        st.markdown("#### Hashtag suggestions (likes & comments)")
        st.write(hs.get("summary") or "")
        rows = [{
            "hashtag": s.get("hashtag"), "impact": s.get("estimated_impact"),
            "best for": s.get("best_for"), "tier": s.get("tier"), "why": s.get("reason"),
        } for s in hs.get("suggestions") or []]
        if rows:
            st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
        if hs.get("post_set"):
            st.markdown("**Post set:** " + " ".join(f"`#{h}`" for h in hs["post_set"]))
        if hs.get("reel_set"):
            st.markdown("**Reel set:** " + " ".join(f"`#{h}`" for h in hs["reel_set"]))
        for n_ in hs.get("notes") or []:
            st.caption(n_)


def _render_trends(tr: dict):
    if not tr:
        st.caption("No trend signals detected for this account.")
        return
    if tr.get("alerts"):
        st.markdown("#### Trend alerts")
        for a in tr["alerts"]:
            t = a.get("trend") or {}
            with st.expander(f"🔥 {t.get('name', '?')} — {t.get('category', '')}"):
                st.markdown(f"**Why relevant:** {a.get('why_relevant', '')}")
                st.markdown(f"**Post idea:** {a.get('suggested_post_idea', '')}")
                st.markdown(f"**Reel idea:** {a.get('suggested_reel_idea', '')}")
                st.markdown(f"**Caption hook:** _{a.get('caption_hook', '')}_")
                st.markdown(" ".join(f"`#{h}`" for h in a.get("recommended_hashtags") or []))
                st.markdown(f"**Tip:** {a.get('posting_tip', '')}")
    if tr.get("suggestions"):
        st.markdown("#### Ready-to-make trend plays")
        for s in tr["suggestions"]:
            with st.expander(f"{s.get('title', '?')} · {s.get('format', 'reel')}"):
                st.markdown(f"{s.get('concept', '')}")
                st.markdown(f"**Caption:** _{s.get('caption', '')}_")
                st.markdown(" ".join(f"`#{h}`" for h in s.get("hashtags") or []))
    if tr.get("trending"):
        st.markdown("#### Active trends")
        for t in tr["trending"]:
            st.markdown(f"- **{t.get('name')}** ({t.get('category')}) — {t.get('description', '')}")


def _render_intel(intel: dict):
    if not intel:
        st.caption("Deep intel unavailable.")
        return
    if intel.get("audience"):
        st.markdown("#### Audience read (inferred)")
        a = intel["audience"]
        st.write(a.get("summary") or "")
        st.caption(a.get("audience_profile") or "")
        if a.get("active_hours"):
            st.markdown(" ".join(f"**{h.get('window', '?')}**" for h in a["active_hours"][:6]))
    if intel.get("trending_topics"):
        tt = intel["trending_topics"]
        st.markdown("#### Trending topics in this niche")
        st.write(tt.get("summary") or "")
        for t in tt.get("topics") or []:
            st.markdown(f"- **{t.get('topic', '?')}** — {t.get('why_now', '')}")
    if intel.get("rival_content"):
        rc = intel["rival_content"]
        st.markdown("#### Rival content analysis")
        st.write(rc.get("summary") or "")
        for r in rc.get("rivals") or []:
            st.markdown(f"- **@{r.get('username')}** · ER {r.get('engagement_rate', 0)}% · signature: {r.get('signature_theme', '?')}")
    if intel.get("hooks"):
        hk = intel["hooks"]
        st.markdown("#### Viral hook patterns")
        st.write(hk.get("summary") or "")
        for h in hk.get("hooks") or []:
            st.markdown(f"- _\"{h.get('hook', '')}\"_ — proven by **{h.get('source', '?')}** ({h.get('examples', 0)} examples)")
    if intel.get("top_content"):
        tc = intel["top_content"]
        st.markdown("#### Your top content (real ranking)")
        st.write(tc.get("summary") or "")
        for it in tc.get("items") or []:
            st.markdown(f"- **#{it.get('rank')}** [{it.get('media_type')}] _\"{(it.get('caption') or '')[:80]}\"_ — {it.get('likes', 0):,} likes")
    if intel.get("rival_growth"):
        rg = intel["rival_growth"]
        st.markdown("#### Rival growth tracking")
        st.write(rg.get("summary") or "Tracked across stored scans.")


def _render_whitespace(w: dict):
    if not w:
        return
    st.markdown("### Whitespace & captions")
    if w.get("summary"):
        st.write(w["summary"])
    if w.get("whitespace"):
        st.markdown("**Whitespace areas** (themes rivals own, you don't):")
        for a in w["whitespace"]:
            st.markdown(f"- **{a.get('theme', '?')}** — {a.get('why_open', '')}")
    if w.get("captions"):
        st.markdown("**Ready-to-post captions:**")
        for c in w["captions"]:
            with st.expander(f"{c.get('theme', '?')} · {c.get('format', 'reel')}"):
                st.markdown(f"_{c.get('caption', '')}_")
                st.markdown(" ".join(f"`#{h}`" for h in c.get("hashtags") or []))


def _render_review(rv: dict):
    if not rv:
        return
    st.markdown("### Monthly review")
    st.caption(f"{rv.get('scan_count', 0)} scans over {rv.get('span_days', 0)} days")
    if rv.get("summary"):
        st.write(rv["summary"])
    rows = [{
        "metric": x.get("label"), "first": round(x.get("first_value", 0), 2),
        "latest": round(x.get("last_value", 0), 2),
        "change": f"{x.get('change', 0):+g}",
        "change %": f"{x.get('change_pct', 0):+g}%",
    } for x in rv.get("metrics") or []]
    if rows:
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
    if rv.get("recommendations"):
        st.markdown("**Recommendations:**")
        for s in rv["recommendations"]:
            st.markdown(f"- {s}")


def _render_research(res: dict):
    st.markdown("### Competitive ranking")
    if res.get("selection_rationale"):
        st.caption(res["selection_rationale"])
    ranking = res.get("ranking") or []
    all_ins = [res.get("main")] + (res.get("competitors") or [])
    max_er = max((i["metrics"].get("engagement_rate") or 0) for i in all_ins if i) or 1
    for u in ranking:
        ins_i = next((i for i in all_ins if i and i["profile"]["username"] == u), None)
        if not ins_i:
            continue
        er = ins_i["metrics"].get("engagement_rate") or 0
        you = u == res.get("main", {}).get("profile", {}).get("username")
        bar = "█" * max(2, int((er / max_er) * 28))
        st.markdown(
            f'<p style="font-family:var(--font-display);font-size:14px;margin:6px 0;">'
            f'<span style="color:{"var(--signal)" if you else "var(--paper-dim)"};">{bar}</span> '
            f'{"🫵 " if you else ""}@{u} · ER {er}%</p>',
            unsafe_allow_html=True,
        )
    if res.get("market_summary"):
        st.markdown("**Market summary**")
        st.write(res["market_summary"])
    if res.get("opportunities"):
        st.markdown("**Opportunities for you**")
        for s in res["opportunities"]:
            st.markdown(f"- {s}")
    for c in res.get("competitors") or []:
        with st.expander(f"@{c['profile']['username']} — {_km(c['profile'].get('followers'))} followers · ER {c['metrics'].get('engagement_rate')}%"):
            _render_insight(c)


def _deep_dive_button(key: str, title: str, sub: str, path: str, params: dict, store: str, render_fn):
    busy = bool(st.session_state.busy.get(key))
    if st.button(("⏳ " + title + "…") if busy else title, key=f"dd_{key}", use_container_width=True, disabled=busy):
        st.session_state.busy[key] = True

        def _call():
            return api_post(path, {"username": st.session_state.handle}, params=params)
        res = _run_with_progress("", _call)
        st.session_state[store] = res
        st.session_state.busy[key] = False
        st.rerun()
    st.caption(sub)
    if st.session_state.get(store):
        with st.container(border=True):
            render_fn(st.session_state[store])


# ----------------------------------------------------------------------------
# Landing / main render
# ----------------------------------------------------------------------------
if not st.session_state.dash:
    st.markdown(
        '<div class="hero-eyebrow"><span class="dot"></span> INSTAIQ · LIVE INSTAGRAM INTELLIGENCE</div>'
        '<div class="hero-title">Paste a profile. Get the full growth playbook.</div>'
        '<p class="hero-sub">One input — the agent pulls the account\'s real data, benchmarks it, '
        'and returns an AI-written strategy: what to post, when to post, which hashtags, '
        'bio rewrite, trend plays and competitor gaps.</p>',
        unsafe_allow_html=True,
    )
    lc1, lc2 = st.columns([0.68, 0.32], gap="small")
    with lc1:
        scan_in = st.text_input(
            "Instagram handle",
            key="scan_landing",
            placeholder="@handle — or paste a profile URL",
            label_visibility="collapsed",
        )
    with lc2:
        st.write("")  # align button with the 46px input
        if st.button("Do everything", type="primary", use_container_width=True):
            run_analysis(scan_in)

    try:
        ai = api_get("/api/ai-status")
        if not ai.get("available"):
            st.caption("⚙ AI provider unavailable — answers will use the rule-based engine.")
    except Exception:
        st.caption(
            "⚠ Backend offline. If you're viewing this on Streamlit Cloud: open the app's "
            "**Settings → Secrets** and add  BACKEND_URL = \"https://instaiq-api.vercel.app\"  "
            "(then reboot the app). Locally: start the FastAPI backend with start.bat."
        )
    st.stop()

dash = st.session_state.dash
main_ins = dash["main"]
p, m = main_ins["profile"], main_ins["metrics"]

# Compact re-scan row + hero card
dc1, dc2 = st.columns([0.72, 0.28], gap="small")
with dc1:
    dash_scan = st.text_input(
        "Analyze another handle",
        key="scan_dash",
        placeholder="@ analyze another handle",
        label_visibility="collapsed",
    )
with dc2:
    if st.button("Run analysis", type="primary", use_container_width=True):
        run_analysis(dash_scan)

score_val = dash.get("main", {}).get("account_score", 0) or 0
try:
    score_val = max(0, min(100, int(score_val)))
except (TypeError, ValueError):
    score_val = 0
_ring = 2 * 3.14159265 * 52
_dash_offset = _ring * (1 - score_val / 100)
_verified_badge = '<span class="badge">✔ VERIFIED</span>' if p.get("is_verified") else ""
_age_warn = (
    f'<div class="hero-sub" style="color: var(--alert); font-size: 12.5px;">⚠ aged data ({p.get("data_age_hours")}h)</div>'
    if p.get("data_age_hours") is not None
    else ""
)
st.markdown(
    f'<div class="dash-hero">'
    f'<div style="position:relative;width:120px;flex-shrink:0;">'
    f'<svg width="120" height="120" style="transform:rotate(-90deg);">'
    f'<circle cx="60" cy="60" r="52" fill="none" stroke="#23272B" stroke-width="10"/>'
    f'<circle cx="60" cy="60" r="52" fill="none" stroke="#C6FF4E" stroke-width="10" '
    f'stroke-linecap="round" stroke-dasharray="{_ring:.1f}" stroke-dashoffset="{_dash_offset:.1f}"/>'
    f'</svg>'
    f'<div class="ring-num">{score_val}<span>/100</span></div>'
    f'<div class="ring-label">SCORE</div>'
    f'</div>'
    f'<div style="flex:1;min-width:260px;">'
    f'<div class="handle">@{p["username"]} {_verified_badge}</div>'
    f'<div class="category">{p.get("category") or "—"}</div>'
    f'<p class="bio">{(p.get("bio") or "")[:220]}</p>'
    f'{_age_warn}'
    f'</div>'
    f'</div>',
    unsafe_allow_html=True,
)

if dash.get("score_explanation"):
    se = dash["score_explanation"]
    parts = [f"▲ {d}" for d in (se.get("drivers") or [])] + [f"▼ {d}" for d in (se.get("drainers") or [])]
    if parts:
        st.caption(" · ".join(parts))

# Stat blocks
c1, c2, c3, c4 = st.columns(4)
c1.metric("Followers", _km(p.get("followers")))
c2.metric("Engagement", f"{m.get('engagement_rate', 0)}%")
c3.metric("Avg likes", _km(m.get("avg_likes")))
c4.metric("Cadence", f"{m.get('posting_frequency_per_week', 0)}/wk")

for w_ in dash.get("warnings") or []:
    st.warning(w_)

tab_dash, tab_timing, tab_tool, tab_trends, tab_intel, tab_dives, tab_hist, tab_chat = st.tabs(
    ["📈 Dashboard", "⏰ Timing", "🧰 Toolkit", "🌊 Trends",
     "🕵 Deep intel", "🔬 Deep dives", "🕘 History", "💬 Chat"]
)

# --- Dashboard ---------------------------------------------------------------
with tab_dash:
    st.subheader("AI intelligence report")
    st.write(main_ins.get("ai_summary") or "—")
    a, b = st.columns(2)
    with a:
        st.markdown("**💪 Strengths**")
        for s in main_ins.get("strengths", []):
            st.markdown(f"- {s}")
    with b:
        st.markdown("**⚠️ Weaknesses**")
        for s in main_ins.get("weaknesses", []):
            st.markdown(f"- {s}")
    st.markdown("**🎯 Recommendations**")
    for s in main_ins.get("recommendations", []):
        st.markdown(f"- {s}")

    st.divider()
    st.markdown("##### Recent posts (real engagement)")
    df = _posts_df(p)
    if not df.empty:
        st.dataframe(df, use_container_width=True, hide_index=True)

    st.markdown("##### Growth tracking")
    try:
        gt = api_get("/api/growth-tracking", {"username": p["username"]})
        snaps = gt.get("snapshots") or []
        if len(snaps) >= 2:
            chart_df = pd.DataFrame(snaps)
            if "followers" in chart_df.columns:
                xcol = "day" if "day" in chart_df.columns else chart_df.index
                st.line_chart(chart_df.set_index("day")["followers"] if "day" in chart_df.columns else chart_df["followers"])
        else:
            st.caption("One scan so far — keep analyzing this handle to build a trend.")
    except Exception as e:
        st.caption(f"Growth data unavailable: {str(e)[:80]}")

# --- Timing ---------------------------------------------------------------
with tab_timing:
    _render_timing(dash)

# --- Toolkit ---------------------------------------------------------------
with tab_tool:
    _render_toolkit(dash)

# --- Trends ---------------------------------------------------------------
with tab_trends:
    _render_trends(dash.get("trends_result"))

# --- Deep intel ---------------------------------------------------------------
with tab_intel:
    _render_intel(dash.get("intel"))

# --- Deep dives ---------------------------------------------------------------
with tab_dives:
    c1, c2, c3 = st.columns(3)
    with c1:
        _deep_dive_button(
            "research", "Competitor research",
            "Auto-discover 5 rivals, rank you against them, list the gaps they own.",
            "/api/competitor-research", {"count": 5}, "research", _render_research,
        )
    with c2:
        _deep_dive_button(
            "whitespace", "Whitespace & captions",
            "Content themes you're missing + ready-to-post captions from your data.",
            "/api/whitespace", {"rivals": 0}, "whitespace", _render_whitespace,
        )
    with c3:
        _deep_dive_button(
            "review", "Monthly review",
            "Trajectory of followers, engagement and cadence across your scans.",
            "/api/review", {}, "review", _render_review,
        )

# --- History ---------------------------------------------------------------
with tab_hist:
    st.subheader("Recent searches")
    try:
        rows = api_get("/api/recent-searches", {"limit": 25}).get("searches", [])
        if not rows:
            st.caption("No searches recorded yet.")
        for r in rows:
            c1, c2, c3, c4 = st.columns([2, 2, 2, 2])
            c1.markdown(f"**@{r.get('username')}**")
            c2.caption(f"{_km(r.get('followers'))} followers · ER {r.get('engagement_rate')}%")
            c3.caption(str(r.get("last_scanned_at", ""))[:16].replace("T", " "))
            if c4.button("Restore", key=f"restore_{r.get('username')}"):
                try:
                    d2 = api_post("/api/growth-plan", {"username": r["username"]}, params={"count": 4})
                    st.session_state.dash = d2
                    st.session_state.handle = d2["main"]["profile"]["username"]
                    st.rerun()
                except Exception as e:
                    st.error(f"Restore failed: {e}")
            if c4.button("Forget", key=f"forget_{r.get('username')}"):
                st.session_state.confirm_delete = r["username"]
    except Exception as e:
        st.caption(f"History unavailable: {str(e)[:80]}")

    if st.session_state.get("confirm_delete"):
        victim = st.session_state.confirm_delete
        st.warning(f"Delete all stored scans for @{victim}?")
        yes, no = st.columns(2)
        if yes.button("Yes, delete", key="confirm_yes"):
            try:
                rr = _client.delete(f"/api/recent-searches/{victim}")
                rr.raise_for_status()
                st.session_state.confirm_delete = None
                st.toast(f"@{victim} forgotten", icon="🗑️")
                st.rerun()
            except Exception as e:
                st.error(f"Delete failed: {e}")
        if no.button("Cancel", key="confirm_confirm_no"):
            st.session_state.confirm_delete = None
            st.rerun()

    st.download_button(
        "⬇️ Download full analysis (JSON)",
        data=json.dumps(dash, indent=2, default=str),
        file_name=f"instaiq_{p['username']}_{datetime.now():%Y%m%d}.json",
        mime="application/json",
    )
    if st.button("⬇️ Export PDF report", type="secondary"):
        with st.spinner("Rendering PDF via headless Chromium…"):
            try:
                pdf_resp = _client.post("/api/export/pdf", json={"username": p["username"]}, params={"count": 0})
                pdf_resp.raise_for_status()
                st.download_button(
                    "Save PDF",
                    data=pdf_resp.content,
                    file_name=f"instaiq_{p['username']}_{datetime.now():%Y%m%d}.pdf",
                    mime="application/pdf",
                    key="pdf_download",
                )
            except Exception as e:
                st.error(f"PDF export failed: {e}")

# --- Chat ---------------------------------------------------------------
with tab_chat:
    st.subheader("Analyst chat")
    try:
        ai = api_get("/api/ai-status")
        icon = "🟢" if ai.get("available") else "🔴"
        st.caption(
            f"{icon} AI: {'ready' if ai.get('available') else 'rule-based fallback'} "
            f"· {ai.get('provider', '?')} · {ai.get('model', '?')}"
        )
    except Exception:
        pass
    st.caption("Ask about strategy — mention a @handle and the answer quotes its real numbers.")
    chat_box = st.container(height=380)
    with chat_box:
        for role, text in st.session_state.chat:
            with st.chat_message("user" if role == "user" else "assistant"):
                st.markdown(text)
    if prompt := st.chat_input("Ask about growth, content, competitors…"):
        st.session_state.chat.append(("user", prompt))
        with chat_box:
            with st.chat_message("user"):
                st.markdown(prompt)
        with chat_box:
            with st.chat_message("assistant"):
                with st.spinner("Thinking…"):
                    try:
                        resp = api_post(
                            "/api/chat",
                            {
                                "message": prompt,
                                "username": st.session_state.handle,
                                "context": (
                                    f"Account: @{p['username']} | {_km(p.get('followers'))} followers | "
                                    f"ER {m.get('engagement_rate')}% | avg likes {_km(m.get('avg_likes'))} | "
                                    f"posting {m.get('posting_frequency_per_week')}/wk | "
                                    f"best format: {m.get('best_content_type') or 'n/a'}"
                                ),
                            },
                        )
                        answer = resp.get("answer") or "(empty answer)"
                        if not resp.get("llm_used", True):
                            answer = "⚙ _[rule-based]_ " + answer
                        st.session_state.chat.append(("assistant", answer))
                        st.markdown(answer)
                    except Exception as e:
                        err = f"Chat failed: {e}"
                        st.session_state.chat.append(("assistant", err))
                        st.error(err)
