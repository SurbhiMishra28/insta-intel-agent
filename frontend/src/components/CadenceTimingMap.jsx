// Posting cadence + weekday×hour timing heatmap.
// Plain-language first: a big "Post next" recommendation card, AM/PM times,
// a UTC ↔ local toggle, then the detail grid for users who want to explore.

import { useState } from 'react';

const DAYS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];
const SLOT_HOURS = [0, 3, 6, 9, 12, 15, 18, 21];

const fmtK = (n) => (n >= 1000 ? `${(n / 1000).toFixed(n >= 10000 ? 0 : 1)}k` : `${Math.round(n)}`);

// Local UTC offset in hours (fractional for e.g. India +5:30).
const OFF_H = -new Date().getTimezoneOffset() / 60;
const OFF_FRACTIONAL = Math.abs(OFF_H % 1) > 0.001;

/* Convert one UTC cell (day, hour) to the viewer's local 3-hour bucket. */
function toLocalBucket(day, hour) {
  const di = DAYS.indexOf(day);
  if (di < 0) return { day, hour };
  const total = hour + OFF_H;
  const dayShift = Math.floor(total / 24);
  const localHour = ((total - dayShift * 24) % 24 + 24) % 24;
  const slot = Math.floor(localHour / 3) * 3;
  const nd = ((di + dayShift) % 7 + 7) % 7;
  return { day: DAYS[nd], hour: slot };
}

/* Re-bucket UTC cells into local time, merging collisions with a
   sample-weighted average so numbers stay honest. */
function cellsToLocal(cells) {
  const acc = {};
  for (const c of cells) {
    const { day, hour } = toLocalBucket(c.day, c.hour);
    const k = `${day}-${hour}`;
    const a = acc[k] || { day, hour, weighted: 0, samples: 0 };
    a.weighted += c.engagement * (c.samples || 1);
    a.samples += c.samples || 1;
    acc[k] = a;
  }
  return Object.values(acc).map((a) => ({
    day: a.day,
    hour: a.hour,
    samples: a.samples,
    engagement: a.samples > 0 ? a.weighted / a.samples : 0,
  }));
}

const ampm = (h) => {
  const hh = ((h % 24) + 24) % 24;
  const suffix = hh < 12 ? 'AM' : 'PM';
  const base = hh % 12 === 0 ? 12 : hh % 12;
  return `${base}${suffix}`;
};
const windowLabel = (h) => `${ampm(h)}–${ampm(h + 3)}`;

export default function CadenceTimingMap({ cadenceMap }) {
  const [local, setLocal] = useState(true);

  if (!cadenceMap) return null;

  const rawCells = cadenceMap.heatmap || [];

  if (!cadenceMap.enough_data || rawCells.length === 0) {
    return (
      <div>
        <p className="report-summary">{cadenceMap.summary}</p>
        <p className="timing-explainer">
          This chart appears once a handful of posts with timestamps have been
          analyzed — it shows which day and hour your audience actually responds to.
        </p>
      </div>
    );
  }

  const cells = local ? cellsToLocal(rawCells) : rawCells;
  const maxEng = Math.max(...cells.map((c) => c.engagement), 1);

  // Engagement lookup per (day, slot).
  const grid = {};
  for (const c of cells) grid[`${c.day}-${c.hour}`] = c;

  // Ranked windows (best per day, so the list mixes days like a schedule).
  const slotRanking = Object.values(
    cells.reduce((acc, c) => {
      if (!acc[c.day] || c.engagement > acc[c.day].engagement) acc[c.day] = c;
      return acc;
    }, {})
  ).sort((a, b) => b.engagement - a.engagement);

  const best = cells.reduce((b, c) => (!b || c.engagement > b.engagement ? c : b), null);
  const bestKey = best ? `${best.day}-${best.hour}` : null;

  const zoneLabel = local ? 'your local time' : 'UTC';
  const shift = (h) => String(h).padStart(2, '0');

  return (
    <div className="cadence">
      {/* ---------- Plain-language recommendation ---------- */}
      {best && (
        <div className="timing-verdict">
          <span className="timing-verdict-kicker">Post next</span>
          <span className="timing-verdict-main">
            {best.day} {windowLabel(best.hour)}
          </span>
          <span className="timing-verdict-sub">
            your posts in this window got {best.engagement.toLocaleString()}{' '}
            likes + comments on average — your strongest of the week.
          </span>
          <span className="timing-verdict-note">
            Times shown in {zoneLabel}. Based on this account's own recent posts, not guesswork.
          </span>
        </div>
      )}

      {/* ---------- Toggle ---------- */}
      {OFF_H !== 0 && (
        <div className="cadence-toggle-row">
          <button
            type="button"
            className="cadence-toggle"
            onClick={() => setLocal((v) => !v)}
            title={OFF_FRACTIONAL ? 'Fractional offsets snap to the nearest 3-hour window' : 'Switch between UTC and your local timezone'}
          >
            <span className={local ? '' : 'on'}>UTC</span>
            <span className="cadence-toggle-pill" aria-hidden="true"><span className="cadence-toggle-knob" /></span>
            <span className={local ? 'on' : ''}>My time</span>
          </button>
          {local && OFF_FRACTIONAL && (
            <span className="cadence-footnote">offset {OFF_H > 0 ? '+' : ''}{OFF_H}h — snapped to nearest 3-hour window</span>
          )}
        </div>
      )}

      {/* ---------- Detail grid ---------- */}
      <p className="cadence-grid-title">
        Every window, {zoneLabel} — brighter green = more likes + comments your posts got:
      </p>
      <div className="cadence-grid" role="img" aria-label={`Average engagement by weekday and time window in ${zoneLabel}`}>
        <span className="cadence-corner" />
        {SLOT_HOURS.map((h) => (
          <span key={h} className="cadence-hour">{windowLabel(h)}</span>
        ))}

        {DAYS.map((day) => (
          <div key={day} style={{ display: 'contents' }}>
            <span className="cadence-day">{day}</span>
            {SLOT_HOURS.map((h) => {
              const cell = grid[`${day}-${h}`];
              const eng = cell ? cell.engagement : 0;
              const ratio = cell ? Math.min(1, eng / maxEng) : 0;
              const eased = ratio ** 0.75;
              const isBest = bestKey === `${day}-${h}`;
              return (
                <div
                  key={h}
                  className={'cadence-cell' + (isBest ? ' is-best' : '') + (!cell ? ' is-empty' : '')}
                  title={
                    cell
                      ? `${day} ${windowLabel(h)} (${zoneLabel}) — posts got ${cell.engagement.toLocaleString()} likes+comments on average, from ${cell.samples} post${cell.samples === 1 ? '' : 's'}`
                      : `${day} ${windowLabel(h)} (${zoneLabel}) — no posts in the recent sample`
                  }
                  style={cell ? { background: `rgba(198, 255, 78, ${(0.07 + 0.9 * eased).toFixed(3)})` } : undefined}
                >
                  {cell && <span>{fmtK(eng)}</span>}
                </div>
              );
            })}
          </div>
        ))}
      </div>

      <div className="cadence-legend">
        <span>fewer likes + comments</span>
        <span className="cadence-legend-bar" aria-hidden="true" />
        <span>more likes + comments</span>
        <span className="cadence-legend-best">⭐ best window</span>
      </div>

      {/* ---------- Ranked list ---------- */}
      <div className="cadence-ranking">
        <p className="cadence-ranking-title">
          Your best window for each day ({zoneLabel}):
        </p>
        {slotRanking.slice(0, 4).map((c, i) => {
          const isBest = bestKey === `${c.day}-${c.hour}`;
          return (
            <div key={`${c.day}-${c.hour}`} className="cadence-rank-row">
              <span className="cadence-rank-idx">{String(i + 1).padStart(2, '0')}</span>
              <span className="cadence-rank-window">
                {c.day} {windowLabel(c.hour)}
                {isBest && <span className="cadence-rank-star" title="Best window of the whole week"> ⭐</span>}
              </span>
              <span className="cadence-rank-meta">
                {c.engagement.toLocaleString()} likes + comments · {c.samples} post{c.samples === 1 ? '' : 's'}
              </span>
            </div>
          );
        })}
      </div>
    </div>
  );
}
