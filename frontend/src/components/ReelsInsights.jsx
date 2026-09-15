export default function ReelsInsights({ reels }) {
  // No reels in the sample → nothing meaningful to show. Hide the whole card
  // instead of displaying zeros, "hidden" and "n/a" placeholders.
  if (!reels || !reels.reels_count) return null;

  const stat = (label, value, title) => (
    <div className="stat" style={{ minWidth: 110 }} title={title}>
      <div className="num" style={{ fontSize: 22 }}>{value}</div>
      <div className="label">{label}</div>
    </div>
  );

  const hasViewData = (reels.videos_with_views ?? (reels.avg_views > 0 ? reels.reels_count : 0)) > 0;

  return (
    <div>
      <div style={{ display: 'flex', gap: 24, flexWrap: 'wrap', marginBottom: 12 }}>
        {stat('Reels in sample', reels.reels_count)}
        {hasViewData
          ? stat('Avg views', reels.avg_views ? reels.avg_views.toLocaleString(undefined, { maximumFractionDigits: 0 }) : '—')
          : stat('Avg views', 'hidden', 'Instagram (or the data source) does not expose view counts for these reels — engagement below is fully real.')}
        {stat('Avg likes / reel', reels.avg_likes_per_reel ? reels.avg_likes_per_reel.toLocaleString(undefined, { maximumFractionDigits: 0 }) : '—')}
        {hasViewData
          ? stat('Like-rate / view', reels.like_rate_per_view ? `${reels.like_rate_per_view}%` : '—')
          : stat('Like-rate / view', 'n/a', 'Needs view counts, which are hidden for this content — likes/comments analysis below is real.')} 
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
