import { useEffect, useState } from 'react';

const STAGES = [
  'Fetching the profile with real data…',
  'Discovering related accounts in this niche…',
  'Selecting the most relevant competitors…',
  'Researching all competitors in one batch…',
  'Computing engagement metrics…',
  'Writing the intelligence report…',
];

export default function ResearchProgress({ label }) {
  const [stage, setStage] = useState(0);
  const [elapsed, setElapsed] = useState(0);

  useEffect(() => {
    const stageTimer = setInterval(
      () => setStage((s) => Math.min(s + 1, STAGES.length - 1)),
      6000
    );
    const tick = setInterval(() => setElapsed((e) => e + 1), 1000);
    return () => { clearInterval(stageTimer); clearInterval(tick); };
  }, []);

  return (
    <div className="section">
      <p className="loading-line" style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
        <span className="dot" style={{
          width: 7, height: 7, borderRadius: '50%', background: 'var(--signal)',
          boxShadow: '0 0 0 3px rgba(198,255,78,0.15)', display: 'inline-block', flexShrink: 0,
        }} />
        <span>{label || STAGES[stage]}</span>
        <span style={{ color: 'var(--paper-dim)', fontSize: 12 }}>
          {elapsed}s
        </span>
      </p>
      <div style={{ maxWidth: 420, marginTop: 10 }}>
        <div style={{ height: 3, background: 'var(--hairline)', borderRadius: 2, overflow: 'hidden' }}>
          <div style={{
            height: '100%', width: `${((stage + 1) / STAGES.length) * 100}%`,
            background: 'var(--signal)', transition: 'width 1.2s ease',
          }} />
        </div>
        <p style={{ color: 'var(--paper-dim)', fontSize: 11.5, margin: '6px 0 0' }}>
          Live data takes 30–90s the first time — results are cached afterwards.
        </p>
      </div>
    </div>
  );
}
