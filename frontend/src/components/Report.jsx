export default function Report({ insight }) {
  return (
    <div>
      <p className="report-summary">{insight.ai_summary}</p>

      <div className="finding-cols">
        <div className="finding-col strengths">
          <h3>Strengths</h3>
          <ul className="finding-list">
            {insight.strengths.map((s, i) => <li key={i}>{s}</li>)}
          </ul>
        </div>
        <div className="finding-col weaknesses">
          <h3>Weaknesses</h3>
          <ul className="finding-list">
            {insight.weaknesses.map((s, i) => <li key={i}>{s}</li>)}
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
