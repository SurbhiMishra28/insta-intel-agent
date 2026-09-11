import { useState } from 'react';

function TrendArrow({ trend }) {
  if (trend === 'up') return <span style={{ color: '#C6FF4E', fontSize: 12 }}>&#9650;</span>;
  if (trend === 'down') return <span style={{ color: '#FF5C72', fontSize: 12 }}>&#9660;</span>;
  return <span style={{ color: '#9CA1A6', fontSize: 12 }}>&#9644;</span>;
}

function MetricCard({ metric, index }) {
  const isUp = metric.trend === 'up';
  const isDown = metric.trend === 'down';
  const borderColor = isUp ? '#C6FF4E' : isDown ? '#FF5C72' : '#3A3F45';
  const barWidth = Math.min(100, Math.abs(metric.change_pct) * 3);

  return (
    <div
      style={{
        borderLeft: `3px solid ${borderColor}`,
        padding: '14px 16px',
        background: '#141824',
        borderRadius: 6,
        marginBottom: 12,
      }}
    >
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 8 }}>
        <strong style={{ fontSize: 14, fontFamily: 'var(--font-display)', color: '#E8EAED' }}>
          {metric.label}
        </strong>
        <TrendArrow trend={metric.trend} />
      </div>

      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', marginBottom: 4 }}>
        <div>
          <span style={{ fontSize: 24, fontWeight: 700, fontFamily: 'var(--font-display)', color: '#fff' }}>
            {metric.last_value.toLocaleString()}
          </span>
          <span style={{ fontSize: 13, color: '#9CA1A6', marginLeft: 4 }}>
            {metric.unit}
          </span>
        </div>
        <div style={{ textAlign: 'right' }}>
          <span style={{
            fontSize: 14, fontWeight: 600,
            color: isUp ? '#C6FF4E' : isDown ? '#FF5C72' : '#9CA1A6',
          }}>
            {metric.change > 0 ? '+' : ''}{metric.change} {metric.unit}
          </span>
          <span style={{ fontSize: 12, color: '#9CA1A6', marginLeft: 4 }}>
            ({metric.change_pct > 0 ? '+' : ''}{metric.change_pct}%)
          </span>
        </div>
      </div>

      <div style={{ marginTop: 8 }}>
        <div style={{
          height: 4, background: '#23272B', borderRadius: 2, overflow: 'hidden',
        }}>
          <div style={{
            height: '100%',
            width: isUp || isDown ? `${barWidth}%` : '2px',
            background: isUp ? '#C6FF4E' : isDown ? '#FF5C72' : '#3A3F45',
            marginLeft: metric.change < 0 ? 'auto' : '0',
            marginRight: metric.change < 0 ? '0' : 'auto',
            transition: 'width 0.6s ease',
          }} />
        </div>
        <div style={{ display: 'flex', justifyContent: 'space-between', marginTop: 4 }}>
          <span style={{ fontSize: 11, color: '#9CA1A6' }}>
            {metric.first_value.toLocaleString()} {metric.unit} (start)
          </span>
          <span style={{ fontSize: 11, color: '#9CA1A6' }}>
            {metric.samples} scans
          </span>
        </div>
      </div>
    </div>
  );
}

export default function MonthlyReviewer({ review }) {
  const [activeTab, setActiveTab] = useState('overview'); // 'overview' | 'history'

  if (!review) return null;

  const isGrowing = review.metrics.filter(m => m.trend === 'up').length;
  const isDeclining = review.metrics.filter(m => m.trend === 'down').length;

  return (
    <div>
      {/* Header */}
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 20 }}>
        <div>
          <p className="report-summary">
            Profile review for <strong>@{review.username}</strong> covering {review.scan_count} scans over {review.span_days} day{review.span_days !== 1 ? 's' : ''}.
          </p>
        </div>
      </div>

      {/* Quick stats row */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(160px, 1fr))', gap: 12, marginBottom: 24 }}>
        <div className="stat" style={{ paddingLeft: 14 }}>
          <div className="num" style={{ fontSize: 28 }}>{review.scan_count}</div>
          <div className="label">Total scans</div>
        </div>
        <div className="stat" style={{ paddingLeft: 14 }}>
          <div className="num" style={{ fontSize: 28 }}>{review.span_days}</div>
          <div className="label">Days tracked</div>
        </div>
        <div className="stat" style={{ paddingLeft: 14, borderLeftColor: isGrowing > isDeclining ? 'var(--signal)' : isDeclining > isGrowing ? 'var(--alert)' : 'var(--hairline)' }}>
          <div className="num" style={{ fontSize: 28, color: isGrowing > isDeclining ? 'var(--signal)' : isDeclining > isGrowing ? 'var(--alert)' : 'var(--paper)' }}>
            {isGrowing > isDeclining ? '+' : isDeclining > isGrowing ? '' : '0'}
            {isGrowing - isDeclining}
          </div>
          <div className="label">
            {isGrowing > isDeclining ? 'Metrics trending up' : isDeclining > isGrowing ? 'Metrics declining' : 'Mixed metrics'}
          </div>
        </div>
      </div>

      {/* Metrics grid */}
      <p className="section-label" style={{ marginBottom: 12 }}>Engagement trajectory</p>
      <div style={{ maxWidth: 640 }}>
        {review.metrics.map((m, i) => (
          <MetricCard key={i} metric={m} />
        ))}
      </div>

      {/* Summary */}
      <div style={{
        marginTop: 24, padding: '16px 20px',
        background: '#1D2023', border: '1px solid var(--hairline)',
        borderRadius: 6,
      }}>
        <p style={{ fontSize: 13, fontWeight: 600, color: '#C6FF4E', margin: '0 0 8px' }}>ANALYST SUMMARY</p>
        <p style={{ fontSize: 14, color: '#C7CCD6', lineHeight: 1.7, margin: 0 }}>{review.summary}</p>
      </div>

      {/* Recommendations */}
      {review.recommendations.length > 0 && (
        <div style={{ marginTop: 24 }}>
          <p className="section-label" style={{ marginBottom: 12 }}>Recommendations</p>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
            {review.recommendations.map((r, i) => (
              <div
                key={i}
                style={{
                  display: 'flex', gap: 10, alignItems: 'flex-start',
                  padding: '10px 14px',
                  background: 'rgba(198,255,78,0.04)',
                  border: '1px solid var(--signal-dim)',
                  borderRadius: 4,
                }}
              >
                <span style={{ color: 'var(--signal)', fontWeight: 700, fontSize: 14, marginTop: 2 }}>&#9654;</span>
                <span style={{ fontSize: 13.5, color: '#C7CCD6', lineHeight: 1.55 }}>{r}</span>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Warnings */}
      {review.warnings.length > 0 && (
        <div style={{ marginTop: 20 }}>
          <p className="section-label" style={{ marginBottom: 12 }}>Data notes</p>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
            {review.warnings.map((w, i) => (
              <div
                key={i}
                style={{
                  display: 'flex', gap: 8, alignItems: 'flex-start',
                  padding: '8px 12px',
                  background: 'rgba(255,92,114,0.04)',
                  border: '1px solid var(--alert-dim)',
                  borderRadius: 4,
                }}
              >
                <span style={{ color: 'var(--alert)', fontSize: 14 }}>&#9888;</span>
                <span style={{ fontSize: 12.5, color: '#9CA1A6', lineHeight: 1.5 }}>{w}</span>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
