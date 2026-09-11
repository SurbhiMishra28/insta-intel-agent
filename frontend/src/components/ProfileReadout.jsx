import FollowerGrowthIcon from './FollowerGrowthIcon.jsx';

function fmt(n) {
  if (n >= 1_000_000) return (n / 1_000_000).toFixed(1) + 'M';
  if (n >= 1_000) return (n / 1_000).toFixed(1) + 'K';
  return String(n);
}

export default function ProfileReadout({ insight }) {
  const { profile, metrics } = insight;
  return (
    <div className="readout">
      <div className="profile-id">
        <p className="handle">
          @{profile.username}
          {profile.is_verified && <span className="badge">VERIFIED</span>}
        </p>
        <p className="category">{profile.category}</p>
        <p className="bio">{profile.bio}</p>
        <div className="hashtag-row">
          {metrics.top_hashtags.map((h) => (
            <span className="hashtag-chip" key={h}>{h}</span>
          ))}
        </div>
      </div>

      <div className="stat-grid">
        <div className="stat highlight">
          <div className="num">{metrics.engagement_rate}%</div>
          <div className="label">Engagement rate</div>
        </div>
        <div className="stat">
          <div className="num" style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            {fmt(profile.followers)}
            <FollowerGrowthIcon size={17} title="Follower growth potential" />
          </div>
          <div className="label">Followers</div>
        </div>
        <div className="stat">
          <div className="num">{fmt(profile.posts_count)}</div>
          <div className="label">Total posts</div>
        </div>
        <div className="stat">
          <div className="num">{fmt(Math.round(metrics.avg_likes))}</div>
          <div className="label">Avg. likes / post</div>
        </div>
        <div className="stat">
          <div className="num">{metrics.posting_frequency_per_week}</div>
          <div className="label">Posts / week</div>
        </div>
        <div className="stat">
          <div className="num" style={{ textTransform: 'capitalize' }}>{metrics.best_content_type}</div>
          <div className="label">Top format</div>
        </div>
      </div>
    </div>
  );
}
