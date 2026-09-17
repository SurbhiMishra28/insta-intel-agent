import { useEffect, useState } from 'react';

/* Growth tracking: what changed since the agent's stored searches.
   Every number comes from real recorded scans of this handle —
   when a baseline (1 week / 1 month ago) doesn't exist yet, the card says so
   instead of inventing data. */

const timeAgo = (iso) => {
  if (!iso) return '';
  const h = (Date.now() - new Date(iso).getTime()) / 3.6e6;
  if (h < 1) return 'just now';
  if (h < 48) return `${Math.round(h)}h ago`;
  return `${Math.round(h / 24)}d ago`;
};

const dateShort = (iso) =>
  iso ? new Date(iso).toLocaleDateString(undefined, { month: 'short', day: 'numeric' }) : '';

const fmtNum = (n) => (n == null ? '—' : n >= 1000 ? n.toLocaleString('en-US') : String(Math.round(n)));

const fmtDelta = (d, digits = 0) => {
  if (d == null) return null;
  const v = digits ? Math.abs(d).toFixed(digits) : Math.abs(Math.round(d)).toLocaleString('en-US');
  return `${d > 0 ? '+' : d < 0 ? '−' : '±'}${v}`;
};

/* One before → after metric card. */
function DeltaCard({ label, cur, base, delta, unit = '', pct, digits = 0, better, baseDate }) {
  // better: 'up' | 'down' | null — what direction is good for this metric
  const hasDelta = delta != null && delta !== 0;
  const isGood = hasDelta && better ? (better === 'up' ? delta > 0 : delta < 0) : null;
  const color = !hasDelta ? 'var(--paper-dim)' : isGood === null ? 'var(--paper-dim)' : isGood ? 'var(--signal)' : '#FF5C72';
  const arrow = !hasDelta ? '▬' : delta > 0 ? '▲' : '▼';

  return (
    <div className="gt-card" style={{ borderLeft: `3px solid ${hasDelta ? color : 'var(--hairline)'}` }}>
      <div className="gt-card-label">{label}</div>
      <div className="gt-card-now">
        {cur == null ? '—' : fmtNum(cur)}
        {cur != null && unit && <span className="gt-unit">{unit}</span>}
      </div>
      <div className="gt-card-delta" style={{ color }}>
        {delta == null ? (
          <span title="No stored scan near that date to compare against">no baseline yet</span>
        ) : (
          <>
            <span className="gt-arrow">{arrow}</span> {fmtDelta(delta, digits)}
            {pct != null && unit !== '%' && <span className="gt-pct"> ({pct > 0 ? '+' : ''}{pct.toFixed(1)}%)</span>}
          </>
        )}
      </div>
      {base != null && <div className="gt-card-base">was {fmtNum(base)}{unit} · {dateShort(baseDate)}</div>}
    </div>
  );
}

export default function GrowthTracking({ api, handle }) {
  const [data, setData] = useState(null);
  const [err, setErr] = useState('');
  const [baselineIdx, setBaselineIdx] = useState(0);

  useEffect(() => {
    if (!handle) return;
    let alive = true;
    setErr('');
    setData(null);
    fetch(`${api}/api/growth-tracking?username=${encodeURIComponent(handle)}`)
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error('Could not load growth tracking'))))
      .then((d) => alive && setData(d))
      .catch((e) => alive && setErr(e.message));
    return () => { alive = false; };
  }, [api, handle]);

  if (!handle) return null;
  if (err) return <p className="muted">{err}</p>;
  if (!data) return <p className="muted">Loading growth tracking…</p>;

  /* --- Not enough history: honest starter state --- */
  if (!data.enough_history) {
    return (
      <div className="gt-root">
        <div className="gt-empty">
          <p className="gt-empty-title">Growth tracking starts today</p>
          <p className="muted">
            This agent has <b>{data.scan_count}</b> stored scan{data.scan_count === 1 ? '' : 's'} of
            @{handle}{data.scan_count > 0 && <> — the first on {dateShort(data.first_scan)} ({timeAgo(data.first_scan)})</>}.
            Analyze this handle again after a week or a month, and this section will show exactly
            what grew — followers, engagement, posting pace — by comparing each new scan against
            the stored ones. Nothing is estimated: only real fetched numbers are tracked.
          </p>
        </div>
      </div>
    );
  }

  const { latest, baselines, history } = data;
  const available = baselines.map((b, i) => ({ ...b, i })).filter((b) => b.available);
  const chosen = available.find((b) => b.i === baselineIdx) || available[available.length - 1];
  const d = chosen?.deltas || {};
  const v = chosen?.values || {};

  const cards = [
    { label: 'Followers', cur: latest.followers, base: v.followers, delta: d.followers, pct: chosen?.followers_delta_pct, better: 'up' },
    { label: 'Engagement rate', cur: latest.engagement_rate, base: v.engagement_rate, delta: d.engagement_rate, unit: '%', digits: 2, better: 'up' },
    { label: 'Avg likes / post', cur: latest.avg_likes, base: v.avg_likes, delta: d.avg_likes, better: 'up' },
    { label: 'Avg comments / post', cur: latest.avg_comments, base: v.avg_comments, delta: d.avg_comments, digits: 1, better: 'up' },
    { label: 'Posts on account', cur: latest.posts_count, base: v.posts_count, delta: d.posts_count, better: 'up' },
    { label: 'Posting pace', cur: latest.posting_frequency_per_week, base: v.posting_frequency_per_week, delta: d.posting_frequency_per_week, unit: '/wk', digits: 1, better: 'up' },
  ];

  return (
    <div className="gt-root">
      {/* Verdict — the plain-language answer first */}
      <div className="gt-verdict">
        <span className="gt-verdict-label">@{handle} — growth vs {chosen?.label}</span>
        <p>{data.verdict}</p>
      </div>

      {/* Baseline switch */}
      <div className="gt-switch" role="tablist" aria-label="Comparison baseline">
        {baselines.map((b) => (
          <button
            key={b.label}
            role="tab"
            aria-selected={chosen?.i === b.i}
            className={`gt-switch-btn ${chosen?.i === b.i ? 'on' : ''}`}
            disabled={!b.available}
            title={b.available ? `Compare against the stored scan from ${timeAgo(b.scanned_at)}` : 'No stored scan near that date'}
            onClick={() => setBaselineIdx(b.i)}
          >
            {b.label}
          </button>
        ))}
      </div>

      {/* Delta cards */}
      <div className="gt-grid">
        {cards.map((c) => (
          <DeltaCard key={c.label} {...c} baseDate={chosen?.scanned_at} />
        ))}
      </div>

      {/* The real scan history behind the comparison */}
      {history?.length > 0 && (
        <>
          <p className="gt-history-title">Stored scan history ({history.length} searches by the agent):</p>
          <div className="gt-history">
            {history.map((s, i) => (
              <div className="gt-history-row" key={i}>
                <span className="gt-history-date">{dateShort(s.scanned_at)}</span>
                <span className="gt-history-val">{fmtNum(s.followers)} followers</span>
                <span className="gt-history-val">{s.engagement_rate}% ER</span>
                <span className="gt-history-val">{fmtNum(s.avg_likes)} avg likes</span>
                <span className="gt-history-val">{s.posting_frequency_per_week}/wk</span>
                {s.followers_delta !== 0 && (
                  <span className={`gt-history-delta ${s.followers_delta > 0 ? 'up' : 'down'}`}>
                    {s.followers_delta > 0 ? '▲' : '▼'} {fmtDelta(s.followers_delta)}
                  </span>
                )}
              </div>
            ))}
          </div>
        </>
      )}
    </div>
  );
}
