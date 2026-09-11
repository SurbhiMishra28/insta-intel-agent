function Bar({ label, value, max, sub }) {
  const pct = max > 0 ? Math.round((value / max) * 100) : 0;
  return (
    <div style={{ marginBottom: 8 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 12.5, marginBottom: 3 }}>
        <span>{label}</span>
        <span style={{ color: 'var(--paper-dim)' }}>{sub}</span>
      </div>
      <div style={{ height: 6, background: 'var(--hairline)', borderRadius: 3 }}>
        <div style={{
          height: '100%', width: `${pct}%`,
          background: 'var(--signal)', borderRadius: 3, opacity: 0.85,
        }} />
      </div>
    </div>
  );
}

const SLOT_HOURS = { 0: '00–06', 6: '06–12', 12: '12–18', 18: '18–24' };

export default function BestTimes({ bestTimes }) {
  if (!bestTimes) return null;
  const slots = bestTimes.slots || [];

  return (
    <div>
      <p className="report-summary">{bestTimes.summary}</p>
      {bestTimes.enough_data && slots.length > 0 && (
        <div style={{ marginTop: 10 }}>
          {slots.map((s) => (
            <Bar
              key={`${s.hour}-${s.day}`}
              label={`${SLOT_HOURS[s.hour] || s.hour}:00 UTC`}
              value={s.avg_engagement}
              max={slots[0].avg_engagement}
              sub={`${s.avg_engagement.toLocaleString()} avg · ${s.day} · ${s.samples} posts`}
            />
          ))}
        </div>
      )}
    </div>
  );
}
