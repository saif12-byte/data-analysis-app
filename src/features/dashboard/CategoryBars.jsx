import { Cell, Pie, PieChart, ResponsiveContainer, Tooltip } from 'recharts';
import { formatValue } from '../../utils/formatters';

export default function CategoryBars({ items, chartType = 'bar', valueLabel = 'Value' }) {
  const hasAggregateValues = items.some((item) => item.value !== undefined);
  const values = items.map((item) => item.value ?? item.share);
  if (chartType === 'pie') {
    const pieItems = items
      .map((item) => ({ ...item, value: item.value ?? item.share }))
      .filter((item) => item.value > 0);
    if (pieItems.length === 0) {
      return <p className="analysis-privacy">Pie charts need positive values. Choose a bar chart to view these groups.</p>;
    }
    const pieTotal = pieItems.reduce((total, item) => total + item.value, 0);
    return (
      <div className="category-pie-layout">
        <div className="category-pie-chart">
          <ResponsiveContainer width="100%" height="100%">
            <PieChart>
              <Pie
                data={pieItems}
                dataKey="value"
                nameKey="name"
                cx="50%"
                cy="50%"
                outerRadius="82%"
                stroke="var(--card)"
                strokeWidth={2}
              >
                {pieItems.map((item) => <Cell key={item.name} fill={item.color} />)}
              </Pie>
              <Tooltip
                contentStyle={{ background: 'var(--card)', border: '1px solid var(--border)', borderRadius: 8 }}
                labelStyle={{ color: 'var(--text)' }}
                itemStyle={{ color: 'var(--text)' }}
                formatter={(value) => {
                  const percentage = pieTotal ? (Number(value) / pieTotal) * 100 : 0;
                  return [`${formatValue(value, 'number')} · ${percentage.toFixed(1)}%`, valueLabel];
                }}
              />
            </PieChart>
          </ResponsiveContainer>
        </div>
        <ul className="category-pie-legend" aria-label="Chart categories">
          {pieItems.map((item) => (
            <li key={item.name} title={`${item.name}: ${formatValue(item.value, 'number')}`}>
              <span className="category-pie-swatch" style={{ background: item.color }} aria-hidden="true" />
              <span className="category-pie-name">{item.name}</span>
              <span className="category-pie-value">{formatValue(item.value, 'number')}</span>
            </li>
          ))}
        </ul>
      </div>
    );
  }

  const maxValue = hasAggregateValues ? Math.max(0, ...values.map((value) => Math.abs(value))) : 100;
  return (
    <div className="category-bar-list">
      {items.map((item) => (
        <div className="bar-row" key={item.name}>
          <div className="bar-top">
            <span className="bar-name" title={item.name}>{item.name}</span>
            <span className="bar-value">{formatValue(item.value ?? item.share, hasAggregateValues ? 'number' : 'percent')}</span>
          </div>
          <div className="bar-track" role="progressbar" aria-label={item.name} aria-valuenow={maxValue ? Math.round(Math.abs(item.value ?? item.share) / maxValue * 100) : 0} aria-valuemin={0} aria-valuemax={100}>
            <div className="bar-fill" style={{ width: `${maxValue ? Math.abs(item.value ?? item.share) / maxValue * 100 : 0}%`, background: item.color }} />
          </div>
        </div>
      ))}
    </div>
  );
}
