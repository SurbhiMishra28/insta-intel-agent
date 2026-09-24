import { useState } from 'react';

/*
 * IntelGrid — the six new deep-intel features in one tabbed grid:
 *   audience · trending topics · rival content · viral hooks ·
 *   top content · rival growth tracking
 *
 * Data comes bundled in the full dashboard response (d.intel). Each tab
 * renders only when its section exists; thin-data sections show their
 * honest empty state straight from the backend.
 */

const card = {
  border: '1px solid var(--hairline)',
  borderRadius: 8,
  padding: '14px 16px',
  background: 'var(--panel)',
};

const label = {
  fontSize: 11,
  color: 'var(--paper-dim)',
  textTransform: 'uppercase',
  letterSpacing: 0.5,
  fontWeight: 600,
  margin: '0 0 4px',
};

const chip = (color = 'var(--panel-raised)', fg = 'var(--signal)') => ({
  fontSize: 10.5,
  padding: '2px 8px',
  borderRadius: 10,
  background: color,
  color: fg,
  border: '1px solid var(--hairline)',
  fontWeight: 600,
});

function Momentum({ m }) {
  const map = {
    rising: { c: 'var(--signal)', t: '▲ rising' },
    fading: { c: 'var(--paper-dim)', t: '▼ fading' },
    steady: { c: '#ffc107', t: '■ steady' },
  };
  const s = map[m] || map.steady;
  return (
    <span style={{ fontSize: 11, color: s.c, fontWeight: 700, whiteSpace: 'nowrap' }}>{s.t}</span>
  );
}

function Empty({ children }) {
  return <p className="muted" style={{ fontSize: 13 }}>{children}</p>;
}

export default function IntelGrid({ intel }) {
  const [tab, setTab] = useState('audience');
  if (!intel) return null;

  const tabs = [
    { key: 'audience', label: 'Audience', ok: !!intel.audience },
    { key: 'trending', label: 'Trending topics', ok: !!intel.trending },
    { key: 'rivals', label: 'Rival content', ok: !!intel.rival_content },
    { key: 'hooks', label: 'Viral hooks', ok: !!intel.hooks },
    { key: 'top', label: 'Top content', ok: !!intel.top_content },
    { key: 'growth', label: 'Rival growth', ok: !!intel.rival_growth },
  ].filter((t) => t.ok);

  if (!tabs.length) return null;
  const active = tabs.find((t) => t.key === tab) ? tab : tabs[0].key;

  return (
    <div>
      <div style={{ display: 'flex', gap: 4, flexWrap: 'wrap', marginBottom: 18, background: 'var(--panel)', borderRadius: 6, padding: 4 }}>
        {tabs.map((t) => (
          <button
            key={t.key}
            onClick={() => setTab(t.key)}
            style={{
              background: active === t.key ? 'var(--panel-raised)' : 'transparent',
              border: 'none',
              color: active === t.key ? '#E8EAED' : 'var(--paper-dim)',
              padding: '8px 14px',
              borderRadius: 4,
              fontSize: 13,
              fontWeight: 600,
              cursor: 'pointer',
            }}
          >
            {t.label}
          </button>
        ))}
      </div>

      {/* ---------------- Audience analysis ---------------- */}
      {active === 'audience' && (
        <AudienceTab a={intel.audience} />
      )}

      {/* ---------------- Trending topics ---------------- */}
      {active === 'trending' && (
        <TrendingTab t={intel.trending} />
      )}

      {/* ---------------- Rival content ---------------- */}
      {active === 'rivals' && (
        <RivalContentTab rc={intel.rival_content} />
      )}

      {/* ---------------- Viral hooks ---------------- */}
      {active === 'hooks' && (
        <HooksTab h={intel.hooks} />
      )}

      {/* ---------------- Top content ---------------- */}
      {active === 'top' && (
        <TopContentTab tc={intel.top_content} />
      )}

      {/* ---------------- Rival growth ---------------- */}
      {active === 'growth' && (
        <RivalGrowthTab rg={intel.rival_growth} />
      )}
    </div>
  );
}

function AudienceTab({ a }) {
  if (!a) return <Empty>No audience analysis available.</Empty>;
  const maxEng = Math.max(1, ...(a.active_hours || []).map((h) => h.avg_engagement));
  return (
    <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(300px, 1fr))', gap: 14 }}>
      <div style={{ ...card, gridColumn: '1 / -1' }}>
        <p style={label}>Read on this audience</p>
        <p className="report-summary" style={{ margin: 0 }}>{a.summary}</p>
        <p style={{ fontSize: 12.5, color: 'var(--paper-dim)', margin: '10px 0 0', lineHeight: 1.6 }}>{a.audience_profile}</p>
      </div>

      <div style={card}>
        <p style={label}>When they engage (UTC windows)</p>
        {(a.active_hours || []).length === 0 && <Empty>No timestamped posts in the sample.</Empty>}
        {(a.active_hours || []).slice(0, 5).map((h) => (
          <div key={h.hour} style={{ marginBottom: 10 }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 12.5, marginBottom: 3 }}>
              <span>{h.label}</span>
              <span style={{ color: 'var(--paper-dim)' }}>{h.avg_engagement.toLocaleString('en-US', { maximumFractionDigits: 0 })} avg · {h.samples} post{s_(h.samples)}</span>
            </div>
            <div style={{ height: 6, background: 'var(--panel-raised)', borderRadius: 3 }}>
              <div style={{ height: 6, width: `${Math.max(6, (h.avg_engagement / maxEng) * 100)}%`, background: 'var(--signal)', borderRadius: 3 }} />
            </div>
          </div>
        ))}
      </div>

      <div style={card}>
        <p style={label}>Format affinity (100 = account avg)</p>
        {(a.format_affinity || []).map((f) => (
          <div key={f.format} style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', fontSize: 13, padding: '6px 0', borderBottom: '1px solid var(--hairline)' }}>
            <span style={{ textTransform: 'capitalize' }}>{f.format}</span>
            <span>
              <span style={{ fontWeight: 700, color: f.engagement_index >= 110 ? 'var(--signal)' : f.engagement_index <= 90 ? 'var(--paper-dim)' : '#E8EAED' }}>
                {f.engagement_index}%
              </span>
              <span style={{ color: 'var(--paper-dim)', fontSize: 11.5 }}> · {f.posts} post{s_(f.posts)}</span>
            </span>
          </div>
        ))}
        {(a.format_affinity || []).length === 0 && <Empty>No format data.</Empty>}
      </div>

      <div style={card}>
        <p style={label}>Engagement quality</p>
        <p style={{ fontSize: 13, lineHeight: 1.6, margin: 0 }}>{a.engagement_quality}</p>
        {(a.niche_signals || []).length > 0 && (
          <>
            <p style={{ ...label, marginTop: 14 }}>Niche signals</p>
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: 4 }}>
              {[...new Set(a.niche_signals)].map((s) => (
                <span key={s} className="hashtag-chip" style={{ fontSize: 10.5 }}>{s}</span>
              ))}
            </div>
          </>
        )}
      </div>

      {(a.caveats || []).length > 0 && (
        <div style={{ ...card, gridColumn: '1 / -1', borderLeft: '3px solid #ffc107' }}>
          <p style={label}>What this can NOT know</p>
          {a.caveats.map((c, i) => (
            <p key={i} style={{ fontSize: 12, color: 'var(--paper-dim)', margin: '0 0 6px', lineHeight: 1.5 }}>{c}</p>
          ))}
        </div>
      )}
    </div>
  );
}

function TrendingTab({ t }) {
  if (!t) return <Empty>No trending analysis available.</Empty>;
  if (!t.enough_data || !(t.topics || []).length) return <Empty>{t.summary}</Empty>;
  return (
    <div>
      <p className="report-summary" style={{ marginBottom: 14 }}>{t.summary}</p>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(280px, 1fr))', gap: 14 }}>
        {t.topics.map((tp) => (
          <div key={tp.topic} style={card}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 6 }}>
              <strong style={{ fontSize: 14, fontFamily: 'var(--font-display)', textTransform: 'capitalize' }}>{tp.topic}</strong>
              <Momentum m={tp.momentum} />
            </div>
            <p style={{ fontSize: 12.5, color: 'var(--paper-dim)', margin: '0 0 8px' }}>
              {tp.mentions} post{s_(tp.mentions)} · avg {fmtN(tp.avg_engagement)} engagement
              {tp.avg_engagement_overall > 0 && (
                <> · {tp.avg_engagement >= tp.avg_engagement_overall ? '+' : ''}{(((tp.avg_engagement - tp.avg_engagement_overall) / tp.avg_engagement_overall) * 100).toFixed(0)}% vs account avg</>
              )}
            </p>
            {tp.sample_caption && (
              <p style={{ fontSize: 12.5, color: 'var(--paper)', fontStyle: 'italic', margin: '0 0 8px', padding: '6px 10px', background: 'var(--panel-raised)', borderRadius: 4 }}>
                “{tp.sample_caption}”
              </p>
            )}
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: 4 }}>
              {[...new Set(tp.hashtags || [])].slice(0, 5).map((h) => (
                <span key={h} className="hashtag-chip" style={{ fontSize: 10.5 }}>{h}</span>
              ))}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

function RivalContentTab({ rc }) {
  if (!rc) return <Empty>No competitor content analysis available.</Empty>;
  if (!rc.enough_data || !(rc.rivals || []).length) return <Empty>{rc.summary}</Empty>;
  return (
    <div>
      <p className="report-summary" style={{ marginBottom: 14 }}>{rc.summary}</p>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(300px, 1fr))', gap: 14 }}>
        {rc.rivals.map((rv) => {
          const cmp = (rc.comparisons || []).find((c) => c.username === rv.username);
          return (
            <div key={rv.username} style={card}>
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', marginBottom: 4 }}>
                <strong style={{ fontSize: 14, fontFamily: 'var(--font-display)' }}>@{rv.username}</strong>
                {cmp && cmp.engagement_gap_pct !== 0 && (
                  <span style={{ fontSize: 11.5, fontWeight: 700, color: cmp.engagement_gap_pct > 0 ? 'var(--acc-rose)' : 'var(--signal)' }}>
                    {cmp.engagement_gap_pct > 0 ? '+' : ''}{cmp.engagement_gap_pct}% ER vs you
                  </span>
                )}
              </div>
              <p style={{ fontSize: 12, color: 'var(--paper-dim)', margin: '0 0 10px' }}>
                {fmtBigN(rv.followers)} followers · {rv.posts_sampled} post{s_(rv.posts_sampled)} sampled
                {rv.signature_theme ? <> · signature: <b style={{ color: 'var(--paper)' }}>{rv.signature_theme}</b></> : null}
              </p>

              <p style={label}>Format mix</p>
              <div style={{ display: 'flex', height: 8, borderRadius: 4, overflow: 'hidden', marginBottom: 10 }}>
                {Object.entries(rv.format_mix || {}).map(([f, pct]) => (
                  <div key={f} title={`${f} ${pct}%`} style={{ width: `${pct}%`, background: fmtColor(f) }} />
                ))}
              </div>
              <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8, fontSize: 11, color: 'var(--paper-dim)', marginBottom: 10 }}>
                {Object.entries(rv.format_mix || {}).map(([f, pct]) => (
                  <span key={f}><span style={{ color: fmtColor(f) }}>■</span> {f} {pct}%</span>
                ))}
              </div>

              {cmp && cmp.formats_you_miss.length > 0 && (
                <p style={{ fontSize: 12.5, margin: '0 0 6px' }}>
                  <b style={{ color: 'var(--signal)' }}>Formats you miss:</b> {cmp.formats_you_miss.join(', ')}
                </p>
              )}
              {cmp && cmp.hashtags_they_own.length > 0 && (
                <>
                  <p style={label}>Tags they own (absent from your sample)</p>
                  <div style={{ display: 'flex', flexWrap: 'wrap', gap: 4 }}>
                    {[...new Set(cmp.hashtags_they_own)].map((h) => (
                      <span key={h} className="hashtag-chip" style={{ fontSize: 10.5, background: 'var(--panel-raised)' }}>{h}</span>
                    ))}
                  </div>
                </>
              )}
              {cmp && cmp.theme_gap && (
                <p style={{ fontSize: 12, color: 'var(--paper-dim)', margin: '10px 0 0', lineHeight: 1.5 }}>{cmp.theme_gap}</p>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}

function HooksTab({ h }) {
  if (!h) return <Empty>No hook analysis available.</Empty>;
  if (!h.enough_data || !(h.hooks || []).length) return <Empty>{h.summary}</Empty>;
  return (
    <div>
      <p className="report-summary" style={{ marginBottom: 14 }}>{h.summary}</p>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(300px, 1fr))', gap: 14 }}>
        {h.hooks.map((hk, i) => (
          <div key={i} style={card}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 8 }}>
              <span style={chip(hk.source === 'own' ? 'rgba(124,92,255,.15)' : hk.source === 'rival' ? 'rgba(243,72,104,.15)' : 'var(--panel-raised)', hk.source === 'own' ? 'var(--acc-violet)' : hk.source === 'rival' ? 'var(--acc-rose)' : 'var(--paper-dim)')}>
                {hk.source === 'own' ? 'proven on you' : hk.source === 'rival' ? `rival @${hk.source_username}` : 'pattern'}
              </span>
              {hk.engagement_index !== 100 && (
                <span style={{ fontSize: 11.5, color: hk.engagement_index >= 100 ? 'var(--signal)' : 'var(--paper-dim)', fontWeight: 700 }}>
                  {hk.engagement_index}% of avg
                </span>
              )}
            </div>
            <p style={{ fontSize: 14, color: '#E8EAED', fontStyle: 'italic', lineHeight: 1.5, margin: '0 0 10px', padding: '10px 12px', background: 'var(--panel-raised)', borderRadius: 4, borderLeft: '3px solid var(--signal)' }}>
              “{hk.hook}”
            </p>
            {hk.ready_caption && hk.ready_caption !== hk.hook && (
              <p style={{ fontSize: 12.5, color: 'var(--paper)', margin: '0 0 8px' }}>Ready caption: {hk.ready_caption}</p>
            )}
            <p style={{ fontSize: 12, color: 'var(--paper-dim)', margin: 0, lineHeight: 1.5 }}>
              <b style={{ color: 'var(--signal)' }}>Why:</b> {hk.why_it_works}
            </p>
          </div>
        ))}
      </div>
    </div>
  );
}

function TopContentTab({ tc }) {
  if (!tc) return <Empty>No top-content analysis available.</Empty>;
  if (!tc.enough_data || !(tc.items || []).length) return <Empty>{tc.summary}</Empty>;
  return (
    <div>
      <p className="report-summary" style={{ marginBottom: 14 }}>
        {tc.summary}
        {tc.best_format && <> Best format right now: <b style={{ textTransform: 'capitalize' }}>{tc.best_format}</b>.</>}
      </p>
      {(tc.items || []).map((it) => (
        <div key={it.rank} style={{ ...card, marginBottom: 12 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 6 }}>
            <span style={{ fontWeight: 800, fontSize: 16, color: 'var(--signal)', fontFamily: 'var(--font-display)' }}>#{it.rank}</span>
            <span style={chip('var(--panel-raised)', 'var(--paper-dim)')}>{it.media_type}</span>
            <span style={{ fontSize: 12, color: 'var(--paper-dim)', marginLeft: 'auto' }}>
              {it.posted_days_ago != null ? `${it.posted_days_ago}d ago` : ''}
            </span>
          </div>
          <p style={{ fontSize: 13.5, color: 'var(--paper)', margin: '0 0 8px', lineHeight: 1.5 }}>{it.caption}</p>
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 14, fontSize: 12.5 }}>
            <span><b>{fmtN(it.likes)}</b> likes</span>
            <span><b>{fmtN(it.comments)}</b> comments</span>
            {it.views > 0 && <span><b>{fmtN(it.views)}</b> views</span>}
            <span style={{ color: it.engagement_index >= 100 ? 'var(--signal)' : 'var(--paper-dim)', fontWeight: 700 }}>
              {it.engagement_index}% of avg
            </span>
          </div>
          {it.why_it_won && (
            <p style={{ fontSize: 12, color: 'var(--paper-dim)', margin: '8px 0 0', lineHeight: 1.5 }}>
              <b style={{ color: 'var(--signal)' }}>Why it won:</b> {it.why_it_won}
            </p>
          )}
          {(it.hashtags || []).length > 0 && (
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: 4, marginTop: 8 }}>
              {[...new Set(it.hashtags)].map((h) => (
                <span key={h} className="hashtag-chip" style={{ fontSize: 10.5 }}>{h}</span>
              ))}
            </div>
          )}
        </div>
      ))}
    </div>
  );
}

function RivalGrowthTab({ rg }) {
  if (!rg) return <Empty>No rival growth tracking available.</Empty>;
  if (!rg.enough_data || !(rg.rivals || []).length) return <Empty>{rg.summary}</Empty>;
  const withSeries = rg.rivals.filter((e) => (e.series || []).length >= 2);
  return (
    <div>
      <p className="report-summary" style={{ marginBottom: 14 }}>{rg.summary}</p>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(300px, 1fr))', gap: 14 }}>
        {rg.rivals.map((e) => {
          const first = e.series?.[0];
          const last = e.series?.[e.series.length - 1];
          const growth = e.followers_change;
          return (
            <div key={e.username} style={card}>
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', marginBottom: 4 }}>
                <strong style={{ fontSize: 14, fontFamily: 'var(--font-display)' }}>@{e.username}</strong>
                {growth != null ? (
                  <span style={{ fontSize: 12.5, fontWeight: 800, color: growth >= 0 ? 'var(--signal)' : 'var(--acc-rose)' }}>
                    {growth >= 0 ? '+' : ''}{fmtBigN(growth)} followers
                  </span>
                ) : (
                  <span style={{ fontSize: 11.5, color: 'var(--paper-dim)' }}>baseline scan</span>
                )}
              </div>
              <p style={{ fontSize: 12, color: 'var(--paper-dim)', margin: '0 0 8px' }}>
                {fmtBigN(e.followers_now)} followers now · {e.scans} scan{s_(e.scans)}
                {e.er_change != null && <> · ER {e.er_change >= 0 ? '+' : ''}{e.er_change.toFixed(2)}</>}
              </p>
              {first && last && first.day !== last.day && (
                <MiniSpark series={e.series} />
              )}
              <p style={{ fontSize: 11.5, color: 'var(--paper-dim)', margin: '8px 0 0', fontStyle: 'italic' }}>{e.note}</p>
            </div>
          );
        })}
      </div>
      {withSeries.length === 0 && (
        <p style={{ fontSize: 12, color: 'var(--paper-dim)', marginTop: 10 }}>
          Tip: re-run the analysis in a few days — each scan adds a point to every rival's trajectory.
        </p>
      )}
    </div>
  );
}

function MiniSpark({ series }) {
  const pts = (series || []).slice(-14);
  if (pts.length < 2) return null;
  const vals = pts.map((p) => p.followers);
  const min = Math.min(...vals);
  const max = Math.max(...vals);
  const span = max - min || 1;
  const W = 260, H = 36;
  const path = pts
    .map((p, i) => `${i === 0 ? 'M' : 'L'}${(i / (pts.length - 1)) * W},${H - 4 - ((p.followers - min) / span) * (H - 8)}`)
    .join(' ');
  const up = pts[pts.length - 1].followers >= pts[0].followers;
  return (
    <div style={{ marginTop: 6 }}>
      <svg width="100%" height={H} viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none">
        <path d={path} fill="none" stroke={up ? 'var(--signal)' : 'var(--acc-rose)'} strokeWidth="2" />
      </svg>
      <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 10.5, color: 'var(--paper-dim)' }}>
        <span>{pts[0].day}</span>
        <span>{pts[pts.length - 1].day}</span>
      </div>
    </div>
  );
}

/* ---------- tiny helpers ---------- */
function s_(n) { return n === 1 ? '' : 's'; }
function fmtN(n) {
  if (n == null) return '—';
  return Number(n).toLocaleString('en-US', { maximumFractionDigits: 0 });
}
function fmtBigN(n) {
  if (n == null) return '—';
  const abs = Math.abs(n);
  if (abs >= 1e6) return (n / 1e6).toFixed(1).replace(/\.0$/, '') + 'M';
  if (abs >= 1e3) return (n / 1e3).toFixed(1).replace(/\.0$/, '') + 'K';
  return String(n);
}
function fmtColor(f) {
  return { image: 'var(--acc-violet)', reel: 'var(--acc-rose)', video: 'var(--acc-green)', carousel: '#ffc107' }[f] || '#5352ed';
}
