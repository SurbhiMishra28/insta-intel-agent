function BioOptimizerView({ bio }) {
  if (!bio) return null;
  return (
    <div>
      {bio.current_bio && (
        <p style={{ fontSize: 13, color: 'var(--paper-dim)', margin: '0 0 10px' }}>
          <strong style={{ color: 'var(--paper)' }}>Current:</strong> {bio.current_bio}
        </p>
      )}
      <div style={{
        border: '1px solid var(--signal-dim)', borderRadius: 4, padding: '12px 14px',
        background: 'rgba(198,255,78,0.04)', whiteSpace: 'pre-line',
        fontSize: 13.5, lineHeight: 1.6,
      }}>
        {bio.suggested_bio}
      </div>
      <button
        type="button"
        onClick={() => navigator.clipboard?.writeText(bio.suggested_bio)}
        style={{
          marginTop: 8, background: 'none', border: '1px solid var(--hairline)',
          color: 'var(--signal)', fontSize: 12, padding: '5px 12px', borderRadius: 3,
        }}
      >
        Copy suggested bio
      </button>
      {bio.notes?.length > 0 && (
        <ul className="gap-list" style={{ marginTop: 10 }}>
          {bio.notes.map((n, i) => <li key={i}>{n}</li>)}
        </ul>
      )}
    </div>
  );
}

function HashtagResearchView({ hashtags }) {
  if (!hashtags) return null;
  const tierColor = { rare: 'var(--acc-green)', mid: 'var(--acc-violet)', broad: 'var(--acc-rose)' };
  return (
    <div>
      <p className="report-summary">{hashtags.summary}</p>

      {hashtags.tiered?.length > 0 && (
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6, margin: '10px 0 14px' }}>
          {hashtags.tiered.map((t) => (
            <span key={t.name} style={{
              border: `1px solid ${tierColor[t.tier] || 'var(--hairline)'}`,
              color: tierColor[t.tier] || 'var(--paper-dim)',
              fontSize: 11.5, padding: '3px 9px', borderRadius: 2,
            }}>
              #{t.name}
              {t.posts_count > 0 && (
                <span style={{ opacity: 0.7, marginLeft: 4 }}>
                  {t.posts_count >= 1e6 ? `${(t.posts_count / 1e6).toFixed(1)}M`
                    : t.posts_count >= 1e3 ? `${(t.posts_count / 1e3).toFixed(0)}K` : t.posts_count}
                </span>
              )}
            </span>
          ))}
        </div>
      )}

      {hashtags.recommended_sets?.map((set, i) => (
        <div key={i} style={{ marginBottom: 8 }}>
          <p style={{ fontSize: 11, color: 'var(--paper-dim)', margin: '0 0 4px' }}>
            Recommended set {i + 1} — paste-ready:
          </p>
          <div style={{
            fontFamily: 'Consolas, monospace', fontSize: 11.5, color: 'var(--paper)',
            background: 'var(--panel)', border: '1px solid var(--hairline)',
            borderRadius: 3, padding: '8px 10px', wordBreak: 'break-all',
          }}>
            {set.join(' ')}
          </div>
        </div>
      ))}

      {hashtags.notes?.map((n, i) => (
        <p key={i} style={{ fontSize: 11.5, color: 'var(--paper-dim)', margin: '6px 0 0' }}>{n}</p>
      ))}
    </div>
  );
}

function HistoryView({ history }) {
  if (!history || history.length === 0) {
    return (
      <p style={{ fontSize: 12.5, color: 'var(--paper-dim)' }}>
        No scan history yet — every analysis is recorded, so re-scan this account
        later to see follower and engagement trends here.
      </p>
    );
  }
  const latest = history[history.length - 1];
  const maxF = Math.max(...history.map((h) => h.followers)) || 1;

  return (
    <div>
      <div style={{ display: 'flex', gap: 20, flexWrap: 'wrap', marginBottom: 12 }}>
        <div className="stat">
          <div className="num" style={{ fontSize: 20 }}>
            {latest.followers_delta > 0 ? `+${latest.followers_delta.toLocaleString()}` : latest.followers_delta.toLocaleString()}
          </div>
          <div className="label">Followers since previous scan</div>
        </div>
        <div className="stat">
          <div className="num" style={{ fontSize: 20 }}>
            {latest.er_delta > 0 ? `+${latest.er_delta}` : latest.er_delta}%
          </div>
          <div className="label">Engagement rate change</div>
        </div>
        <div className="stat">
          <div className="num" style={{ fontSize: 20 }}>{history.length}</div>
          <div className="label">Total scans recorded</div>
        </div>
      </div>

      <div style={{ display: 'flex', alignItems: 'flex-end', gap: 6, height: 70, maxWidth: 480 }}>
        {history.map((h, i) => (
          <div key={i} style={{ flex: 1, textAlign: 'center' }} title={`${h.scanned_at}: ${h.followers.toLocaleString()} followers, ER ${h.engagement_rate}%`}>
            <div style={{
              height: `${Math.max(6, (h.followers / maxF) * 60)}px`,
              background: i === history.length - 1 ? 'var(--signal)' : 'var(--signal-dim)',
              borderRadius: 2,
            }} />
            <div style={{ fontSize: 9.5, color: 'var(--paper-dim)', marginTop: 3 }}>
              {new Date(h.scanned_at).toLocaleDateString(undefined, { month: 'short', day: 'numeric' })}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

import ReelTimingView from './ReelTiming.jsx';
import HashtagSuggestionView from './HashtagSuggestion.jsx';

export default function ExtrasGrid({ bio, hashtags, history, reelTiming, hashtagSuggestions }) {
  return (
    <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(320px, 1fr))', gap: 28 }}>
      {bio && (
        <div>
          <h3 style={{ fontFamily: 'var(--font-display)', fontSize: 14, margin: '0 0 10px' }}>Bio optimizer</h3>
          <BioOptimizerView bio={bio} />
        </div>
      )}
      {hashtags && (
        <div>
          <h3 style={{ fontFamily: 'var(--font-display)', fontSize: 14, margin: '0 0 10px' }}>Hashtag research (real volumes)</h3>
          <HashtagResearchView hashtags={hashtags} />
        </div>
      )}
      <div>
        <h3 style={{ fontFamily: 'var(--font-display)', fontSize: 14, margin: '0 0 10px' }}>Growth tracking</h3>
        <HistoryView history={history} />
      </div>
    </div>
  );
}
