import FollowerGrowthIcon from './FollowerGrowthIcon.jsx';

export default function GrowthPlan({ plan }) {
  const fmtChip = (f) => {
    const colors = {
      reel: 'var(--acc-rose)', video: 'var(--acc-rose)', carousel: 'var(--acc-violet)', image: 'var(--acc-green)',
    };
    const bg = colors[f?.toLowerCase()] || 'var(--hairline)';
    return (
      <span style={{
        background: bg, color: '#fff', fontSize: 10, fontWeight: 700,
        padding: '2px 8px', borderRadius: 10, textTransform: 'uppercase', letterSpacing: 0.5,
      }}>
        {f || 'post'}
      </span>
    );
  };

  return (
    <div>
      <p className="report-summary">{plan.summary}</p>

      {plan.content_pillars?.length > 0 && (
        <>
          <h3 style={{ fontFamily: 'var(--font-display)', fontSize: 14, margin: '14px 0 6px' }}>Content pillars</h3>
          <ul className="gap-list">
            {plan.content_pillars.map((p, i) => <li key={i}>{p}</li>)}
          </ul>
        </>
      )}

      {plan.post_ideas?.length > 0 && (
        <>
          <h3 style={{ fontFamily: 'var(--font-display)', fontSize: 14, margin: '14px 0 6px' }}>
            Ready-to-make post ideas
          </h3>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(300px, 1fr))', gap: 12 }}>
            {plan.post_ideas.map((idea, i) => (
              <div key={i} style={{
                border: '1px solid var(--hairline)', borderRadius: 8, padding: '12px 14px',
                background: 'var(--panel)',
              }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 8, marginBottom: 6 }}>
                  <strong style={{ fontSize: 13.5, fontFamily: 'var(--font-display)' }}>{idea.title}</strong>
                  {fmtChip(idea.format)}
                </div>
                <p style={{ fontSize: 12.5, color: 'var(--paper-dim)', margin: '0 0 6px' }}>{idea.why}</p>
                <p style={{ fontSize: 12.5, color: 'var(--paper)', margin: '0 0 8px', fontStyle: 'italic' }}>{idea.caption_concept}</p>
                <div style={{ display: 'flex', flexWrap: 'wrap', gap: 4 }}>
                  {[...new Set(idea.hashtags || [])].slice(0, 6).map((h) => (
                    <span key={h} className="hashtag-chip" style={{ fontSize: 10.5 }}>{h}</span>
                  ))}
                </div>
              </div>
            ))}
          </div>
        </>
      )}

      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(280px, 1fr))', gap: 16, marginTop: 14 }}>
        {plan.weekly_schedule?.length > 0 && (
          <div>
            <h3 style={{ fontFamily: 'var(--font-display)', fontSize: 14, margin: '6px 0 6px' }}>Weekly posting schedule</h3>
            <ul className="gap-list">
              {plan.weekly_schedule.map((d, i) => <li key={i}>{d}</li>)}
            </ul>
          </div>
        )}

        {plan.hashtag_sets?.length > 0 && (
          <div>
            <h3 style={{ fontFamily: 'var(--font-display)', fontSize: 14, margin: '6px 0 6px' }}>Rotating hashtag sets</h3>
            {plan.hashtag_sets.map((set, i) => (
              <div key={i} style={{ marginBottom: 8 }}>
                <p style={{ fontSize: 11, color: 'var(--paper-dim)', margin: '0 0 4px' }}>Set {i + 1} — rotate to avoid flagging:</p>
                <div style={{ display: 'flex', flexWrap: 'wrap', gap: 4 }}>
                  {[...new Set(set)].map((h) => <span key={h} className="hashtag-chip" style={{ fontSize: 10.5 }}>{h}</span>)}
                </div>
              </div>
            ))}
          </div>
        )}
      </div>

      {plan.format_mix && (
        <p className="report-summary" style={{ marginTop: 12 }}><strong>Format mix:</strong> {plan.format_mix}</p>
      )}

      {plan.engagement_tactics?.length > 0 && (
        <>
          <h3 style={{ fontFamily: 'var(--font-display)', fontSize: 14, margin: '14px 0 6px' }}>
            Daily engagement tactics (what lifts likes &amp; comments)
          </h3>
          <ul className="gap-list">
            {plan.engagement_tactics.map((t, i) => <li key={i}>{t}</li>)}
          </ul>
        </>
      )}

      {plan.follower_growth_targets?.length > 0 && (
        <>
          <h3 style={{ fontFamily: 'var(--font-display)', fontSize: 14, margin: '14px 0 6px', display: 'flex', alignItems: 'center', gap: 8 }}>
            <FollowerGrowthIcon size={16} />
            Follower growth — honest targets
          </h3>
          <ul className="gap-list">
            {plan.follower_growth_targets.map((t, i) => <li key={i}>{t}</li>)}
          </ul>
        </>
      )}

      {plan.quick_wins?.length > 0 && (
        <>
          <h3 style={{ fontFamily: 'var(--font-display)', fontSize: 14, margin: '14px 0 6px' }}>Do these today</h3>
          <ul className="rec-list">
            {plan.quick_wins.map((q, i) => (
              <li key={i}>
                <span className="idx">{String(i + 1).padStart(2, '0')}</span>
                <span>{q}</span>
              </li>
            ))}
          </ul>
        </>
      )}
    </div>
  );
}
