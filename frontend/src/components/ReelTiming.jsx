function SlotRow({ slot, rank }) {
  const heat = {
    low: { c: 'var(--acc-green)', label: 'low competition' },
    medium: { c: 'var(--acc-violet)', label: 'medium competition' },
    high: { c: 'var(--acc-rose)', label: 'high competition' },
  }[slot.competitor_activity] || { c: 'var(--paper-dim)', label: 'competition' };

  return (
    <div className="rt-row">
      <span className="rt-rank">{String(rank).padStart(2, '0')}</span>
      <div className="rt-main">
        <div className="rt-head">
          <span className="rt-window">
            {slot.day} {String(slot.hour).padStart(2, '0')}:00–{String(slot.hour + 3).padStart(2, '0')}:00
          </span>
          <span className="rt-badge" style={{ color: heat.c }}>
            <span className="rt-pip" style={{ background: heat.c }} />
            {heat.label}
          </span>
        </div>
        <p className="rt-why">{slot.rationale}</p>
      </div>
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
        <p className="rt-cadence">Current reel cadence: {reelTiming.current_reel_cadence}</p>
      )}
      {!reelTiming.enough_data && slots.length === 0 && (
        <p className="rt-empty">Start posting reels to unlock whitespace analysis.</p>
      )}
      {slots.length > 0 && (
        <div className="rt-list">
          {slots.map((s, i) => (
            <SlotRow key={`${s.day}-${s.hour}`} slot={s} rank={i + 1} />
          ))}
        </div>
      )}
    </div>
  );
}
