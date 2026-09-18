import { useCallback, useEffect, useState } from 'react';
import { fmtCompact as fmt } from '../format.js';

const API_URL = import.meta.env.VITE_API_URL || 'http://localhost:8000';

function timeAgo(iso) {
  try {
    const s = (Date.now() - new Date(iso).getTime()) / 1000;
    if (s < 90) return 'just now';
    if (s < 3600) return `${Math.round(s / 60)} min ago`;
    if (s < 86400) return `${Math.round(s / 3600)} h ago`;
    if (s < 86400 * 30) return `${Math.round(s / 86400)} d ago`;
    return new Date(iso).toLocaleDateString();
  } catch {
    return '';
  }
}

/**
 * History tab: every account previously searched through the agent (real
 * recorded searches only — nothing invented). Restore re-opens the account's
 * stored real snapshot; PDF downloads its full stored Instagram data
 * (profile, metrics, every stored post, scan history) as a report.
 */
export default function HistoryPanel({
  activeHandle,
  onRestore,
  onPdf,
  onCut,
  pdfBusyHandle = '',
  refreshKey = 0,
}) {
  const [searches, setSearches] = useState(null); // null = loading
  const [error, setError] = useState('');
  const [confirmingCut, setConfirmingCut] = useState(null); // username pending cut

  const load = useCallback(async () => {
    try {
      const res = await fetch(`${API_URL}/api/recent-searches?limit=50`);
      if (!res.ok) throw new Error(`Failed to load history (${res.status})`);
      const data = await res.json();
      setSearches(data.searches || []);
      setError('');
    } catch (err) {
      setError(err.message || 'Could not load history.');
      setSearches([]);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load, refreshKey]);

  if (searches === null) {
    return <p className="muted">Loading history…</p>;
  }

  if (!searches.length) {
    return (
      <div className="hist-panel">
        {error && <p className="error-line">{error}</p>}
        <p className="muted">
          No accounts stored yet. Every handle you analyze through the agent is
          recorded here automatically — with its full real data available to
          restore or download as a PDF.
        </p>
      </div>
    );
  }

  return (
    <div className="hist-panel">
      <div className="hist-head">
        <span className="muted">
          {searches.length} account{searches.length === 1 ? '' : 's'} searched ·
          real recorded data only
        </span>
      </div>
      {error && <p className="error-line">{error}</p>}
      <div className="hist-table">
        <div className="hist-tr hist-th">
          <span>Account</span>
          <span>Followers</span>
          <span>ER</span>
          <span>Last searched</span>
          <span className="hist-actions-h">Actions</span>
        </div>
        {searches.map((s) => (
          <div
            key={s.username}
            className={`hist-tr${activeHandle && activeHandle.toLowerCase() === s.username ? ' active' : ''}`}
          >
            <span className="hist-handle">@{s.username}</span>
            <span className="hist-num">{fmt(s.followers)}</span>
            <span className="hist-num">{s.engagement_rate ?? '—'}%</span>
            <span className="hist-when">
              {timeAgo(s.last_scanned_at)}
              {s.scan_count > 1 ? ` · ${s.scan_count} scans` : ''}
            </span>
            <span className="hist-actions">
              <button
                type="button"
                className="hist-btn"
                onClick={() => onRestore && onRestore(s.username)}
                title={`Restore @${s.username}'s stored real data`}
              >
                Restore
              </button>
              <button
                type="button"
                className="hist-btn pdf"
                disabled={!!pdfBusyHandle}
                onClick={() => onPdf && onPdf(s.username)}
                title={`Download @${s.username}'s full stored data as a PDF`}
              >
                {pdfBusyHandle === s.username ? 'Building…' : '⬇ PDF'}
              </button>
              {confirmingCut === s.username ? (
                <span className="hist-confirm">
                  <button
                    type="button"
                    className="hist-btn cut yes"
                    onClick={() => {
                      setConfirmingCut(null);
                      onCut && onCut(s.username);
                    }}
                    title="Remove this account's stored data"
                  >
                    cut it
                  </button>
                  <button
                    type="button"
                    className="hist-btn"
                    onClick={() => setConfirmingCut(null)}
                  >
                    keep
                  </button>
                </span>
              ) : (
                <button
                  type="button"
                  className="hist-btn cut"
                  onClick={() => setConfirmingCut(s.username)}
                  title={`Cut @${s.username} — remove its stored data from the agent`}
                >
                  ✂ Cut
                </button>
              )}
            </span>
          </div>
        ))}
      </div>
    </div>
  );
}
