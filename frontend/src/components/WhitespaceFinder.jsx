import { useState } from 'react';

const gapChip = {
  untouched: { bg: '#ff2e63', label: 'untouched' },
  underused: { bg: '#ffc107', label: 'underused' },
  overused: { bg: '#5352ed', label: 'over-indexed' },
};

const formatChip = {
  reel: '#3ddc97',
  carousel: '#7c5cff',
  image: '#5352ed',
  video: '#5352ed',
  story: '#ff2e63',
};

export default function WhitespaceFinder({ data }) {
  const [activeTab, setActiveTab] = useState('gaps'); // 'gaps' | 'captions' | 'coverage'
  const [copiedIdx, setCopiedIdx] = useState(null);

  const copyCaption = async (cap, idx) => {
    const text = [cap.caption, (cap.hashtags || []).join(' ')].filter(Boolean).join('\n\n');
    try {
      await navigator.clipboard.writeText(text);
      setCopiedIdx(idx);
      setTimeout(() => setCopiedIdx(null), 1600);
    } catch {
      /* clipboard unavailable — no-op */
    }
  };

  const tabs = [
    { key: 'gaps', label: `Whitespace (${data.whitespace?.length || 0})` },
    { key: 'captions', label: `Captions (${data.captions?.length || 0})` },
    { key: 'coverage', label: 'Coverage' },
  ];

  const maxShare = Math.max(1, ...(data.coverage || []).map((c) => c.share_pct || 0));

  return (
    <div>
      {data.summary && (
        <p className="report-summary" style={{ marginBottom: 16 }}>{data.summary}</p>
      )}

      {/* Tab switcher */}
      <div style={{ display: 'flex', gap: 4, marginBottom: 20, background: '#1D2023', borderRadius: 6, padding: 4 }}>
        {tabs.map((t) => (
          <button
            key={t.key}
            onClick={() => setActiveTab(t.key)}
            style={{
              background: activeTab === t.key ? '#23272B' : 'transparent',
              border: 'none',
              color: activeTab === t.key ? '#E8EAED' : '#9CA1A6',
              padding: '8px 16px',
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

      {/* ---- Whitespace areas ---- */}
      {activeTab === 'gaps' && (
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(320px, 1fr))', gap: 14 }}>
          {(data.whitespace || []).map((w, i) => {
            const chip = gapChip[w.gap_type] || gapChip.underused;
            return (
              <div key={i} style={{ border: '1px solid #2A2F3A', borderRadius: 8, padding: '14px 16px', background: '#141824' }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: 8, marginBottom: 8 }}>
                  <strong style={{ fontSize: 14, fontFamily: 'var(--font-display)' }}>{w.theme}</strong>
                  <span style={{
                    background: chip.bg, color: '#fff', fontSize: 10, fontWeight: 700,
                    padding: '2px 8px', borderRadius: 10, textTransform: 'uppercase', letterSpacing: 0.5,
                    whiteSpace: 'nowrap',
                  }}>
                    {chip.label}
                  </span>
                </div>
                <p style={{ fontSize: 12.5, color: '#9CA1A6', lineHeight: 1.6, margin: '0 0 10px' }}>{w.description}</p>
                <div style={{
                  background: '#1B1F2A', borderLeft: '3px solid #C6FF4E', borderRadius: 4,
                  padding: '8px 10px', marginBottom: 10,
                }}>
                  <p style={{ fontSize: 12, color: '#E8EAED', lineHeight: 1.55, margin: 0 }}>
                    <span style={{ color: '#C6FF4E', fontWeight: 700 }}>Do this: </span>{w.opportunity}
                  </p>
                </div>
                <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6, alignItems: 'center' }}>
                  <span style={{
                    background: formatChip[w.suggested_format] || '#3A3F45', color: '#fff',
                    fontSize: 10, fontWeight: 700, padding: '2px 8px', borderRadius: 10, textTransform: 'uppercase',
                  }}>
                    {w.suggested_format}
                  </span>
                  {(w.hashtags || []).slice(0, 4).map((h) => (
                    <span key={h} style={{ fontSize: 11, color: '#9CA1A6' }}>{h}</span>
                  ))}
                  {w.rivals_own_it && (
                    <span style={{ fontSize: 11, color: '#ff2e63', fontWeight: 600, marginLeft: 'auto' }}>
                      ⚑ rivals already own this
                    </span>
                  )}
                </div>
              </div>
            );
          })}
          {(data.whitespace || []).length === 0 && (
            <p className="report-summary">No significant whitespace — the content mix covers the standard themes well.</p>
          )}
        </div>
      )}

      {/* ---- Caption suggestions ---- */}
      {activeTab === 'captions' && (
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(320px, 1fr))', gap: 14 }}>
          {(data.captions || []).map((cap, i) => (
            <div key={i} style={{ border: '1px solid #2A2F3A', borderRadius: 8, padding: '14px 16px', background: '#141824', display: 'flex', flexDirection: 'column' }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 8, marginBottom: 10 }}>
                <strong style={{ fontSize: 14, fontFamily: 'var(--font-display)' }}>{cap.title}</strong>
                <span style={{
                  background: formatChip[cap.format] || '#3A3F45', color: '#fff',
                  fontSize: 10, fontWeight: 700, padding: '2px 8px', borderRadius: 10, textTransform: 'uppercase',
                }}>
                  {cap.format}
                </span>
              </div>
              <p style={{
                fontSize: 12.5, color: '#E8EAED', lineHeight: 1.65, margin: '0 0 10px',
                whiteSpace: 'pre-wrap', background: '#1B1F2A', borderRadius: 6, padding: '10px 12px',
                border: '1px solid #2A2F3A',
              }}>
                {cap.caption}
              </p>
              {(cap.hashtags || []).length > 0 && (
                <p style={{ fontSize: 11.5, color: '#3ddc97', margin: '0 0 10px', lineHeight: 1.5 }}>
                  {cap.hashtags.join(' ')}
                </p>
              )}
              {cap.best_time_hint && (
                <p style={{ fontSize: 11.5, color: '#9CA1A6', margin: '0 0 6px' }}>🕒 {cap.best_time_hint}</p>
              )}
              {cap.why && (
                <p style={{ fontSize: 11.5, color: '#9CA1A6', margin: '0 0 10px', fontStyle: 'italic', lineHeight: 1.5 }}>
                  {cap.why}
                </p>
              )}
              <button
                onClick={() => copyCaption(cap, i)}
                style={{
                  marginTop: 'auto', background: copiedIdx === i ? '#C6FF4E' : 'transparent',
                  color: copiedIdx === i ? '#15171A' : '#C6FF4E',
                  border: '1px solid #C6FF4E', borderRadius: 4, padding: '7px 0',
                  fontSize: 12.5, fontWeight: 700, cursor: 'pointer',
                }}
              >
                {copiedIdx === i ? '✓ Copied' : 'Copy caption + hashtags'}
              </button>
            </div>
          ))}
        </div>
      )}

      {/* ---- Theme coverage ---- */}
      {activeTab === 'coverage' && (
        <div>
          <p className="report-summary" style={{ marginBottom: 16 }}>
            Share of the last {data.main?.profile?.recent_posts?.length || 0} posts per content theme,
            with per-theme engagement vs the account-wide average.
          </p>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
            {(data.coverage || []).map((c, i) => {
              const chip = c.post_count === 0
                ? gapChip.untouched
                : c.verdict === 'overused' ? gapChip.overused
                : c.verdict === 'underused' ? gapChip.underused : null;
              const beatsAvg = c.avg_engagement > c.avg_engagement_all;
              return (
                <div key={i} style={{ border: '1px solid #2A2F3A', borderRadius: 8, padding: '12px 16px', background: '#141824' }}>
                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 8, marginBottom: 6 }}>
                    <strong style={{ fontSize: 13 }}>{c.theme}</strong>
                    <div style={{ display: 'flex', gap: 6, alignItems: 'center' }}>
                      <span style={{ fontSize: 11.5, color: beatsAvg ? '#3ddc97' : '#9CA1A6' }}>
                        {c.avg_engagement} eng {beatsAvg ? '↑' : ''} vs {c.avg_engagement_all} avg
                      </span>
                      {chip && (
                        <span style={{
                          background: chip.bg, color: '#fff', fontSize: 9.5, fontWeight: 700,
                          padding: '2px 7px', borderRadius: 10, textTransform: 'uppercase',
                        }}>
                          {chip.label}
                        </span>
                      )}
                    </div>
                  </div>
                  <div style={{ height: 8, background: '#1D2023', borderRadius: 4, overflow: 'hidden' }}>
                    <div style={{
                      width: `${Math.max(c.share_pct, c.post_count > 0 ? 4 : 0)}%`,
                      height: '100%',
                      background: c.post_count === 0 ? '#2A2F3A' : beatsAvg ? '#C6FF4E' : '#7c5cff',
                      borderRadius: 4,
                    }} />
                  </div>
                  <div style={{ display: 'flex', justifyContent: 'space-between', marginTop: 5 }}>
                    <span style={{ fontSize: 11, color: '#9CA1A6' }}>
                      {c.post_count} post{c.post_count === 1 ? '' : 's'} · {c.share_pct}%
                    </span>
                    {c.example_caption && (
                      <span style={{ fontSize: 11, color: '#6B7280', fontStyle: 'italic', maxWidth: '60%', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                        “{c.example_caption}”
                      </span>
                    )}
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      )}
    </div>
  );
}
