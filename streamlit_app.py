"""InstaIQ — Streamlit front-end for the FastAPI agent.

One app, four tabs:
  Dashboard   — metrics + AI narrative + posts table + growth chart
  Competitors — competitor research (LLM-picked rivals + market research)
  Chat        — analyst chat (live-grounded when the question mentions a handle)
  History     — recent searches, restore stored analyses, forget a handle

The UI talks to the FastAPI backend at BACKEND_URL (env, default
http://127.0.0.1:8000). Real Instagram data comes from the keyless
Playwright Chromium provider — no tokens, no relay, no login.
Run: streamlit run streamlit_app.py  (backend must be on :8000)
"""
import json
import os
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
    initial_sidebar_state="expanded",
)

# ----------------------------------------------------------------------------
# HTTP helpers (single shared client; honest error surfacing)
# ----------------------------------------------------------------------------
_client = httpx.Client(base_url=BACKEND, timeout=httpx.Timeout(300.0), headers=UA)


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
    "insight": None,        # full ProfileInsight dict for the current handle
    "handle": "",           # lowercase handle the insight belongs to
    "chat": [],             # [(role, text)]
    "confirm_delete": None  # handle pending a "forget" confirmation
}.items():
    st.session_state.setdefault(_k, _v)


# ----------------------------------------------------------------------------
# Sidebar: handle input + analyze + status
# ----------------------------------------------------------------------------
with st.sidebar:
    st.title("📊 InstaIQ")
    st.caption("AI Instagram intelligence — real data via headless Chromium, no login.")

    handle_in = st.text_input(
        "Instagram handle",
        value=st.session_state.handle,
        placeholder="e.g. nasa",
        help="Public account to analyze. Real data is fetched live by the backend.",
    )

    if st.button("🚀 Run analysis", type="primary", use_container_width=True):
        h = handle_in.strip().lstrip("@").strip()
        if not h:
            st.warning("Enter a handle first.")
        else:
            with st.spinner(f"Fetching real data for @{h} — first run takes ~30-60s…"):
                try:
                    insight = api_post("/api/analyze", {"username": h})
                    st.session_state.insight = insight
                    st.session_state.handle = insight["profile"]["username"]
                    st.session_state.pop("competitors", None)  # stale rivals
                    st.toast(f"@{st.session_state.handle} loaded", icon="✅")
                    st.rerun()
                except RuntimeError as e:
                    st.error(f"Analysis failed — {e}")
                except Exception as e:
                    st.error(f"Unexpected error: {type(e).__name__}: {e}")

    if st.session_state.insight:
        st.success(f"Loaded: @{st.session_state.handle}")

    st.divider()

    # --- Data / AI transparency ---
    try:
        usage = api_get("/api/usage")
        st.caption(
            f"Data mode: **{usage.get('data_mode', '?')}** · cached profiles: "
            f"{usage.get('cached_profiles', 0)}"
        )
    except Exception:
        st.caption("Backend offline — start it with start.sh")

    try:
        ai = api_get("/api/ai-status")
        icon = "🟢" if ai.get("available") else "🔴"
        st.caption(
            f"{icon} AI: {'ready' if ai.get('available') else 'rule-based fallback'} "
            f"({ai.get('provider', '?')} · {ai.get('model', '?')})"
        )
        if ai.get("last_error"):
            with st.expander("AI diagnostics"):
                st.caption(str(ai["last_error"])[:300])
    except Exception:
        pass

    st.divider()
    st.caption(
        "Real data: keyless Playwright Chromium reads Instagram's own "
        "web_profile_info JSON inside a real browser page."
    )


# ----------------------------------------------------------------------------
# Helpers
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
    for p in profile.get("recent_posts", []) or []:
        rows.append(
            {
                "type": p.get("media_type") or "?",
                "likes": p.get("likes", 0),
                "comments": p.get("comments", 0),
                "days ago": p.get("posted_days_ago", 0),
                "caption": (p.get("caption") or "")[:90],
            }
        )
    cols = ["type", "likes", "comments", "days ago", "caption"]
    return pd.DataFrame(rows, columns=cols)


# ----------------------------------------------------------------------------
# Main area
# ----------------------------------------------------------------------------
if not st.session_state.insight:
    st.title("📊 InstaIQ — AI Instagram Intelligence Agent")
    st.markdown(
        "Analyze any public Instagram account: real follower/engagement data "
        "fetched by a keyless headless-Chromium provider, plus AI-written "
        "growth strategy."
    )
    st.markdown(
        "1. Enter a handle in the sidebar → **Run analysis**\n"
        "2. Explore the Dashboard, Competitors and Chat tabs\n"
        "3. Chat answers quote the account's real numbers"
    )
    st.info("Backend: " + BACKEND)
    st.stop()

ins = st.session_state.insight
p, m = ins["profile"], ins["metrics"]

st.title(f"@{p['username']}")
head_cols = st.columns([2, 3, 2])
with head_cols[0]:
    if p.get("is_verified"):
        st.markdown("✔️ **Verified**")
    st.caption(p.get("category") or "—")
with head_cols[1]:
    st.caption((p.get("bio") or "")[:200])
with head_cols[2]:
    if p.get("data_age_hours") is not None:
        st.warning(f"Simulated/aged data (age {p.get('data_age_hours')}h)")
    st.metric("Account score", f"{ins.get('account_score', '—')}/100")

# --- Dashboard tab -----------------------------------------------------------
tab_dash, tab_comp, tab_chat, tab_hist = st.tabs(
    ["📈 Dashboard", "⚔️ Competitors", "💬 Chat", "🕘 History"]
)

with tab_dash:
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Followers", _km(p.get("followers")))
    c2.metric("Following", _km(p.get("following")))
    c3.metric("Posts", _km(p.get("posts_count")))
    c4.metric("Engagement", f"{m.get('engagement_rate', 0)}%")

    c5, c6, c7, c8 = st.columns(4)
    c5.metric("Avg likes", _km(m.get("avg_likes")))
    c6.metric("Avg comments", _km(m.get("avg_comments")))
    c7.metric("Posting cadence", f"{m.get('posting_frequency_per_week', 0)}/wk")
    c8.metric("Best format", m.get("best_content_type") or "—")

    st.divider()
    st.subheader("AI narrative")
    st.write(ins.get("ai_summary") or "—")

    a, b, c = st.columns(3)
    with a:
        st.markdown("**💪 Strengths**")
        for s in ins.get("strengths", []):
            st.markdown(f"- {s}")
    with b:
        st.markdown("**⚠️ Weaknesses**")
        for s in ins.get("weaknesses", []):
            st.markdown(f"- {s}")
    with c:
        st.markdown("**🎯 Recommendations**")
        for s in ins.get("recommendations", []):
            st.markdown(f"- {s}")

    st.divider()
    left, right = st.columns([3, 2])
    with left:
        st.subheader("Recent posts (real engagement)")
        df = _posts_df(p)
        if df.empty:
            st.caption("No posts in the fetched sample.")
        else:
            st.dataframe(df, use_container_width=True, hide_index=True)
    with right:
        st.subheader("Growth tracking")
        try:
            gt = api_get("/api/growth-tracking", {"username": p["username"]})
            snaps = gt.get("snapshots") or []
            if len(snaps) >= 2:
                chart_df = pd.DataFrame(snaps)
                ycol = "followers" if "followers" in chart_df.columns else None
                if ycol:
                    st.line_chart(chart_df.set_index("day")[ycol] if "day" in chart_df.columns else chart_df[ycol])
                else:
                    st.caption("Snapshots available but no follower column.")
                deltas = gt.get("latest_vs_previous") or gt.get("deltas") or {}
                if deltas:
                    st.caption(json.dumps(deltas, indent=2)[:300])
            else:
                st.caption("One scan so far — keep analyzing this handle to build a trend.")
        except Exception as e:
            st.caption(f"Growth data unavailable: {str(e)[:80]}")

    st.divider()
    st.download_button(
        "⬇️ Download full analysis (JSON)",
        data=json.dumps(ins, indent=2),
        file_name=f"instaiq_{p['username']}_{datetime.now():%Y%m%d}.json",
        mime="application/json",
    )

# --- Competitors tab ----------------------------------------------------------
with tab_comp:
    st.subheader("Competitor research")
    count = st.slider("How many rivals?", 2, 5, 3)
    if st.button("🔍 Research competitors", type="primary"):
        with st.spinner("Discovering + researching rivals (LLM-picked) — 1-3 min…"):
            try:
                res = api_post(
                    "/api/competitor-research",
                    {"username": p["username"]},
                    params={"count": count},
                )
                st.session_state.competitors = res
            except Exception as e:
                st.error(f"Research failed: {e}")

    res = st.session_state.get("competitors")
    if res:
        ranking = res.get("ranking") or []
        if ranking:
            st.caption("Composite ranking (best first): " + " → ".join(f"@{u}" for u in ranking))
        st.markdown("**Market summary**")
        st.write(res.get("market_summary") or "—")
        g1, g2, o = st.columns(3)
        with g1:
            st.markdown("**Competitive gaps**")
            for s in res.get("competitive_gaps", []):
                st.markdown(f"- {s}")
        with g2:
            st.markdown("**Content gaps**")
            for s in res.get("content_gaps", []):
                st.markdown(f"- {s}")
        with o:
            st.markdown("**Opportunities**")
            for s in res.get("opportunities", []):
                st.markdown(f"- {s}")
        st.divider()
        for ci in res.get("competitors", []):
            cp, cm = ci["profile"], ci["metrics"]
            with st.expander(
                f"@{cp['username']} — {_km(cp.get('followers'))} followers · "
                f"ER {cm.get('engagement_rate')}% · score {ci.get('account_score', '—')}"
            ):
                st.write(ci.get("ai_summary") or "—")
                d1, d2, d3 = st.columns(3)
                d1.metric("Avg likes", _km(cm.get("avg_likes")))
                d2.metric("Avg comments", _km(cm.get("avg_comments")))
                d3.metric("Cadence", f"{cm.get('posting_frequency_per_week', 0)}/wk")
                cdf = _posts_df(cp)
                if not cdf.empty:
                    st.dataframe(cdf, use_container_width=True, hide_index=True)
        if res.get("warnings"):
            st.warning("Could not fetch: " + ", ".join(res["warnings"]))

# --- Chat tab -------------------------------------------------------------------
with tab_chat:
    st.subheader("Analyst chat")
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
                                    f"Account: @{p['username']} | {p.get('followers'):,} followers | "
                                    f"ER {m.get('engagement_rate')}% | avg likes {m.get('avg_likes'):,.0f}"
                                ),
                            },
                        )
                        answer = resp.get("answer") or "(empty answer)"
                        if not resp.get("llm_used"):
                            answer = "_[rule-based]_ " + answer
                        st.session_state.chat.append(("assistant", answer))
                        st.markdown(answer)
                    except Exception as e:
                        err = f"Chat failed: {e}"
                        st.session_state.chat.append(("assistant", err))
                        st.error(err)

# --- History tab ------------------------------------------------------------------
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
                    ins2 = api_post("/api/restore", {"username": r["username"]})
                    st.session_state.insight = ins2
                    st.session_state.handle = ins2["profile"]["username"]
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
        if no.button("Cancel", key="confirm_no"):
            st.session_state.confirm_delete = None
            st.rerun()
