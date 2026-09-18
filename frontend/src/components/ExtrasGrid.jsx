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

import ReelTimingView from './ReelTiming.jsx';
import HashtagSuggestionView from './HashtagSuggestion.jsx';

export default function ExtrasGrid({ bio, hashtags, reelTiming, hashtagSuggestions }) {
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
    </div>
  );
}
