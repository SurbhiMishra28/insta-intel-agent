function impactBadge(impact) {
  const colors = {
    high: '#3ddc97',
    medium: '#7c5cff',
    low: '#ff2e63',
  };
  return (
    <span style={{
      fontSize: 10, fontWeight: 700, padding: '1px 7px', borderRadius: 8,
      background: (colors[impact] || '#9CA1A6') + '22',
      color: colors[impact] || '#9CA1A6',
    }}>
      {impact}
    </span>
  );
}

function tagChip(tag, impact, bestFor) {
  return (
    <span style={{
      fontSize: 11.5, padding: '3px 8px', borderRadius: 3,
      background: 'var(--panel)', border: '1px solid var(--hairline)',
      color: 'var(--paper)', whiteSpace: 'nowrap',
    }}>
      {tag}
      <span style={{ marginLeft: 4, opacity: 0.6, fontSize: 10 }}>{impactBadge(impact)}</span>
    </span>
  );
}

export default function HashtagSuggestionView({ hashtagSuggestions }) {
  if (!hashtagSuggestions) return null;
  const { summary, suggestions, post_set, reel_set, notes } = hashtagSuggestions;

  return (
    <div>
      <p className="report-summary">{summary}</p>

      {suggestions.length > 0 && (
        <>
          <h3 style={{ fontFamily: 'var(--font-display)', fontSize: 13, margin: '14px 0 6px' }}>
            What each tag is picked for
          </h3>
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6, marginBottom: 14 }}>
            {suggestions.map((s, i) => (
              <span key={i} style={{
                border: '1px solid var(--hairline)', borderRadius: 4, padding: '6px 10px',
                background: 'var(--panel)', minWidth: 160, flex: '1 1 160px',
              }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 2 }}>
                  <span style={{ fontSize: 12, fontWeight: 600 }}>{s.hashtag}</span>
                  {impactBadge(s.estimated_impact)}
                </div>
                <p style={{ fontSize: 11, color: 'var(--paper-dim)', margin: '2px 0 0', lineHeight: 1.4 }}>
                  {s.reason}
                </p>
                <span style={{ fontSize: 10, color: 'var(--signal)', marginTop: 3, textTransform: 'uppercase' }}>
                  best for: {s.best_for}
                </span>
              </span>
            ))}
          </div>
        </>
      )}

      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 14, marginTop: 4 }}>
        <div>
          <h3 style={{ fontFamily: 'var(--font-display)', fontSize: 13, margin: '0 0 6px' }}>
            Paste-ready set — posts (carousel / image)
          </h3>
          <div style={{
            fontFamily: 'Consolas, monospace', fontSize: 11.5, color: 'var(--paper)',
            background: 'var(--panel)', border: '1px solid var(--hairline)',
            borderRadius: 3, padding: '10px 12px', wordBreak: 'break-all', lineHeight: 1.7,
          }}>
            {post_set.join(' ')}
          </div>
          <button
            type="button"
            onClick={() => navigator.clipboard?.writeText(post_set.join(' '))}
            style={{
              marginTop: 6, background: 'none', border: '1px solid var(--hairline)',
              color: 'var(--signal)', fontSize: 11.5, padding: '4px 10px', borderRadius: 3,
            }}
          >
            Copy post set
          </button>
        </div>

        <div>
          <h3 style={{ fontFamily: 'var(--font-display)', fontSize: 13, margin: '0 0 6px' }}>
            Paste-ready set — reels
          </h3>
          <div style={{
            fontFamily: 'Consolas, monospace', fontSize: 11.5, color: 'var(--paper)',
            background: 'var(--panel)', border: '1px solid var(--hairline)',
            borderRadius: 3, padding: '10px 12px', wordBreak: 'break-all', lineHeight: 1.7,
          }}>
            {reel_set.join(' ')}
          </div>
          <button
            type="button"
            onClick={() => navigator.clipboard?.writeText(reel_set.join(' '))}
            style={{
              marginTop: 6, background: 'none', border: '1px solid var(--hairline)',
              color: 'var(--signal)', fontSize: 11.5, padding: '4px 10px', borderRadius: 3,
            }}
          >
            Copy reel set
          </button>
        </div>
      </div>

      {notes?.length > 0 && (
        <ul className="gap-list" style={{ marginTop: 12 }}>
          {notes.map((n, i) => <li key={i}>{n}</li>)}
        </ul>
      )}
    </div>
  );
}
