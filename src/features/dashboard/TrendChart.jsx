import { Area, AreaChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts';
import { formatValue } from '../../utils/formatters';

export default function TrendChart({ data, valueLabel = 'Revenue', valueFormat = 'lakh' }) {
  const lastIndex = data.length - 1;
  return (
    <ResponsiveContainer width="100%" height={200}>
      <AreaChart data={data} margin={{ top: 8, right: 8, left: 8, bottom: 0 }}>
        <defs>
          <linearGradient id="trendFill" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" style={{ stopColor: 'var(--primary)', stopOpacity: 0.3 }} />
            <stop offset="100%" style={{ stopColor: 'var(--primary)', stopOpacity: 0.05 }} />
          </linearGradient>
        </defs>
        <CartesianGrid vertical={false} stroke="var(--border)" />
        <XAxis dataKey="month" axisLine={false} tickLine={false} tick={{ fill: 'var(--muted)', fontSize: 11 }} />
        <YAxis hide domain={[0, 'auto']} />
        <Tooltip
          contentStyle={{ background: 'var(--card)', border: '1px solid var(--border)', borderRadius: 8 }}
          formatter={(value) => [formatValue(value, valueFormat), valueLabel]}
        />
        <Area
          type="monotone"
          dataKey="revenue"
          stroke="var(--primary)"
          strokeWidth={2}
          fill="url(#trendFill)"
          dot={({ cx, cy, index }) =>
            index === lastIndex ? <circle key="last" cx={cx} cy={cy} r={4} fill="var(--secondary)" /> : <g key={index} />
          }
        />
      </AreaChart>
    </ResponsiveContainer>
  );
}
