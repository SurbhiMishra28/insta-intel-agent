export default function ReelsInsights({ reels }) {
  if (!reels) return null;

  const stat = (label, value) => (
    <div className="stat" style={{ minWidth: 110 }}>
      <div className="num" style={{ fontSize: 22 }}>{value}</div>
      <div className="label">{label}</div>
    </div>
  );

  return (
    <div>
      <div style={{ display: 'flex', gap: 24, flexWrap: 'wrap', marginBottom: 12 }}>
        {stat('Reels in sample', reels.reels_count)}
        {stat('Avg views', reels.avg_views ? reels.avg_views.toLocaleString(undefined, { maximumFractionDigits: 0 }) : '—')}
        {stat('Avg likes / reel', reels.avg_likes_per_reel ? reels.avg_likes_per_reel.toLocaleString(undefined, { maximumFractionDigits: 0 }) : '—')}
        {stat('Like-rate / view', reels.like_rate_per_view ? `${reels.like_rate_per_view}%` : '—')}
      </div>

      <p className="report-summary">{reels.verdict}</p>

      {reels.niche_like_rate_per_view > 0 && (
        <p style={{ fontSize: 12.5, color: 'var(--paper-dim)', margin: '6px 0 10px' }}>
          Niche benchmark (researched rivals): {reels.niche_like_rate_per_view}% like-rate per view
        </p>
      )}

      {reels.tips?.length > 0 && (
        <ul className="gap-list">
          {reels.tips.map((t, i) => <li key={i}>{t}</li>)}
        </ul>
      )}
    </div>
  );
}
