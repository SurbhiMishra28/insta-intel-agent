function SlotCard({ slot, rank }) {
  const activityColor = {
    low: '#3ddc97',
    medium: '#7c5cff',
    high: '#ff2e63',
  }[slot.competitor_activity] || '#9CA1A6';

  return (
    <div style={{
      border: '1px solid var(--hairline)', borderRadius: 8, padding: '12px 14px',
      background: '#141824', marginBottom: 10,
    }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 6 }}>
        <strong style={{ fontSize: 13.5, fontFamily: 'var(--font-display)' }}>
          #{rank} · {slot.day} {String(slot.hour).padStart(2, '0')}:00–{String(slot.hour + 3).padStart(2, '0')}:00 UTC
        </strong>
        <span style={{
          fontSize: 10.5, fontWeight: 700, padding: '2px 8px', borderRadius: 10,
          background: activityColor + '22', color: activityColor, textTransform: 'uppercase',
        }}>
          {slot.competitor_activity} competition
        </span>
      </div>
      <p style={{ fontSize: 12.5, color: '#C7CCD6', margin: 0, lineHeight: 1.5 }}>{slot.rationale}</p>
    </div>
  );
}

export default function ReelTimingView({ reelTiming }) {
  if (!reelTiming) return null;
  const slots = reelTiming.slots || [];

  return (
    <div>
      <p className="report-summary">{reelTiming.summary}</p>
      {reelTiming.current_reel_cadence && (
        <p style={{ fontSize: 12.5, color: 'var(--paper-dim)', margin: '4px 0 10px' }}>
          Current reel cadence: {reelTiming.current_reel_cadence}
        </p>
      )}
      {!reelTiming.enough_data && slots.length === 0 && (
        <p style={{ fontSize: 12.5, color: 'var(--paper-dim)', fontStyle: 'italic' }}>
          Start posting reels to unlock whitespace analysis.
        </p>
      )}
      {slots.length > 0 && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
          {slots.map((s, i) => (
            <SlotCard key={`${s.day}-${s.hour}`} slot={s} rank={i + 1} />
          ))}
        </div>
      )}
    </div>
  );
}
