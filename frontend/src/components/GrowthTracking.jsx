import { useEffect, useState } from 'react';
import {
  LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer,
} from 'recharts';
import { fmtCompact } from '../format.js';

/* Growth tracking: what changed since the agent's stored searches.
   Every number comes from real recorded scans of this handle, collapsed to
   one snapshot per day — when a baseline (yesterday / 1 week / 1 month ago)
   doesn't exist yet, the card says so instead of inventing data. */

const timeAgo = (iso) => {
  if (!iso) return '';
  const h = (Date.now() - new Date(iso).getTime()) / 3.6e6;
  if (h < 1) return 'just now';
  if (h < 48) return `${Math.round(h)}h ago`;
  return `${Math.round(h / 24)}d ago`;
};

const dateShort = (iso) =>
  iso ? new Date(iso).toLocaleDateString(undefined, { month: 'short', day: 'numeric' }) : '';

const fmtNum = (n) => {
  if (n == null) return '—';
  // Non-integer metrics (avg comments 0.2, cadence 1.75) must keep their
  // decimals — rounding them to "0" made real data look missing. Big counts
  // (followers) abbreviate to K/M via the shared formatter.
  if (!Number.isInteger(n)) return n.toLocaleString(undefined, { maximumFractionDigits: 1 });
  return fmtCompact(n);
};

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

const chartAxis = { fontSize: 10, fill: 'var(--paper-dim)' };

function GrowthChart({ series }) {
  if (!series || series.length === 0) return null;
  return (
    <div className="gt-chart" style={{ marginTop: 14 }}>
      <p className="gt-history-title" style={{ margin: '0 0 6px' }}>
        Followers, day by day ({series.length} snapshot{series.length === 1 ? '' : 's'}):
      </p>
      <div style={{ width: '100%', height: 170 }}>
        <ResponsiveContainer>
          <LineChart data={series} margin={{ top: 6, right: 12, bottom: 0, left: 0 }}>
            <CartesianGrid stroke="var(--hairline)" strokeDasharray="3 3" vertical={false} />
            <XAxis
              dataKey="day"
              tick={chartAxis}
              tickFormatter={(d) => dateShort(d)}
              tickLine={false}
              axisLine={{ stroke: 'var(--hairline)' }}
              minTickGap={18}
            />
            <YAxis
              tick={chartAxis}
              tickLine={false}
              axisLine={false}
              width={46}
              domain={['auto', 'auto']}
              tickFormatter={(v) => fmtCompact(v)}
            />
            <Tooltip
              contentStyle={{
                background: 'var(--panel-raised)', border: '1px solid var(--hairline)',
                borderRadius: 4, fontSize: 12, color: 'var(--paper)',
              }}
              labelStyle={{ color: 'var(--paper-dim)' }}
              formatter={(value, name, item) => {
                const p = item?.payload || {};
                const d = p.followers_delta;
                const line = d != null && d !== 0 ? ` (${d > 0 ? '+' : '−'}${Math.abs(d).toLocaleString('en-US')} vs prev day)` : '';
                return [`${value.toLocaleString('en-US')} followers${line}`, dateShort(p.day)];
              }}
            />
            <Line
              type="monotone"
              dataKey="followers"
              stroke="var(--signal)"
              strokeWidth={2}
              dot={{ r: 3, fill: 'var(--signal)', strokeWidth: 0 }}
              activeDot={{ r: 4 }}
              isAnimationActive={false}
            />
          </LineChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}

export default function GrowthTracking({ api, handle }) {
  const [data, setData] = useState(null);
  const [err, setErr] = useState('');
  // null = auto-pick the longest available baseline (reset when the handle
  // changes); a number pins the user's manual tab choice.
  const [baselineIdx, setBaselineIdx] = useState(null);

  useEffect(() => {
    if (!handle) return;
    let alive = true;
    setErr('');
    setData(null);
    setBaselineIdx(null);
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
    const days = (data.snapshots || []).length;
    return (
      <div className="gt-root">
        <div className="gt-empty">
          <p className="gt-empty-title">Growth tracking starts today</p>
          <p className="muted">
            This agent has <b>{data.scan_count}</b> stored scan{data.scan_count === 1 ? '' : 's'} of
            @{handle}{days > 1 ? ` across ${days} different days` : ''}{data.first_scan && <> — the first on {dateShort(data.first_scan)} ({timeAgo(data.first_scan)})</>}.
            Growth is tracked day over day, so analyze this handle again tomorrow
            (or any later day) and this section will show exactly what changed —
            followers, engagement, posting pace — against today's stored numbers.
            Nothing is estimated: only real fetched numbers are tracked.
          </p>
        </div>
      </div>
    );
  }

  const { latest, baselines, snapshots, series } = data;
  if (!latest) return <p className="muted">No stored scans for @{handle} yet.</p>;

  const withIdx = baselines.map((b, i) => ({ ...b, _i: i }));
  const available = withIdx.filter((b) => b.available);
  // Default to the longest available span (month > week > yesterday) so a
  // fresh comparison shows the most meaningful story, not day-zero noise.
  const chosen =
    (baselineIdx != null && withIdx.find((b) => b._i === baselineIdx && b.available)) ||
    available[available.length - 1] ||
    null;
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

  const rows = (snapshots && snapshots.length ? snapshots : []).slice().reverse();

  return (
    <div className="gt-root">
      {/* Verdict — the plain-language answer first */}
      <div className="gt-verdict">
        <span className="gt-verdict-label">@{handle} — growth vs {chosen?.label || '—'}</span>
        <p>{data.verdict}</p>
      </div>

      {/* Baseline switch */}
      <div className="gt-switch" role="tablist" aria-label="Comparison baseline">
        {withIdx.map((b) => (
          <button
            key={b.label}
            role="tab"
            aria-selected={chosen?._i === b._i}
            className={`gt-switch-btn ${chosen?._i === b._i ? 'on' : ''}`}
            disabled={!b.available}
            title={b.available ? `Compare against the ${dateShort(b.scanned_at)} snapshot (${timeAgo(b.scanned_at)})` : 'No stored scan near that date'}
            onClick={() => setBaselineIdx(b._i)}
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

      {/* Day-scale trend behind the comparison */}
      <GrowthChart series={series} />

      {/* The real scan history behind the comparison: one row per day */}
      {rows.length > 0 && (
        <>
          <p className="gt-history-title">
            Stored scan history ({data.scan_count} searches across {rows.length} day{rows.length === 1 ? '' : 's'} — one row per day):
          </p>
          <div className="gt-history">
            {rows.map((s) => (
              <div className="gt-history-row" key={s.day}>
                <span className="gt-history-date">{dateShort(s.day)}</span>
                <span className="gt-history-val">{fmtNum(s.followers)} followers</span>
                <span className="gt-history-val">{fmtNum(s.engagement_rate)}% ER</span>
                <span className="gt-history-val">{fmtNum(s.avg_likes)} avg likes</span>
                {s.avg_comments != null && (
                  <span className="gt-history-val">{fmtNum(s.avg_comments)} avg comments</span>
                )}
                <span className="gt-history-val">{fmtNum(s.posting_frequency_per_week)}/wk</span>
                {s.scan_count > 1 && (
                  <span className="gt-history-val" title="Repeated scans this day were collapsed into one snapshot">
                    ×{s.scan_count} scans
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
