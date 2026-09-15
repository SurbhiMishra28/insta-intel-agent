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

const SLOT_HOURS = { 0: '00:00–06:00', 6: '06:00–12:00', 12: '12:00–18:00', 18: '18:00–24:00' };

export default function BestTimes({ bestTimes }) {
  if (!bestTimes) return null;
  const slots = bestTimes.slots || [];

  return (
    <div>
      <p className="report-summary">{bestTimes.summary}</p>
      {bestTimes.enough_data && slots.length > 0 && (
        <div style={{ marginTop: 12, display: 'flex', flexDirection: 'column', gap: 12 }}>
          {slots.map((s, i) => (
            <Bar
              key={`${s.hour}-${s.day}`}
              rank={i + 1}
              label={`${SLOT_HOURS[s.hour] || `${String(s.hour).padStart(2, '0')}:00`} UTC`}
              value={s.avg_engagement}
              max={slots[0].avg_engagement}
              sub={`${s.avg_engagement.toLocaleString()} avg · ${s.day} · ${s.samples} post${s.samples === 1 ? '' : 's'}`}
            />
          ))}
        </div>
      )}
    </div>
  );
}
