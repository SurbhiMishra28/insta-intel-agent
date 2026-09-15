function Bar({ label, value, max, sub, rank }) {
  const pct = max > 0 ? Math.round((value / max) * 100) : 0;
  return (
    <div className="bt-row">
      <div className="bt-row-head">
        <span className="bt-row-label">
          <span className="bt-rank-idx">{String(rank).padStart(2, '0')}</span>
          {label}
        </span>
        <span className="bt-row-sub">{sub}</span>
      </div>
      <div className="bt-track">
        <div className="bt-fill" style={{ width: `${Math.max(pct, 2)}%` }} />
      </div>
    </div>
  );
}

const ampm = (h) => {
  const hh = ((h % 24) + 24) % 24;
  const suffix = hh < 12 ? 'AM' : 'PM';
  const base = hh % 12 === 0 ? 12 : hh % 12;
  return `${base}${suffix}`;
};
const rangeLabel = (h) => `${ampm(h)}–${ampm(h + 6)}`;

export default function BestTimes({ bestTimes }) {
  if (!bestTimes) return null;
  const slots = bestTimes.slots || [];

  return (
    <div>
      <p className="report-summary">{bestTimes.summary}</p>
      {bestTimes.enough_data && slots.length > 0 && (
        <>
          <p className="timing-hint">
            Wider bar = your posts got more likes + comments in that 6-hour window.
            #1 is your strongest.
          </p>
          <div style={{ marginTop: 10, display: 'flex', flexDirection: 'column', gap: 12 }}>
            {slots.map((s, i) => (
              <Bar
                key={`${s.hour}-${s.day}`}
                rank={i + 1}
                label={rangeLabel(s.hour)}
                value={s.avg_engagement}
                max={slots[0].avg_engagement}
                sub={`${s.avg_engagement.toLocaleString()} avg · ${s.day} · ${s.samples} post${s.samples === 1 ? '' : 's'}`}
              />
            ))}
          </div>
        </>
      )}
    </div>
  );
}
