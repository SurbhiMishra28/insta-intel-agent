import { useEffect, useState } from 'react';
import ProfileReadout from './components/ProfileReadout.jsx';
import RankingBars from './components/RankingBars.jsx';
import EngagementChart from './components/EngagementChart.jsx';
import Report from './components/Report.jsx';
import GrowthPlanView from './components/GrowthPlan.jsx';
import FollowerGrowthIcon from './components/FollowerGrowthIcon.jsx';
import ResearchProgress from './components/ResearchProgress.jsx';
import BestTimes from './components/BestTimes.jsx';
import ReelsInsights from './components/ReelsInsights.jsx';
import ExtrasGrid from './components/ExtrasGrid.jsx';
import ReelTimingView from './components/ReelTiming.jsx';
import HashtagSuggestionView from './components/HashtagSuggestion.jsx';
import Trends from './components/Trends.jsx';
import MonthlyReviewer from './components/MonthlyReviewer.jsx';
import WhitespaceFinder from './components/WhitespaceFinder.jsx';
import PWAInstallBanner from './components/PWAInstallBanner.jsx';
import ChatBox from './components/ChatBox.jsx';

const API_URL = import.meta.env.VITE_API_URL || 'http://localhost:8000';

export default function App() {
  const [mode, setMode] = useState('single'); // 'single' | 'compare' | 'growth' | 'trends'
  const [username, setUsername] = useState('');
  const [competitors, setCompetitors] = useState(['', '']);
  const [autoFind, setAutoFind] = useState(true);
  const [compCount, setCompCount] = useState(5);
  const [includeCompetitors, setIncludeCompetitors] = useState(true);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [result, setResult] = useState(null); // ProfileInsight | research response
  const [trendsResult, setTrendsResult] = useState(null); // TrendsResponse
  const [reviewResult, setReviewResult] = useState(null); // MonthlyReviewResponse
  const [dataMode, setDataMode] = useState('');

  useEffect(() => {
    fetch(`${API_URL}/`)
      .then((r) => (r.ok ? r.json() : null))
      .then((d) => setDataMode(d?.data_mode || ''))
      .catch(() => {});
  }, []);

  const updateCompetitor = (idx, value) => {
    setCompetitors((prev) => prev.map((c, i) => (i === idx ? value : c)));
  };

  const addCompetitorField = () => {
    if (competitors.length < 10) setCompetitors((prev) => [...prev, '']);
  };

  const runAnalysis = async (e) => {
    e.preventDefault();
    setError('');
    if (!username.trim()) return;
    setLoading(true);
    setResult(null);
    setTrendsResult(null);
    try {
      if (mode === 'trends') {
        // Trend detection + alerts + content ideas for the account.
        const res = await fetch(`${API_URL}/api/trends/alert`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ username: username.trim() }),
        });
        if (!res.ok) throw new Error((await res.json()).detail || 'Trend detection failed');
        setTrendsResult(await res.json());
        // Also load the basic profile so the readout shows.
        const profRes = await fetch(`${API_URL}/api/analyze`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ username: username.trim() }),
        });
        if (profRes.ok) setResult(await profRes.json());
      } else if (mode === 'review') {
        // Monthly profile review from scan history.
        const res = await fetch(`${API_URL}/api/review`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ username: username.trim() }),
        });
        if (!res.ok) throw new Error((await res.json()).detail || 'Review failed');
        setReviewResult(await res.json());
        // Also load the basic profile so the readout shows.
        const profRes = await fetch(`${API_URL}/api/analyze`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ username: username.trim() }),
        });
        if (profRes.ok) setResult(await profRes.json());
      } else if (mode === 'growth') {
        // Content suggestions + follower-growth plan, grounded in the
        // account's real data plus 4 auto-researched niche rivals.
        const res = await fetch(`${API_URL}/api/growth-plan?count=4`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ username: username.trim() }),
        });
        if (!res.ok) throw new Error((await res.json()).detail || 'Growth plan failed');
        setResult(await res.json());
      } else if (mode === 'whitespace') {
        // Content whitespace finder + caption suggestions, grounded in the
        // account's real posts (rivals research skipped: uses cached profile
        // when available, so it's fast and free).
        const res = await fetch(`${API_URL}/api/whitespace?rivals=0`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ username: username.trim() }),
        });
        if (!res.ok) throw new Error((await res.json()).detail || 'Whitespace analysis failed');
        setResult(await res.json());
      } else if (mode === 'single' && !includeCompetitors) {
        // Just the one profile, no competitor research.
        const res = await fetch(`${API_URL}/api/analyze`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ username: username.trim() }),
        });
        if (!res.ok) throw new Error((await res.json()).detail || 'Analysis failed');
        setResult(await res.json());
      } else if (mode === 'single') {
        // Full pipeline: profile + auto-found, deeply researched competitors.
        const res = await fetch(`${API_URL}/api/competitor-research?count=5`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ username: username.trim() }),
        });
        if (!res.ok) throw new Error((await res.json()).detail || 'Research failed');
        setResult(await res.json());
      } else if (autoFind) {
        const res = await fetch(`${API_URL}/api/competitor-research?count=${compCount}`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ username: username.trim() }),
        });
        if (!res.ok) throw new Error((await res.json()).detail || 'Research failed');
        setResult(await res.json());
      } else {
        const list = competitors.map((c) => c.trim()).filter(Boolean);
        if (list.length === 0) {
          setError('Add at least one competitor handle to compare against.');
          setLoading(false);
          return;
        }
        const res = await fetch(`${API_URL}/api/compare`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ main_username: username.trim(), competitor_usernames: list }),
        });
        if (!res.ok) throw new Error((await res.json()).detail || 'Comparison failed');
        setResult(await res.json());
      }
    } catch (err) {
      setError(err.message || 'Something went wrong.');
    } finally {
      setLoading(false);
    }
  };

  // Unified view model: plain analysis results are shaped into the research
  // response's structure so one rendering path handles every mode.
  const mainInsight = result ? (result.main || result) : null;
  const rivals = result?.competitors || [];
  const hasRivals = rivals.length > 0;
  const allInsights = mainInsight ? [mainInsight, ...rivals] : [];
  const researching = loading && (mode === 'compare' || mode === 'growth' || (mode === 'single' && includeCompetitors));

  return (
    <div className="app-shell">
      <header className="hero">
        <div className="hero-eyebrow"><span className="dot" />InstaIQ · AI Intelligence Agent</div>
        <h1>Read any Instagram account like an analyst would.</h1>
        <p className="sub">
          Enter a handle to get engagement benchmarks, content patterns, and
          AI-written strategy notes — with competitors found and researched
          for you automatically.
        </p>

        <form className="scanner" onSubmit={runAnalysis}>
          <div className="mode-toggle">
            <button type="button" className={mode === 'single' ? 'active' : ''} onClick={() => setMode('single')}>
              Analyze one profile
            </button>
            <button type="button" className={mode === 'compare' ? 'active' : ''} onClick={() => setMode('compare')}>
              Compare vs competitors
            </button>
            <button type="button" className={mode === 'growth' ? 'active' : ''} onClick={() => setMode('growth')} style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
              Growth plan <FollowerGrowthIcon size={13} />
            </button>
            <button type="button" className={mode === 'whitespace' ? 'active' : ''} onClick={() => setMode('whitespace')} style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
              Whitespace &amp; captions <span style={{ fontSize: 11, background: '#C6FF4E', color: '#15171A', borderRadius: 10, padding: '1px 6px', fontWeight: 700 }}>NEW</span>
            </button>
            <button type="button" className={mode === 'trends' ? 'active' : ''} onClick={() => setMode('trends')} style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
              Trends <span style={{ fontSize: 11, background: '#C6FF4E', color: '#15171A', borderRadius: 10, padding: '1px 6px', fontWeight: 700 }}>NEW</span>
            </button>
            <button type="button" className={mode === 'review' ? 'active' : ''} onClick={() => setMode('review')} style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
              Monthly review <FollowerGrowthIcon size={13} />
            </button>
          </div>

          <div className="scanner-row">
            <span className="scanner-prefix">@</span>
            <input
              type="text"
              placeholder="handle or instagram.com/username"
              value={username}
              onChange={(e) => setUsername(e.target.value)}
            />
            <button className="scanner-submit" type="submit" disabled={loading}>
              {loading ? 'Scanning…' : 'Run analysis'}
            </button>
          </div>

          {mode === 'single' && (
            <label style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 13, color: '#9CA1A6', cursor: 'pointer', margin: '8px 0 4px' }}>
              <input
                type="checkbox"
                checked={includeCompetitors}
                onChange={(e) => setIncludeCompetitors(e.target.checked)}
              />
              Also find &amp; benchmark 5 competitors for this account
            </label>
          )}

          {mode === 'compare' && (
            <div style={{ display: 'flex', gap: 16, alignItems: 'center', margin: '8px 0 4px', flexWrap: 'wrap' }}>
              <label style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 13, color: '#9CA1A6', cursor: 'pointer' }}>
                <input type="checkbox" checked={autoFind} onChange={(e) => setAutoFind(e.target.checked)} />
                Auto-find competitors for me
              </label>
              {autoFind && (
                <label style={{ fontSize: 13, color: '#9CA1A6' }}>
                  How many:{' '}
                  <select
                    value={compCount}
                    onChange={(e) => setCompCount(Number(e.target.value))}
                    style={{ background: 'transparent', color: '#E8EAED', border: '1px solid #3A3F45', borderRadius: 4, padding: '2px 6px' }}
                  >
                    {[5, 6, 7, 8, 9, 10].map((n) => <option key={n} value={n}>{n}</option>)}
                  </select>
                </label>
              )}
            </div>
          )}

          {mode === 'compare' && !autoFind && (
            <div className="competitor-inputs">
              {competitors.map((c, idx) => (
                <input
                  key={idx}
                  type="text"
                  placeholder={`competitor ${idx + 1} handle`}
                  value={c}
                  onChange={(e) => updateCompetitor(idx, e.target.value)}
                />
              ))}
              {competitors.length < 10 && (
                <button
                  type="button"
                  onClick={addCompetitorField}
                  style={{ background: 'none', border: 'none', color: '#9CA1A6', fontSize: 13, textAlign: 'left', padding: '4px 0' }}
                >
                  + add another competitor
                </button>
              )}
            </div>
          )}

          {error && <p className="error-line">{error}</p>}
        </form>
      </header>

      {loading && (
        <ResearchProgress
          label={researching ? undefined : 'Pulling profile data and running the analysis engine…'}
 />
      )}

      {result && mode === 'single' && !hasRivals && (
        <>
          <section className="section">
            <p className="section-label">Profile readout</p>
            <ProfileReadout insight={mainInsight} />
          </section>
          <section className="section">
            <p className="section-label">AI intelligence report</p>
            <Report insight={mainInsight} />
          </section>
        </>
      )}

      {result && mode === 'growth' && result.plan && (
        <>
          <section className="section">
            <p className="section-label">Account readout</p>
            <ProfileReadout insight={result.main} />
          </section>

          {result.warnings?.length > 0 && (
            <div className="section">
              <p className="error-line">
                {result.warnings.map((w, i) => <span key={i} style={{ display: 'block' }}>{w}</span>)}
              </p>
            </div>
          )}

          <section className="section">
            <p className="section-label">Content &amp; follower growth plan</p>
            <GrowthPlanView plan={result.plan} />
          </section>

          {result.best_times && (
            <section className="section">
              <p className="section-label">Best time to post (from real post timestamps)</p>
              <BestTimes bestTimes={result.best_times} />
            </section>
          )}

          {result.reels && (
            <section className="section">
              <p className="section-label">Reels deep-dive vs niche</p>
              <ReelsInsights reels={result.reels} />
            </section>
          )}

          {result.trends_result && (
            <section className="section">
              <p className="section-label">Instagram trends for this account</p>
              <Trends trends={result.trends_result} />
            </section>
          )}

          {(result.bio || result.hashtags || result.history || result.reel_timing || result.hashtag_suggestions) && (
            <section className="section">
              <p className="section-label">Optimizer toolkit</p>
              <ExtrasGrid
                bio={result.bio}
                hashtags={result.hashtags}
                history={result.history}
                reelTiming={result.reel_timing}
                hashtagSuggestions={result.hashtag_suggestions}
              />
            </section>
          )}

          {result.rivals?.length > 0 && (
            <section className="section">
              <p className="section-label">Benchmarked against (auto-researched rivals)</p>
              <RankingBars
                ranking={[result.main.profile.username, ...result.rivals.map((r) => r.profile.username)]}
                allInsights={[result.main, ...result.rivals]}
                mainUsername={result.main.profile.username}
              />
            </section>
          )}
        </>
      )}

      {result && hasRivals && mode !== 'growth' && (
        <>
          <section className="section">
            <p className="section-label">Profile readout</p>
            <ProfileReadout insight={mainInsight} />
          </section>

          <section className="section">
            <p className="section-label">
              Competitive ranking
              {result.candidates_found ? ` — ${result.candidates_found} candidates discovered` : ''}
            </p>
            {result.selection_rationale && (
              <p className="report-summary" style={{ marginBottom: 10 }}>{result.selection_rationale}</p>
            )}
            <RankingBars ranking={result.ranking} allInsights={allInsights} mainUsername={mainInsight.profile.username} />
          </section>

          <section className="section">
            <p className="section-label">Engagement rate vs. competitors</p>
            <EngagementChart allInsights={allInsights} mainUsername={mainInsight.profile.username} />
          </section>

          <section className="section">
            <p className="section-label">Market summary</p>
            <p className="report-summary">{result.market_summary}</p>
            <h3 style={{ fontFamily: 'var(--font-display)', fontSize: 14, margin: '0 0 4px' }}>Competitive gaps</h3>
            <ul className="gap-list">
              {result.competitive_gaps.map((g, i) => <li key={i}>{g}</li>)}
            </ul>
          </section>

          {result.content_gaps?.length > 0 && (
            <section className="section">
              <p className="section-label">Content gaps rivals own</p>
              <ul className="gap-list">
                {result.content_gaps.map((g, i) => <li key={i}>{g}</li>)}
              </ul>
            </section>
          )}

          {result.opportunities?.length > 0 && (
            <section className="section">
              <p className="section-label">Opportunities for you</p>
              <ul className="gap-list">
                {result.opportunities.map((g, i) => <li key={i}>{g}</li>)}
              </ul>
            </section>
          )}

          <section className="section">
            <p className="section-label">Your intelligence report</p>
            <Report insight={mainInsight} />
          </section>

          {result.warnings?.length > 0 && (
            <div className="section">
              <p className="error-line">
                {result.warnings.map((w, i) => <span key={i} style={{ display: 'block' }}>{w}</span>)}
              </p>
            </div>
          )}

          {rivals.map((c) => (
            <div className="competitor-block" key={c.profile.username}>
              <p className="section-label">@{c.profile.username} — competitor readout</p>
              <ProfileReadout insight={c} />
            </div>
          ))}
        </>
      )}

      {result && mode === 'trends' && (
        <section className="section">
          <p className="section-label">Account readout</p>
          <ProfileReadout insight={result.main || result} />
        </section>
      )}

      {result && mode === 'whitespace' && result.whitespace && (
        <>
          <section className="section">
            <p className="section-label">Account readout</p>
            <ProfileReadout insight={result.main} />
          </section>
          {result.warnings?.length > 0 && (
            <div className="section">
              <p className="error-line">
                {result.warnings.map((w, i) => <span key={i} style={{ display: 'block' }}>{w}</span>)}
              </p>
            </div>
          )}
          <section className="section">
            <p className="section-label">Content whitespace &amp; caption suggestions</p>
            <WhitespaceFinder data={result} />
          </section>
        </>
      )}

      {mode === 'trends' && trendsResult && (
        <section className="section">
          <p className="section-label">Instagram trends</p>
          <Trends trends={trendsResult} />
        </section>
      )}

      {result && mode === 'review' && reviewResult && (
        <>
          <section className="section">
            <p className="section-label">Monthly profile review</p>
            <MonthlyReviewer review={reviewResult} />
          </section>
        </>
      )}

      <ChatBox context={mainInsight} />

      <PWAInstallBanner />

      <footer className="footer-note">
        {dataMode === 'live'
          ? 'Data mode: live — real public Instagram data via the Apify data provider.'
          : dataMode === 'demo'
            ? 'Data mode: demo — simulated profiles. Set APIFY_TOKEN + DATA_MODE=live in the backend for real data.'
            : ''}
      </footer>
    </div>
  );
}
