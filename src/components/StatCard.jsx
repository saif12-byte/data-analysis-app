import { formatChange, formatValue } from '../utils/formatters';

export default function StatCard({ label, value, format, change, detail }) {
  const isUp = change?.value >= 0;
  return (
    <div className="card tile">
      <p className="stat-label">{label}</p>
      <p className="stat-value">{formatValue(value, format)}</p>
      {change && <p className={`stat-change ${isUp ? 'is-up' : 'is-down'}`}>{formatChange(change)}</p>}
      {detail && <p className="stat-detail">{detail}</p>}
    </div>
  );
}
