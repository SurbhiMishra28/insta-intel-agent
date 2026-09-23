import { BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, Cell, LabelList } from 'recharts';

export default function EngagementChart({ allInsights, mainUsername }) {
  const data = allInsights.map((i) => ({
    name: '@' + i.profile.username,
    engagement: i.metrics.engagement_rate,
    isYou: i.profile.username === mainUsername,
  }));

  return (
    <div className="chart-panel">
      <ResponsiveContainer width="100%" height={240}>
        <BarChart data={data} margin={{ top: 8, right: 8, left: -20, bottom: 8 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="#2A3350" vertical={false} />
          <XAxis dataKey="name" tick={{ fill: '#FFFFFF', fontSize: 12 }} axisLine={{ stroke: '#5A6485' }} tickLine={false} />
          <YAxis tick={{ fill: '#FFFFFF', fontSize: 12 }} axisLine={false} tickLine={false} unit="%" />
          <Tooltip
            contentStyle={{ background: 'var(--panel)', border: '1px solid #5A6485', borderRadius: 8, fontSize: 13 }}
            labelStyle={{ color: '#FFFFFF' }}
            itemStyle={{ color: '#FFFFFF' }}
            cursor={{ fill: 'rgba(255, 255, 255, 0.06)' }}
            formatter={(value) => [`${value}%`, 'Engagement rate']}
          />
          <Bar dataKey="engagement" radius={[2, 2, 0, 0]}>
            <LabelList
              dataKey="engagement"
              position="top"
              formatter={(v) => `${v}%`}
              style={{ fill: '#FFFFFF', fontSize: 12, fontWeight: 600 }}
            />
            {data.map((entry, idx) => (
              <Cell key={idx} fill={entry.isYou ? '#8B7CFF' : '#39415E'} />
            ))}
          </Bar>
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}
