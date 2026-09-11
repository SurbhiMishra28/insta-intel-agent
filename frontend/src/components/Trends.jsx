import { useState } from 'react';

export default function Trends({ trends }) {
  const [activeTab, setActiveTab] = useState('alerts'); // 'trending' | 'alerts' | 'ideas'

  const categoryColors = {
    visual: '#ff2e63',
    audio: '#7c5cff',
    challenge: '#ffc107',
    filter: '#3ddc97',
    format: '#5352ed',
  };

  const fmtChip = (cat) => {
    const color = categoryColors[cat?.toLowerCase()] || '#3A3F45';
    return (
      <span
        style={{
          background: color,
          color: '#fff',
          fontSize: 10,
          fontWeight: 700,
          padding: '2px 8px',
          borderRadius: 10,
          textTransform: 'uppercase',
          letterSpacing: 0.5,
        }}
      >
        {cat || 'trend'}
      </span>
    );
  };

  const tabs = [
    { key: 'trending', label: 'Trending now' },
    { key: 'alerts', label: 'Trend alerts' },
    { key: 'ideas', label: 'Post/reel ideas' },
  ];

  return (
    <div>
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

      {/* ---- Section: Trending now ---- */}
      {activeTab === 'trending' && (
        <div>
          {trends.trending && trends.trending.length > 0 ? (
            <>
              <p className="report-summary" style={{ marginBottom: 16 }}>
                These Instagram trend archetypes are active right now. Spot one that fits your niche, then check the{' '}
                <button
                  onClick={() => setActiveTab('alerts')}
                  style={{ color: '#C6FF4E', background: 'none', border: 'none', cursor: 'pointer', fontSize: 'inherit', fontWeight: 600 }}
                >
                  Trend alerts
                </button>{' '}
                tab for tailored ideas.
              </p>
              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(320px, 1fr))', gap: 14 }}>
                {trends.trending.map((t, i) => (
                  <div
                    key={i}
                    style={{
                      border: '1px solid #2A2F3A',
                      borderRadius: 8,
                      padding: '14px 16px',
                      background: '#141824',
                    }}
                  >
                    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 8 }}>
                      <strong style={{ fontSize: 14, fontFamily: 'var(--font-display)' }}>{t.name}</strong>
                      {fmtChip(t.category)}
                    </div>
                    <p style={{ fontSize: 12.5, color: '#9CA1A6', lineHeight: 1.6, margin: '0 0 10px' }}>{t.description}</p>
                    {t.is_rising !== undefined && t.is_rising && (
                      <span style={{ display: 'inline-flex', alignItems: 'center', gap: 4, fontSize: 11, color: '#C6FF4E', fontWeight: 600, marginBottom: 8 }}>
                        <span style={{ width: 6, height: 6, borderRadius: '50%', background: '#C6FF4E' }} />
                        Rising — ride it early
                      </span>
                    )}
                    <div style={{ display: 'flex', flexWrap: 'wrap', gap: 4 }}>
                      {(t.hashtags || []).slice(0, 6).map((h) => (
                        <span key={h} className="hashtag-chip" style={{ fontSize: 10.5 }}>{h}</span>
                      ))}
                    </div>
                  </div>
                ))}
              </div>
            </>
          ) : (
            <p className="loading-line">No trend data available.</p>
          )}
        </div>
      )}

      {/* ---- Section: Trend alerts ---- */}
      {activeTab === 'alerts' && (
        <div>
          {trends.alerts && trends.alerts.length > 0 ? (
            <>
              <p className="report-summary" style={{ marginBottom: 16 }}>
                Trends detected that are relevant for this account. Each alert includes a post idea, a reel idea, a caption hook, and ready-to-paste hashtags.
              </p>
              {trends.alerts.map((alert, i) => (
                <div
                  key={i}
                  style={{
                    border: '1px solid #2A2F3A',
                    borderRadius: 8,
                    padding: '18px 20px',
                    marginBottom: 16,
                    background: '#141824',
                  }}
                >
                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 6 }}>
                    <div>
                      <strong style={{ fontSize: 16, fontFamily: 'var(--font-display)', color: '#C6FF4E' }}>
                        ⚡ {alert.trend.name}
                      </strong>
                      <div style={{ marginTop: 4 }}>{fmtChip(alert.trend.category)}</div>
                    </div>
                    {alert.trend.is_rising && (
                      <span style={{ fontSize: 11, color: '#C6FF4E', fontWeight: 600, display: 'flex', alignItems: 'center', gap: 4 }}>
                        <span style={{ width: 6, height: 6, borderRadius: '50%', background: '#C6FF4E' }} />
                        Rising
                      </span>
                    )}
                  </div>

                  <p style={{ fontSize: 13, color: '#9CA1A6', margin: '8px 0 12px', lineHeight: 1.6 }}>
                    {alert.trend.description}
                  </p>

                  <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 16, marginTop: 4 }}>
                    <div>
                      <p style={{ fontSize: 11, color: '#9CA1A6', margin: '0 0 4px', textTransform: 'uppercase', letterSpacing: 0.5, fontWeight: 600 }}>Why it matters</p>
                      <p style={{ fontSize: 13, color: '#C7CCD6', lineHeight: 1.6, margin: 0 }}>{alert.why_relevant}</p>
                    </div>
                    <div>
                      <p style={{ fontSize: 11, color: '#9CA1A6', margin: '0 0 4px', textTransform: 'uppercase', letterSpacing: 0.5, fontWeight: 600 }}>Posting tip</p>
                      <p style={{ fontSize: 13, color: '#C7CCD6', lineHeight: 1.6, margin: 0 }}>{alert.posting_tip}</p>
                    </div>
                  </div>

                  <div style={{ marginTop: 14 }}>
                    <p style={{ fontSize: 11, color: '#9CA1A6', margin: '0 0 4px', textTransform: 'uppercase', letterSpacing: 0.5, fontWeight: 600 }}>Caption hook</p>
                    <p style={{ fontSize: 14, color: '#E8EAED', fontStyle: 'italic', lineHeight: 1.6, margin: '0 0 14px', padding: '10px 14px', background: '#1D2023', borderRadius: 4, borderLeft: '3px solid #C6FF4E' }}>
                      {alert.caption_hook}
                    </p>
                  </div>

                  <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 16 }}>
                    <div>
                      <p style={{ fontSize: 11, color: '#9CA1A6', margin: '0 0 4px', textTransform: 'uppercase', letterSpacing: 0.5, fontWeight: 600 }}>Post idea</p>
                      <p style={{ fontSize: 13, color: '#C7CCD6', lineHeight: 1.6, margin: 0 }}>{alert.suggested_post_idea}</p>
                    </div>
                    <div>
                      <p style={{ fontSize: 11, color: '#9CA1A6', margin: '0 0 4px', textTransform: 'uppercase', letterSpacing: 0.5, fontWeight: 600 }}>Reel idea</p>
                      <p style={{ fontSize: 13, color: '#C7CCD6', lineHeight: 1.6, margin: 0 }}>{alert.suggested_reel_idea}</p>
                    </div>
                  </div>

                  <div style={{ marginTop: 14 }}>
                    <p style={{ fontSize: 11, color: '#9CA1A6', margin: '0 0 6px', textTransform: 'uppercase', letterSpacing: 0.5, fontWeight: 600 }}>Hashtags to copy</p>
                    <div style={{ display: 'flex', flexWrap: 'wrap', gap: 4 }}>
                      {alert.recommended_hashtags.map((h) => (
                        <span key={h} className="hashtag-chip" style={{ fontSize: 11, background: '#23272B', color: '#C6FF4E', border: '1px solid #3A3F45' }}>
                          {h}
                        </span>
                      ))}
                    </div>
                  </div>
                </div>
              ))}
            </>
          ) : (
            <p className="loading-line">No trend alerts yet. Run an analysis to detect trends for this account.</p>
          )}
        </div>
      )}

      {/* ---- Section: Post/reel ideas ---- */}
      {activeTab === 'ideas' && (
        <div>
          {trends.suggestions && trends.suggestions.length > 0 ? (
            <>
              <p className="report-summary" style={{ marginBottom: 16 }}>
                Ready-to-make content ideas derived from the trends above. Each one is specific to this account's niche.
              </p>
              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(300px, 1fr))', gap: 14 }}>
                {trends.suggestions.map((s, i) => (
                  <div
                    key={i}
                    style={{
                      border: '1px solid #2A2F3A',
                      borderRadius: 8,
                      padding: '14px 16px',
                      background: '#141824',
                    }}
                  >
                    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 8 }}>
                      <strong style={{ fontSize: 14, fontFamily: 'var(--font-display)' }}>{s.title}</strong>
                      <span
                        style={{
                          background: s.format === 'reel' ? '#ff2e63' : s.format === 'carousel' ? '#7c5cff' : '#3ddc97',
                          color: '#fff',
                          fontSize: 10,
                          fontWeight: 700,
                          padding: '2px 8px',
                          borderRadius: 10,
                          textTransform: 'uppercase',
                        }}
                      >
                        {s.format}
                      </span>
                    </div>
                    <p style={{ fontSize: 12.5, color: '#9CA1A6', margin: '0 0 8px', lineHeight: 1.6 }}>{s.concept}</p>
                    <p style={{ fontSize: 12.5, color: '#C7CCD6', margin: '0 0 10px', fontStyle: 'italic', padding: '8px 10px', background: '#1D2023', borderRadius: 4 }}>
                      {s.caption}
                    </p>
                    <div style={{ display: 'flex', flexWrap: 'wrap', gap: 4 }}>
                      {(s.hashtags || []).slice(0, 8).map((h) => (
                        <span key={h} className="hashtag-chip" style={{ fontSize: 10.5 }}>{h}</span>
                      ))}
                    </div>
                    {s.why && (
                      <p style={{ fontSize: 11.5, color: '#9CA1A6', margin: '10px 0 0', lineHeight: 1.5 }}>
                        <strong style={{ color: '#C6FF4E' }}>Why:</strong> {s.why}
                      </p>
                    )}
                  </div>
                ))}
              </div>
            </>
          ) : (
            <p className="loading-line">No content ideas yet. Run an analysis to get trend-based suggestions.</p>
          )}
        </div>
      )}
    </div>
  );
}
