export default function RankingBars({ ranking, allInsights, mainUsername }) {
  const scoreOf = (username) => {
    const insight = allInsights.find((i) => i.profile.username === username);
    if (!insight) return 0;
    // Backend-computed size-aware score (same value that orders `ranking`).
    if (insight.account_score != null) return insight.account_score;
    // Legacy fallback for responses computed before the backend score existed.
    const m = insight.metrics;
    return Math.round(m.engagement_rate * 10 + m.posting_frequency_per_week * 2 + (insight.profile.is_verified ? 5 : 0));
  };
  const maxScore = Math.max(...ranking.map(scoreOf), 1);

  return (
    <div>
      {ranking.map((username, idx) => {
        const score = scoreOf(username);
        const isYou = username === mainUsername;
        return (
          <div className="rank-row" key={username}>
            <span className="rank-position">{String(idx + 1).padStart(2, '0')}</span>
            <span className={`rank-name ${isYou ? 'you' : ''}`}>
              @{username}{isYou ? ' (you)' : ''}
            </span>
            <div className="rank-bar-track">
              <div
                className={`rank-bar-fill ${isYou ? 'you' : ''}`}
                style={{ width: `${(score / maxScore) * 100}%` }}
              />
            </div>
            <span className="rank-score">{score} pts</span>
          </div>
        );
      })}
    </div>
  );
}
