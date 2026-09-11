import { BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, Cell } from 'recharts';

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
          <CartesianGrid strokeDasharray="3 3" stroke="#33383D" vertical={false} />
          <XAxis dataKey="name" tick={{ fill: '#9CA1A6', fontSize: 12 }} axisLine={{ stroke: '#33383D' }} tickLine={false} />
          <YAxis tick={{ fill: '#9CA1A6', fontSize: 12 }} axisLine={false} tickLine={false} unit="%" />
          <Tooltip
            contentStyle={{ background: '#1D2023', border: '1px solid #33383D', borderRadius: 4, fontSize: 13 }}
            labelStyle={{ color: '#ECEAE4' }}
            formatter={(value) => [`${value}%`, 'Engagement rate']}
          />
          <Bar dataKey="engagement" radius={[2, 2, 0, 0]}>
            {data.map((entry, idx) => (
              <Cell key={idx} fill={entry.isYou ? '#C6FF4E' : '#4A5157'} />
            ))}
          </Bar>
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}
