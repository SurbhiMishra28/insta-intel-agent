// Posting cadence + weekday×hour timing heatmap.
// Renders the 7×8 UTC grid from the backend's CadenceMap analytics with a
// design-system heat ramp (panel → signal green), always-readable cell
// values, a UTC ↔ local-time toggle, a legend, and a ranked
// "when to post next" list.

import { useState } from 'react';

const DAYS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];
const SLOT_HOURS = [0, 3, 6, 9, 12, 15, 18, 21];

const fmtK = (n) => (n >= 1000 ? `${(n / 1000).toFixed(n >= 10000 ? 0 : 1)}k` : `${Math.round(n)}`);

// Local UTC offset in hours (fractional for e.g. India +5:30).
const OFF_H = -new Date().getTimezoneOffset() / 60;
const OFF_FRACTIONAL = Math.abs(OFF_H % 1) > 0.001;

/* Convert one UTC cell (day, hour) to the viewer's local 3-hour bucket.
   Day shifts when the offset crosses midnight; fractional offsets are
   snapped to the nearest 3-hour slot (flagged in the footnote). */
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

export default function CadenceTimingMap({ cadenceMap }) {
  const [local, setLocal] = useState(false);

  if (!cadenceMap) return null;

  const rawCells = cadenceMap.heatmap || [];

  if (!cadenceMap.enough_data || rawCells.length === 0) {
    return <p className="report-summary">{cadenceMap.summary}</p>;
  }

  const cells = local ? cellsToLocal(rawCells) : rawCells;
  const maxEng = Math.max(...cells.map((c) => c.engagement), 1);

  // Engagement lookup per (day, slot).
  const grid = {};
  for (const c of cells) grid[`${c.day}-${c.hour}`] = c;

  // Best day per slot → "when to post next" ranking.
  const slotRanking = Object.values(
    cells.reduce((acc, c) => {
      if (!acc[c.hour] || c.engagement > acc[c.hour].engagement) acc[c.hour] = c;
      return acc;
    }, {})
  ).sort((a, b) => b.engagement - a.engagement);

  // Best window in the current view (UTC grid ships one; recompute for local).
  const best = cells.reduce((b, c) => (!b || c.engagement > b.engagement ? c : b), null);
  const strongestKey = best ? `${best.day}-${best.hour}` : null;

  const zoneLabel = local ? 'your local time' : 'UTC';
  const shift = (h) => String(h).padStart(2, '0');

  return (
    <div className="cadence">
      <div className="cadence-head">
        <p className="report-summary">{cadenceMap.summary}</p>
        {OFF_H !== 0 && (
          <button
            type="button"
            className="cadence-toggle"
            onClick={() => setLocal((v) => !v)}
            title={OFF_FRACTIONAL ? 'Fractional offsets snap to the nearest 3-hour window' : 'Switch between UTC and your local timezone'}
          >
            <span className={local ? '' : 'on'}>UTC</span>
            <span className="cadence-toggle-pill" aria-hidden="true"><span className="cadence-toggle-knob" /></span>
            <span className={local ? 'on' : ''}>Local</span>
          </button>
        )}
      </div>

      <div className="cadence-grid" role="img" aria-label={`Average engagement by weekday and time window in ${zoneLabel}`}>
        <span className="cadence-corner" />
        {SLOT_HOURS.map((h) => (
          <span key={h} className="cadence-hour">{`${shift(h)}–${shift(h + 3)}h`}</span>
        ))}

        {DAYS.map((day) => (
          <div key={day} style={{ display: 'contents' }}>
            <span className="cadence-day">{day}</span>
            {SLOT_HOURS.map((h) => {
              const cell = grid[`${day}-${h}`];
              const eng = cell ? cell.engagement : 0;
              const ratio = cell ? Math.min(1, eng / maxEng) : 0;
              const eased = ratio ** 0.75; // perceptual ramp
              const isBest = strongestKey === `${day}-${h}`;
              return (
                <div
                  key={h}
                  className={'cadence-cell' + (isBest ? ' is-best' : '') + (!cell ? ' is-empty' : '')}
                  title={
                    cell
                      ? `${day} ${shift(h)}:00–${shift(h + 3)}:00 ${zoneLabel} · ${cell.engagement.toLocaleString()} avg engagement · ${cell.samples} post${cell.samples === 1 ? '' : 's'}`
                      : `${day} ${shift(h)}:00–${shift(h + 3)}:00 ${zoneLabel} · no posts in sample`
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
        <span>weaker</span>
        <span className="cadence-legend-bar" aria-hidden="true" />
        <span>stronger</span>
        <span className="cadence-legend-best">⭐ best window</span>
      </div>

      {local && OFF_FRACTIONAL && (
        <p className="cadence-footnote">Your timezone is offset by {OFF_H > 0 ? '+' : ''}{OFF_H}h — windows are snapped to the nearest 3-hour bucket.</p>
      )}

      <div className="cadence-ranking">
        <p className="cadence-ranking-title">When to post next — your windows, ranked ({zoneLabel}):</p>
        {slotRanking.slice(0, 4).map((c, i) => {
          const isBest = strongestKey === `${c.day}-${c.hour}`;
          return (
            <div key={`${c.day}-${c.hour}`} className="cadence-rank-row">
              <span className="cadence-rank-idx">{String(i + 1).padStart(2, '0')}</span>
              <span className="cadence-rank-window">
                {c.day} {shift(c.hour)}:00–{shift(c.hour + 3)}:00
                {isBest && <span className="cadence-rank-star" title="Best window in the sample"> ⭐</span>}
              </span>
              <span className="cadence-rank-meta">
                {c.engagement.toLocaleString()} avg · {c.samples} post{c.samples === 1 ? '' : 's'}
              </span>
            </div>
          );
        })}
      </div>
    </div>
  );
}
