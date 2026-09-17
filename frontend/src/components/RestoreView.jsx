import ProfileReadout from './ProfileReadout.jsx';

const fmt = (n) => (typeof n === 'number' ? n.toLocaleString('en-US') : '—');

function timeAgo(iso) {
  try {
    const s = (Date.now() - new Date(iso).getTime()) / 1000;
    if (s < 90) return 'just now';
    if (s < 3600) return `${Math.round(s / 60)} min ago`;
    if (s < 86400) return `${Math.round(s / 3600)} h ago`;
    return `${Math.round(s / 86400)} d ago`;
  } catch {
    return '';
  }
}

/**
 * Restored account view: the stored REAL snapshot of a previously searched
 * account (plus metrics recomputed from its saved posts), enriched with a
 * keyless Instagram GraphQL refresh when available. Read-only — run a fresh
 * analysis from the search box for the full AI dashboard.
 */
export default function RestoreView({ data }) {
  if (!data) return null;
  const p = data.profile;
  const m = data.metrics || {};
  if (!p) return null;

  return (
    <div className="restore-root">
      <div className="restore-banner">
        <span className="restore-title">
          Restored @{p.username} from search history
        </span>
        <span className="restore-meta">
          {data.scan_count} stored scan{data.scan_count === 1 ? '' : 's'} ·
          {' '}{data.data_age_hours != null && data.data_age_hours >= 0
            ? `data ${timeAgo(new Date(Date.now() - data.data_age_hours * 3.6e6).toISOString())}`
            : 'real stored data'} ·
          {' '}via {data.refreshed_via}
        </span>
      </div>

      <div className="dash-hero" style={{ marginTop: 14 }}>
        <div className="dash-hero-main">
          <div className="dash-hero-top">
            <h2>@{p.username}</h2>
            {p.is_verified && <span className="badge">VERIFIED</span>}
            {p.data_age_hours === -1 && <span className="pill bad">SIMULATED</span>}
          </div>
          <p className="dash-hero-bio">{p.bio || p.full_name || ''}</p>
          <div className="dash-stats">
            <div className="stat">
              <div className="num">{fmt(p.followers)}</div>
              <div className="label">Followers</div>
            </div>
            <div className="stat">
              <div className="num">{fmt(p.posts_count)}</div>
              <div className="label">Posts</div>
            </div>
            <div className="stat highlight">
              <div className="num">{m.engagement_rate != null ? `${m.engagement_rate}%` : '—'}</div>
              <div className="label">Engagement rate</div>
            </div>
            <div className="stat">
              <div className="num">{fmt(m.avg_comments != null ? Math.round(m.avg_comments) : null)}</div>
              <div className="label">Avg comments</div>
            </div>
            <div className="stat">
              <div className="num">{m.posting_frequency_per_week ?? '—'}</div>
              <div className="label">Posts / week</div>
            </div>
          </div>
          <ProfileReadout insight={{ profile: p, metrics: m }} compact />
        </div>
      </div>
    </div>
  );
}
