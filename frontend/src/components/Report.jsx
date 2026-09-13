// AI intelligence report: summary, strengths (always first), weaknesses,
// then numbered recommendations.

export default function Report({ insight }) {
  const strengths = insight.strengths?.length
    ? insight.strengths
    : ['No distinct strengths detected in this sample yet — rescan after a few more posts.'];
  const weaknesses = insight.weaknesses?.length
    ? insight.weaknesses
    : ['No major weaknesses detected in the sampled data.'];

  return (
    <div>
      <p className="report-summary">{insight.ai_summary}</p>

      <div className="finding-cols">
        <div className="finding-col strengths">
          <h3>Strengths</h3>
          <ul className="finding-list">
            {strengths.map((s, i) => <li key={i}>{s}</li>)}
          </ul>
        </div>
        <div className="finding-col weaknesses">
          <h3>Weaknesses</h3>
          <ul className="finding-list">
            {weaknesses.map((s, i) => <li key={i}>{s}</li>)}
          </ul>
        </div>
      </div>

      <ul className="rec-list">
        {insight.recommendations.map((r, i) => (
          <li key={i}>
            <span className="idx">{String(i + 1).padStart(2, '0')}</span>
            <span>{r}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}
