import { useEffect, useState } from 'react';

/* Data store browser: every Instagram handle the agent has ever fetched,
   with the exact profile + post data stored for each. */

function fmt(n) {
  if (n == null) return '—';
  if (n >= 1_000_000) return (n / 1_000_000).toFixed(1) + 'M';
  if (n >= 1_000) return (n / 1_000).toFixed(1) + 'K';
  return String(n);
}

const timeAgo = (iso) => {
  const h = (Date.now() - new Date(iso).getTime()) / 3.6e6;
  if (h < 1) return 'just now';
  if (h < 48) return `${Math.round(h)}h ago`;
  return `${Math.round(h / 24)}d ago`;
};

function download(filename, content, type) {
  const blob = new Blob([content], { type });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

const csvCell = (v) => `"${String(v ?? '').replace(/"/g, '""')}"`;

function exportJson(p) {
  download(
    `instaiq-${p.username}-data.json`,
    JSON.stringify(p, null, 2),
    'application/json'
  );
}

function exportCsv(p) {
  const header = ['shortcode', 'media_type', 'posted_at', 'posted_days_ago', 'likes', 'comments', 'views', 'caption', 'hashtags'];
  const rows = p.posts.map((post) => [
    post.id, post.media_type, post.posted_at || '', post.posted_days_ago ?? '',
    post.likes, post.comments, post.views, post.caption, (post.hashtags || []).join(' '),
  ].map(csvCell).join(','));
  download(
    `instaiq-${p.username}-posts.csv`,
    '\ufeff' + [header.join(','), ...rows].join('\r\n'),
    'text/csv;charset=utf-8'
  );
}

function HandleDetail({ api, username, onBack }) {
  const [data, setData] = useState(null);
  const [err, setErr] = useState('');

  useEffect(() => {
    let alive = true;
    setErr('');
    setData(null);
    fetch(`${api}/api/data-store?username=${encodeURIComponent(username)}`)
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error('Not stored yet'))))
      .then((d) => alive && setData(d))
      .catch((e) => alive && setErr(e.message));
    return () => { alive = false; };
  }, [api, username]);

  if (err) return <p className="muted">{err}</p>;
  if (!data) return <p className="muted">Loading stored data…</p>;

  const p = data.profile;
  return (
    <div className="ds-detail">
      <button className="ds-back" onClick={onBack}>← All handles</button>

      <div className="ds-detail-head">
        <strong className="ds-handle">@{p.username}</strong>
        {p.is_verified && <span className="badge">VERIFIED</span>}
        <span className="ds-meta">
          fetched {timeAgo(p.fetched_at)} · {p.fetch_count} fetch(es) · {p.posts_stored} posts stored
        </span>
        <div className="ds-export">
          <button className="ds-export-btn" onClick={() => exportJson(p)} title="Download the full stored record as JSON">
            ⬇ JSON
          </button>
          <button className="ds-export-btn" onClick={() => exportCsv(p)} title="Download all stored posts as a spreadsheet">
            ⬇ CSV
          </button>
        </div>
      </div>
      {p.bio && <p className="muted ds-bio">{p.bio}</p>}

      <div className="ds-stats">
        <div className="stat"><div className="num">{fmt(p.followers)}</div><div className="label">Followers</div></div>
        <div className="stat"><div className="num">{fmt(p.following)}</div><div className="label">Following</div></div>
        <div className="stat"><div className="num">{fmt(p.posts_count)}</div><div className="label">Posts (account)</div></div>
        <div className="stat highlight"><div className="num">{p.posts_stored}</div><div className="label">Posts stored here</div></div>
      </div>

      <p className="ds-section-title">Stored posts (real data the analysis used):</p>
      <div className="ds-posts">
        {p.posts.length === 0 && <p className="muted">No posts stored for this handle.</p>}
        {p.posts.map((post) => (
          <div className="ds-post" key={post.id}>
            <div className="ds-post-head">
              <span className={`ds-format ds-format-${post.media_type}`}>{post.media_type}</span>
              <span className="ds-post-age">
                {post.posted_at ? new Date(post.posted_at).toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' })
                  : post.posted_days_ago ? `${post.posted_days_ago}d ago` : 'date unknown'}
              </span>
            </div>
            <div className="ds-post-metrics">
              <span>❤ {post.likes.toLocaleString()}</span>
              <span>💬 {post.comments.toLocaleString()}</span>
              {post.views > 0 && <span>▶ {post.views.toLocaleString()}</span>}
            </div>
            {post.caption && <p className="ds-post-caption">{post.caption}</p>}
            {post.hashtags?.length > 0 && (
              <div className="ds-post-tags">
                {post.hashtags.slice(0, 6).map((h) => <span key={h} className="hashtag-chip">{h}</span>)}
              </div>
            )}
          </div>
        ))}
      </div>

      {data.fetch_log?.length > 0 && (
        <>
          <p className="ds-section-title">Fetch log:</p>
          <div className="ds-fetchlog">
            {data.fetch_log.slice(0, 8).map((f, i) => (
              <span key={i} className="ds-fetchrow">
                {new Date(f.fetched_at).toLocaleString(undefined, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })}
                {' · '}{fmt(f.followers)} followers · {f.posts_stored} posts
              </span>
            ))}
          </div>
        </>
      )}
    </div>
  );
}

export default function DataStore({ api }) {
  const [data, setData] = useState(null);
  const [selected, setSelected] = useState(null);
  const [query, setQuery] = useState('');

  const load = () => {
    fetch(`${api}/api/data-store`)
      .then((r) => (r.ok ? r.json() : null))
      .then((d) => setData(d))
      .catch(() => {});
  };

  useEffect(load, [api]);

  if (selected) return <HandleDetail api={api} username={selected} onBack={() => { setSelected(null); load(); }} />;

  const handles = (data?.handles || []).filter(
    (h) => !query || h.username.includes(query.toLowerCase()) || (h.full_name || '').toLowerCase().includes(query.toLowerCase())
  );

  return (
    <div className="ds-root">
      <p className="report-summary">
        Every Instagram handle this agent has searched is stored here with its full
        real data — the exact numbers behind every metric and AI recommendation.
      </p>

      {data && (
        <div className="ds-statsheader">
          <span><b>{data.stats.handles}</b> handles stored</span>
          <span><b>{data.stats.posts}</b> posts stored</span>
          <span><b>{data.stats.fetches}</b> total fetches</span>
        </div>
      )}

      {handles.length > 0 && (
        <input
          className="ds-search"
          placeholder="Search stored handles…"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
        />
      )}

      <div className="ds-grid">
        {handles.map((h) => (
          <button className="ds-card" key={h.username} onClick={() => setSelected(h.username)}>
            <div className="ds-card-head">
              <span className="ds-handle">@{h.username}</span>
              {h.is_verified && <span className="badge">✓</span>}
            </div>
            <span className="ds-card-name">{h.full_name || h.category || '—'}</span>
            <span className="ds-card-meta">
              {fmt(h.followers)} followers · {h.posts_stored} posts stored · {timeAgo(h.fetched_at)}
            </span>
          </button>
        ))}
      </div>

      {data && handles.length === 0 && (
        <p className="muted">
          {query ? 'No stored handle matches that search.' : 'No handles stored yet — run an analysis and the fetched data will appear here.'}
        </p>
      )}

      {data?.recent_fetches?.length > 0 && !query && (
        <>
          <p className="ds-section-title">Recent fetches:</p>
          <div className="ds-fetchlog">
            {data.recent_fetches.slice(0, 10).map((f, i) => (
              <span key={i} className="ds-fetchrow">
                @{f.username} · {new Date(f.fetched_at).toLocaleString(undefined, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })} · {f.source}
              </span>
            ))}
          </div>
        </>
      )}
    </div>
  );
}
