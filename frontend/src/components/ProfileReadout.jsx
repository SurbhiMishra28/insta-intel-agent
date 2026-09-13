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
          {profile.data_age_hours != null && (
            <span
              title={`Live providers were unavailable — showing the last real fetch for this account (${profile.data_age_hours}h ago)`}
              style={{
                marginLeft: 6,
                fontSize: 10,
                fontWeight: 700,
                color: '#0F1115',
                background: '#E6AA28',
                borderRadius: 8,
                padding: '2px 7px',
                verticalAlign: 'middle',
              }}
            >
              DATA {profile.data_age_hours >= 48 ? `${Math.round(profile.data_age_hours / 24)}d` : `${Math.round(profile.data_age_hours)}h`} OLD
            </span>
          )}
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
