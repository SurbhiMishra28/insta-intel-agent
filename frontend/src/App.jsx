import { useEffect, useState } from 'react';
import ProfileReadout from './components/ProfileReadout.jsx';
import RankingBars from './components/RankingBars.jsx';
import EngagementChart from './components/EngagementChart.jsx';
import Report from './components/Report.jsx';
import GrowthPlanView from './components/GrowthPlan.jsx';
import FollowerGrowthIcon from './components/FollowerGrowthIcon.jsx';
import ResearchProgress from './components/ResearchProgress.jsx';
import BestTimes from './components/BestTimes.jsx';
import CadenceTimingMap from './components/CadenceTimingMap.jsx';
import ReelsInsights from './components/ReelsInsights.jsx';
import ExtrasGrid from './components/ExtrasGrid.jsx';
import ReelTimingView from './components/ReelTiming.jsx';
import HashtagSuggestionView from './components/HashtagSuggestion.jsx';
import Trends from './components/Trends.jsx';
import MonthlyReviewer from './components/MonthlyReviewer.jsx';
import WhitespaceFinder from './components/WhitespaceFinder.jsx';
import PWAInstallBanner from './components/PWAInstallBanner.jsx';
import ChatBox from './components/ChatBox.jsx';
import DataStore from './components/DataStore.jsx';

const API_URL = import.meta.env.VITE_API_URL || 'http://localhost:8000';

function previewHandle(raw) {
  const t = (raw || '').trim();
  if (!t) return '';
  let m = t.match(/(?:instagram\.com|instagr\.am)\/([A-Za-z0-9._]+)/i);
  if (!m) m = t.match(/ig\.me\/(?:m\/)?([A-Za-z0-9._]+)/i);
  if (!m) m = t.match(/^@([A-Za-z0-9._]{2,30})$/);
  return m ? m[1].toLowerCase() : '';
}

const fmt = (n) =>
  typeof n === 'number' ? n.toLocaleString('en-US') : '—';

function erVerdict(er) {
  if (er >= 6) return { label: 'Excellent', cls: 'good' };
  if (er >= 3) return { label: 'Strong', cls: 'good' };
  if (er >= 1) return { label: 'Average', cls: 'mid' };
  return { label: 'Needs work', cls: 'bad' };
}

/* Score ring for the hero card (0-100 composite-feel gauge). */
function ScoreRing({ value }) {
  const R = 52, C = 2 * Math.PI * R;
  const v = Math.max(0, Math.min(100, value));
  return (
    <div className="score-ring">
      <svg viewBox="0 0 120 120" width="120" height="120">
        <circle cx="60" cy="60" r={R} className="ring-track" />
        <circle
          cx="60" cy="60" r={R} className="ring-fill"
          strokeDasharray={C} strokeDashoffset={C - (C * v) / 100}
        />
      </svg>
      <div className="ring-num">{Math.round(v)}<span>/100</span></div>
      <div className="ring-label">Account score</div>
    </div>
  );
}

function Section({ id, label, children }) {
  return (
    <section className="section" id={id}>
      <p className="section-label">{label}</p>
      {children}
    </section>
  );
}

export default function App() {
  const [username, setUsername] = useState('');
  const [loading, setLoading] = useState(false);
  const [stage, setStage] = useState('');
  const [error, setError] = useState('');
  const [dataMode, setDataMode] = useState('');

  // One dashboard: everything the agent produces.
  const [dash, setDash] = useState(null);

  // Deep dives (run on demand, keep the dashboard visible).
  const [research, setResearch] = useState(null);
  const [review, setReview] = useState(null);
  const [whitespace, setWhitespace] = useState(null);
  const [trendsData, setTrendsData] = useState(null);
  const [busy, setBusy] = useState({}); // { research: bool, review: bool, ... }
  const [pdfBusy, setPdfBusy] = useState(false);

  useEffect(() => {
    fetch(`${API_URL}/`)
      .then((r) => (r.ok ? r.json() : null))
      .then((d) => setDataMode(d?.data_mode || ''))
      .catch(() => {});
  }, []);

  const post = async (path, body) => {
    const res = await fetch(`${API_URL}${path}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    if (!res.ok) {
      const detail = await res.json().catch(() => ({}));
      throw new Error(detail.detail || `Request failed (${res.status})`);
    }
    return res.json();
  };

  const run = async (e) => {
    e.preventDefault();
    const handle = username.trim();
    if (!handle) return;
    setError('');
    setLoading(true);
    setDash(null);
    setResearch(null);
    setReview(null);
    setWhitespace(null);
    setTrendsData(null);
    try {
      setStage('Fetching real profile data…');
      const plan = await post('/api/growth-plan?count=4', { username: handle });
      setDash(plan);
      setStage('');
    } catch (err) {
      setError(err.message || 'Something went wrong.');
    } finally {
      setLoading(false);
      setStage('');
    }
  };

  const runDeepDive = async (key, path, qs, setter) => {
    const handle = username.trim();
    if (!handle) return;
    setBusy((b) => ({ ...b, [key]: true }));
    try {
      setter(await post(`${path}${qs}`, { username: handle }));
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy((b) => ({ ...b, [key]: false }));
    }
  };

  const downloadPdf = async () => {
    const handle = p?.username || previewHandle(username);
    if (!handle || pdfBusy) return;
    setPdfBusy(true);
    try {
      const res = await fetch(`${API_URL}/api/export/pdf`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ username: handle }),
      });
      if (!res.ok) {
        const detail = await res.json().catch(() => ({}));
        throw new Error(detail.detail || `Export failed (${res.status})`);
      }
      const blob = await res.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `instaiq-${handle}-report.pdf`;
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
    } catch (err) {
      setError(err.message || 'PDF export failed.');
    } finally {
      setPdfBusy(false);
    }
  };

  const main = dash?.main || null;
  const p = main?.profile || null;
  const m = main?.metrics || null;
  const verdict = m ? erVerdict(m.engagement_rate) : null;
  const handle = p?.username || previewHandle(username);
  const busyAny = busy.research || busy.review || busy.whitespace || busy.trends;

  const navSections = [
    ['profile', 'Profile'],
    ['report', 'AI report'],
    ['plan', 'Growth plan'],
    ['timing', 'Timing'],
    ['toolkit', 'Toolkit'],
    ['trends', 'Trends'],
    ['research', 'Competitors'],
    ['datastore', 'Data store'],
    ['chat', 'Ask AI'],
  ];

  const scrollTo = (id) =>
    document.getElementById(id)?.scrollIntoView({ behavior: 'smooth', block: 'start' });

  return (
    <div className="app-shell">
      <header className="hero">
        <div className="hero-eyebrow">
          <span className="dot" />InstaIQ · AI Growth Agent
        </div>
        <h1>Paste a profile URL. Get the full growth playbook.</h1>
        <p className="sub">
          One input — the agent pulls the account's real data, benchmarks it, and
          returns an AI-written strategy: what to post, when to post, which
          hashtags, bio rewrite, trend plays and competitor gaps.
        </p>

        <form className="scanner" onSubmit={run}>
          <div className="scanner-row">
            <span className="scanner-prefix">@</span>
            <input
              type="text"
              placeholder="paste an instagram.com/username URL — or type a handle"
              value={username}
              onChange={(e) => setUsername(e.target.value)}
            />
            <button className="scanner-submit" type="submit" disabled={loading}>
              {loading ? 'Working…' : 'Do everything'}
            </button>
          </div>
          {username.trim() && /instagram\.com|instagr\.am|ig\.me|https?:\/\//i.test(username) && (
            <p className="handle-hint">→ analyzing @{previewHandle(username) || '…'}</p>
          )}
          {error && <p className="error-line">{error}</p>}
        </form>
      </header>

      {loading && (
        <ResearchProgress label={stage || 'Running the full analysis pipeline…'} />
      )}

      {dash && main && (
        <>
          <nav className="dash-nav" aria-label="Dashboard sections">
            {navSections.map(([id, label]) => (
              <button key={id} type="button" onClick={() => scrollTo(id)}>
                {label}
              </button>
            ))}
          </nav>

          {/* ---------- Score hero + single profile section ---------- */}
          <p className="section-label">Profile</p>
          <div className="dash-hero" id="profile">
            <ScoreRing
              value={
                main?.account_score != null
                  ? main.account_score
                  : Math.min(
                      100,
                      Math.round(
                        (m?.engagement_rate || 0) * 8 +
                          Math.min((p?.followers || 0) / 10000, 30) +
                          Math.min((m?.posting_frequency_per_week || 0) * 6, 20),
                      ),
                    )
              }
            />
            <div className="dash-hero-main">
              <div className="dash-hero-top">
                <h2>@{handle}</h2>
                {p?.is_verified && <span className="badge">VERIFIED</span>}
                {verdict && <span className={`pill ${verdict.cls}`}>{verdict.label} ER</span>}
                {p?.data_age_hours === -1 && <span className="pill bad">SIMULATED</span>}
                <button
                  className="pdf-btn"
                  onClick={downloadPdf}
                  disabled={pdfBusy}
                  title="Download the full analysis as a PDF report"
                >
                  {pdfBusy ? 'Building PDF…' : '⬇ Download PDF'}
                </button>
              </div>
              <p className="dash-hero-bio">{p?.bio || p?.full_name || ''}</p>
              <div className="dash-stats">
                <div className="stat">
                  <div className="num">{fmt(p?.followers)}</div>
                  <div className="label">Followers</div>
                </div>
                <div className="stat">
                  <div className="num">{fmt(p?.posts_count)}</div>
                  <div className="label">Posts</div>
                </div>
                <div className="stat highlight">
                  <div className="num">{m?.engagement_rate ?? '—'}%</div>
                  <div className="label">Engagement rate</div>
                </div>
                <div className="stat">
                  <div className="num">{fmt(m?.avg_comments != null ? Math.round(m.avg_comments) : null)}</div>
                  <div className="label">Avg comments</div>
                </div>
                <div className="stat">
                  <div className="num">{m?.posting_frequency_per_week ?? '—'}</div>
                  <div className="label">Posts / week</div>
                </div>
              </div>

              {/* Compact readout: the stats the hero card doesn't already show.
                  The profile lives ONLY here — no duplicate section below. */}
              <ProfileReadout insight={main} compact />
            </div>
          </div>

          {dash.warnings?.length > 0 && (
            <div className="notice-warn">
              {dash.warnings.map((w, i) => (
                <span key={i}>{w}</span>
              ))}
            </div>
          )}

          {dash.score_explanation && (
            <div className="notice-warn score-explain">
              <span className="score-explain-title">
                Account score {dash.score_explanation.total}/100 — why:
              </span>
              {dash.score_explanation.drivers?.map((d, i) => (
                <span key={`d${i}`}>▲ {d}</span>
              ))}
              {dash.score_explanation.drainers?.map((d, i) => (
                <span key={`n${i}`}>▼ {d}</span>
              ))}
            </div>
          )}

          <Section id="report" label="AI intelligence report">
            <Report insight={main} />
          </Section>

          <Section id="plan" label="Growth plan — what to post next">
            <GrowthPlanView plan={dash.plan} />
          </Section>

          <Section id="timing" label="Timing intelligence — when to post">
            <div className="grid-2">
              {dash.best_times && <BestTimes bestTimes={dash.best_times} />}
              {dash.cadence_map && <CadenceTimingMap cadenceMap={dash.cadence_map} />}
            </div>
            <div className="grid-2">
              {dash.reels && <ReelsInsights reels={dash.reels} />}
              {dash.reel_timing && <ReelTimingView reelTiming={dash.reel_timing} />}
            </div>
          </Section>

          <Section id="toolkit" label="Optimizer toolkit — ready to paste">
            <ExtrasGrid
              bio={dash.bio}
              hashtags={dash.hashtags}
              history={dash.history}
              reelTiming={null}
              hashtagSuggestions={dash.hashtag_suggestions}
            />
          </Section>

          <Section id="trends" label="Trend plays for this account">
            {dash.trends_result ? (
              <Trends trends={dash.trends_result} />
            ) : (
              <p className="muted">No trend signals detected for this account.</p>
            )}
          </Section>

          {/* ---------- Deep dives ---------- */}
          <Section id="research" label="Deep dives — go further with one click">
            <div className="deep-grid">
              <button
                className="deep-card"
                disabled={busyAny}
                onClick={() =>
                  runDeepDive('research', '/api/competitor-research', '?count=5', setResearch)
                }
              >
                <span className="deep-title">
                  {busy.research ? 'Researching rivals…' : 'Competitor research'}
                </span>
                <span className="deep-sub">
                  Auto-discover 5 rivals, rank you against them, list the gaps they own.
                </span>
              </button>
              <button
                className="deep-card"
                disabled={busyAny}
                onClick={() => runDeepDive('whitespace', '/api/whitespace', '?rivals=0', setWhitespace)}
              >
                <span className="deep-title">
                  {busy.whitespace ? 'Finding whitespace…' : 'Whitespace & captions'}
                </span>
                <span className="deep-sub">
                  Content themes you're missing + ready-to-post captions from your data.
                </span>
              </button>
              <button
                className="deep-card"
                disabled={busyAny}
                onClick={() => runDeepDive('review', '/api/review', '', setReview)}
              >
                <span className="deep-title">
                  {busy.review ? 'Building review…' : 'Monthly review'}
                </span>
                <span className="deep-sub">
                  Trajectory of followers, engagement and cadence across your scans.
                </span>
              </button>
            </div>

            {research && (
              <div className="deep-result">
                <p className="section-label">Competitive ranking</p>
                {research.selection_rationale && (
                  <p className="muted">{research.selection_rationale}</p>
                )}
                <RankingBars
                  ranking={research.ranking}
                  allInsights={[research.main, ...(research.competitors || [])]}
                  mainUsername={research.main?.profile?.username}
                />
                <EngagementChart
                  allInsights={[research.main, ...(research.competitors || [])]}
                  mainUsername={research.main?.profile?.username}
                />
                {research.market_summary && (
                  <>
                    <p className="section-label">Market summary</p>
                    <p className="muted">{research.market_summary}</p>
                  </>
                )}
                {research.opportunities?.length > 0 && (
                  <>
                    <p className="section-label">Opportunities for you</p>
                    <ul className="gap-list">
                      {research.opportunities.map((g, i) => (
                        <li key={i}>{g}</li>
                      ))}
                    </ul>
                  </>
                )}
                {research.competitors?.length > 0 && (
                  <>
                    <p className="section-label">Rival readouts</p>
                    {research.competitors.map((c) => (
                      <div className="competitor-block" key={c.profile.username}>
                        <ProfileReadout insight={c} />
                      </div>
                    ))}
                  </>
                )}
              </div>
            )}

            {whitespace && (
              <div className="deep-result">
                <WhitespaceFinder data={whitespace} />
              </div>
            )}

            {review && (
              <div className="deep-result">
                <MonthlyReviewer review={review} />
              </div>
            )}
          </Section>

          <Section id="datastore" label="Data store — every handle the agent has researched">
            <DataStore api={API_URL} />
          </Section>

          <div id="chat">
            <ChatBox context={main} />
          </div>
        </>
      )}

      <PWAInstallBanner />

      <footer className="footer-note">
        {dataMode === 'live'
          ? 'Data mode: live — real Instagram data (Apify + keyless fallback), cached locally; AI by NVIDIA NIM.'
          : dataMode === 'cache'
          ? 'Data mode: cache — real cached Instagram data; AI by NVIDIA NIM.'
          : dataMode === 'demo'
          ? 'Data mode: demo — simulated profiles for offline development.'
          : ''}
      </footer>
    </div>
  );
}
