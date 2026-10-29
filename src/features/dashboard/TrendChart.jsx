import { Area, AreaChart, Bar, BarChart, CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts';
import { formatValue } from '../../utils/formatters';

export default function TrendChart({ data, valueLabel = 'Revenue', valueFormat = 'lakh', chartType = 'area' }) {
  const lastIndex = data.length - 1;
  const dataKey = data[0]?.value === undefined ? 'revenue' : 'value';
  const Chart = chartType === 'line' ? LineChart : chartType === 'bar' ? BarChart : AreaChart;
  const series = chartType === 'line'
    ? <Line type="monotone" dataKey={dataKey} stroke="var(--primary)" strokeWidth={2} dot={false} />
    : chartType === 'bar'
      ? <Bar dataKey={dataKey} fill="var(--primary)" radius={[4, 4, 0, 0]} />
      : <Area
          type="monotone"
          dataKey={dataKey}
          stroke="var(--primary)"
          strokeWidth={2}
          fill="url(#trendFill)"
          dot={({ cx, cy, index }) =>
            index === lastIndex ? <circle key="last" cx={cx} cy={cy} r={4} fill="var(--secondary)" /> : <g key={index} />
          }
        />;

  return (
    <ResponsiveContainer width="100%" height={240}>
      <Chart data={data} margin={{ top: 10, right: 12, left: 4, bottom: 8 }}>
        <defs>
          <linearGradient id="trendFill" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" style={{ stopColor: 'var(--primary)', stopOpacity: 0.3 }} />
            <stop offset="100%" style={{ stopColor: 'var(--primary)', stopOpacity: 0.05 }} />
          </linearGradient>
        </defs>
        <CartesianGrid vertical={false} stroke="var(--border)" />
        <XAxis
          dataKey="month"
          axisLine={false}
          tickLine={false}
          tick={{ fill: 'var(--muted)', fontSize: 10 }}
          tickMargin={10}
          minTickGap={24}
          interval="preserveStartEnd"
        />
        <YAxis hide domain={[0, 'auto']} />
        <Tooltip
          contentStyle={{ background: 'var(--card)', border: '1px solid var(--border)', borderRadius: 8 }}
          labelStyle={{ color: 'var(--text)', marginBottom: 4 }}
          itemStyle={{ color: 'var(--text)' }}
          formatter={(value) => [formatValue(value, valueFormat), valueLabel]}
        />
        {series}
      </Chart>
    </ResponsiveContainer>
  );
}
