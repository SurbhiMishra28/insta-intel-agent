// Posting cadence + weekday×hour timing heatmap.
// Renders the 7×8 UTC grid from the backend's CadenceMap analytics, with a
// "when to post next" ranking of the windows the account actually used.

const DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
const SLOT_HOURS = [0, 3, 6, 9, 12, 15, 18, 21];

function heatColor(ratio) {
  // 0 = cold slate, 1 = hot signal purple.
  const r = Math.round(60 + (160 - 60) * ratio);
  const g = Math.round(60 + (92 - 60) * ratio);
  const b = Math.round(70 + (255 - 70) * ratio);
  return `rgb(${r}, ${g}, ${b})`;
}

export default function CadenceTimingMap({ cadenceMap }) {
  if (!cadenceMap) return null;

  const cells = cadenceMap.heatmap || [];

  if (!cadenceMap.enough_data || cells.length === 0) {
    return <p className="report-summary">{cadenceMap.summary}</p>;
  }

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

  const strongest = cadenceMap.strongest_cell;
  const strongestKey = strongest ? `${strongest.day}-${strongest.hour}` : null;

  return (
    <div>
      <p className="report-summary">{cadenceMap.summary}</p>

      <div
        style={{
          display: 'grid',
          gridTemplateColumns: '34px repeat(8, 1fr)',
          gap: 3,
          margin: '12px 0 6px',
          fontSize: 10.5,
          color: 'var(--paper-dim)',
        }}
      >
        <span />
        {SLOT_HOURS.map((h) => (
          <span key={h} style={{ textAlign: 'center' }}>{`${h}–${h + 3}h`}</span>
        ))}

        {DAYS.map((day) => (
          <div key={day} style={{ display: 'contents' }}>
            <span style={{ alignSelf: 'center' }}>{day}</span>
            {SLOT_HOURS.map((h) => {
              const cell = grid[`${day}-${h}`];
              const eng = cell ? cell.engagement : 0;
              const hot = eng / maxEng > 0.55;
              const isBest = strongestKey === `${day}-${h}`;
              return (
                <div
                  key={h}
                  title={cell
                    ? `${day} ${h}:00–${h + 3}:00 UTC · ${cell.engagement.toLocaleString()} avg engagement · ${cell.samples} post(s)`
                    : `${day} ${h}:00–${h + 3}:00 UTC · no posts in sample`}
                  style={{
                    aspectRatio: '1.4',
                    borderRadius: 4,
                    background: cell ? heatColor(eng / maxEng) : 'var(--hairline)',
                    opacity: cell ? 0.92 : 0.5,
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'center',
                    fontSize: 9.5,
                    color: hot ? '#0F1115' : 'transparent',
                    border: isBest ? '1.5px solid #C6FF4D' : '1px solid transparent',
                    cursor: 'default',
                  }}
                >
                  {cell && hot ? (eng >= 1000 ? `${(eng / 1000).toFixed(1)}k` : Math.round(eng)) : ''}
                </div>
              );
            })}
          </div>
        ))}
      </div>

      <div style={{ fontSize: 11, color: 'var(--paper-dim)', marginBottom: 10 }}>
        Avg engagement (likes+comments) per weekday × 3-hour UTC window. Brighter
        = stronger. Outlined cell = best window in the sample.
      </div>

      <div style={{ marginTop: 8 }}>
        <p style={{ fontSize: 12.5, color: 'var(--paper-dim)', marginBottom: 6 }}>
          When to post next — windows ranked by how this account's posts performed:
        </p>
        {slotRanking.slice(0, 4).map((c) => (
          <div
            key={`${c.day}-${c.hour}`}
            style={{
              display: 'flex',
              justifyContent: 'space-between',
              fontSize: 12.5,
              fontWeight: 500,
              padding: '4px 0',
              borderBottom: '1px solid var(--hairline)',
            }}
          >
            <span>
              {c.day} {String(c.hour).padStart(2, '0')}:00–{String(c.hour + 3).padStart(2, '0')}:00 UTC
              {strongestKey === `${c.day}-${c.hour}` ? ' ⭐' : ''}
            </span>
            <span style={{ color: 'var(--paper-dim)' }}>
              {c.engagement.toLocaleString()} avg · {c.samples} post(s)
            </span>
          </div>
        ))}
      </div>
    </div>
  );
}
