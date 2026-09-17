import FollowerGrowthIcon from './FollowerGrowthIcon.jsx';

function fmt(n) {
  if (n >= 1_000_000) return (n / 1_000_000).toFixed(1) + 'M';
  if (n >= 1_000) return (n / 1_000).toFixed(1) + 'K';
  return String(n);
}

/* Per-post engagement breakdown for the recent sample — makes the averages
   transparent: avg likes/comments are literally the sum of these rows
   divided by the count. Every number comes straight from Instagram. */
export function PostBreakdown({ profile, metrics }) {
  const posts = (profile?.recent_posts || []).filter(
    (p) => p && (p.likes > 0 || p.comments > 0 || p.id),
  );
  if (posts.length === 0) return null;
  const totalLikes = posts.reduce((s, p) => s + (p.likes || 0), 0);
  const totalComments = posts.reduce((s, p) => s + (p.comments || 0), 0);
  const postsOnly = posts.filter((p) => p.media_type === 'image' || p.media_type === 'carousel');
  const reelsOnly = posts.filter((p) => p.media_type === 'reel' || p.media_type === 'video');
  const avgOf = (items, key) =>
    items.length ? (items.reduce((s, p) => s + (p[key] || 0), 0) / items.length) : 0;
  const bfmt = metrics?.comments_by_format || null;
  return (
    <div className="post-breakdown" style={{ marginTop: 14 }}>
      <p className="section-label" style={{ marginBottom: 6 }}>
        Last {posts.length} posts &amp; reels — the data behind the averages
      </p>
      <div style={{ overflowX: 'auto' }}>
        <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12.5 }}>
          <thead>
            <tr style={{ textAlign: 'left', color: 'var(--paper-dim)', fontSize: 11 }}>
              <th style={{ padding: '4px 8px' }}>#</th>
              <th style={{ padding: '4px 8px' }}>Type</th>
              <th style={{ padding: '4px 8px' }}>Likes</th>
              <th style={{ padding: '4px 8px' }}>Comments</th>
              <th style={{ padding: '4px 8px' }}>Posted</th>
              <th style={{ padding: '4px 8px' }}>Caption</th>
            </tr>
          </thead>
          <tbody>
            {posts.map((p, i) => (
              <tr key={p.id || i} style={{ borderTop: '1px solid var(--hairline)' }}>
                <td style={{ padding: '4px 8px', color: 'var(--paper-dim)' }}>{i + 1}</td>
                <td style={{ padding: '4px 8px', textTransform: 'capitalize' }}>{p.media_type || 'post'}</td>
                <td style={{ padding: '4px 8px' }}>{(p.likes || 0).toLocaleString()}</td>
                <td style={{ padding: '4px 8px' }}>{(p.comments || 0).toLocaleString()}</td>
                <td style={{ padding: '4px 8px', color: 'var(--paper-dim)' }}>
                  {p.posted_days_ago != null ? `${p.posted_days_ago}d ago` : '—'}
                </td>
                <td style={{ padding: '4px 8px', color: 'var(--paper-dim)', maxWidth: 280, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                  {(p.caption || '').slice(0, 70) || '(no caption)'}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p style={{ fontSize: 12, color: 'var(--paper-dim)', margin: '6px 0 0' }}>
        Avg likes = {totalLikes.toLocaleString()} ÷ {posts.length} ={' '}
        <b>{(totalLikes / posts.length).toLocaleString(undefined, { maximumFractionDigits: 1 })}</b>
        {' · '}Avg comments (posts + reels) = {totalComments.toLocaleString()} ÷ {posts.length} ={' '}
        <b>{(totalComments / posts.length).toLocaleString(undefined, { maximumFractionDigits: 1 })}</b>
      </p>
      {bfmt && (bfmt.posts?.count > 0 || bfmt.reels?.count > 0) && (
        <p style={{ fontSize: 12, color: 'var(--paper-dim)', margin: '3px 0 0' }}>
          {bfmt.posts?.count > 0 && (
            <>Posts ({bfmt.posts.count}): {bfmt.posts.total_comments.toLocaleString()} comments, avg{' '}
              <b>{bfmt.posts.avg_comments.toLocaleString(undefined, { maximumFractionDigits: 1 })}</b></>
          )}
          {bfmt.posts?.count > 0 && bfmt.reels?.count > 0 && ' · '}
          {bfmt.reels?.count > 0 && (
            <>Reels ({bfmt.reels.count}): {bfmt.reels.total_comments.toLocaleString()} comments, avg{' '}
              <b>{bfmt.reels.avg_comments.toLocaleString(undefined, { maximumFractionDigits: 1 })}</b></>
          )}
        </p>
      )}
    </div>
  );
}

/* Compact mode: rendered INSIDE the dashboard hero card. Shows only the
   metrics the hero card itself does not already display, so the profile
   appears exactly once on the page. */
function CompactReadout({ profile, metrics }) {
  return (
    <div className="readout-compact">
      <div className="stat-grid">
        <div className="stat">
          <div className="num">{fmt(profile.following)}</div>
          <div className="label">Following</div>
        </div>
        <div className="stat">
          <div className="num">{fmt(Math.round(metrics.avg_likes))}</div>
          <div className="label">Avg. likes / post</div>
        </div>
        {metrics.reels_count > 0 && (
          <div className="stat">
            <div className="num">{metrics.reels_count}</div>
            <div className="label">Reels (sampled)</div>
            {metrics.avg_views > 0 && (
              <div className="label" style={{ fontSize: 11, opacity: 0.7 }}>
                {fmt(Math.round(metrics.avg_views))} avg views
              </div>
            )}
          </div>
        )}
        <div className="stat">
          <div className="num" style={{ textTransform: 'capitalize' }}>{metrics.best_content_type}</div>
          <div className="label">Top format</div>
        </div>
      </div>
      {metrics.top_hashtags?.length > 0 && (
        <div className="hashtag-row">
          {metrics.top_hashtags.map((h) => (
            <span className="hashtag-chip" key={h}>{h}</span>
          ))}
        </div>
      )}
    </div>
  );
}

/* Full mode: standalone card used for rival readouts in competitor research. */
function FullReadout({ profile, metrics }) {
  return (
    <div className="readout">
      <div className="profile-id">
        <p className="handle">
          @{profile.username}
          {profile.is_verified && <span className="badge">VERIFIED</span>}
          {profile.data_age_hours === -1 && (
            <span
              title="No cached real data exists for this handle yet — all numbers below are SIMULATED so the analysis still works. They are NOT real Instagram statistics."
              style={{
                marginLeft: 6,
                fontSize: 10,
                fontWeight: 700,
                color: '#FFFFFF',
                background: '#E5484D',
                borderRadius: 8,
                padding: '2px 7px',
                verticalAlign: 'middle',
                letterSpacing: '0.4px',
              }}
            >
              SIMULATED DATA
            </span>
          )}
          {profile.data_age_hours != null && profile.data_age_hours >= 0 && (
            <span
              title={`Real Instagram data from the local cache, fetched ${profile.data_age_hours}h ago`}
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
          <div className="num">{fmt(profile.following)}</div>
          <div className="label">Following</div>
        </div>
        <div className="stat">
          <div className="num">{fmt(Math.round(metrics.avg_likes))}</div>
          <div className="label">Avg. likes / post</div>
        </div>
        <div className="stat">
          <div className="num">{(metrics.avg_comments || 0).toFixed(1)}</div>
          <div className="label">Avg. comments / post</div>
          {metrics.comments_unresolved_in_sample > 0 && (
            <div className="label" style={{ fontSize: 11, opacity: 0.7 }}>
              count unavailable for {metrics.comments_unresolved_in_sample} of sampled posts
            </div>
          )}
        </div>
        {metrics.reels_count > 0 && (
          <div className="stat">
            <div className="num">{metrics.reels_count}</div>
            <div className="label">Reels (sampled)</div>
            {metrics.avg_views > 0 && (
              <div className="label" style={{ fontSize: 11, opacity: 0.7 }}>
                {fmt(Math.round(metrics.avg_views))} avg views
              </div>
            )}
          </div>
        )}
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

export default function ProfileReadout({ insight, compact = false }) {
  if (!insight) return null;
  const { profile, metrics } = insight;
  return compact
    ? <CompactReadout profile={profile} metrics={metrics} />
    : <FullReadout profile={profile} metrics={metrics} />;
}

/* Standalone per-post table with the avg calculation shown explicitly —
   use where the full per-post transparency is wanted (dashboard hero). */
export function AvgCommentBreakdown({ insight }) {
  if (!insight?.profile) return null;
  return <PostBreakdown profile={insight.profile} metrics={insight.metrics} />;
}
