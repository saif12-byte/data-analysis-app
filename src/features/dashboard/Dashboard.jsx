import Card from '../../components/Card';
import StatCard from '../../components/StatCard';
import CategoryBars from './CategoryBars';
import TrendChart from './TrendChart';
import { sampleData } from './sampleData';

export default function Dashboard({ data = sampleData, isSample = true }) {
  return (
    <Card
      title="Overview"
      subtitle={data.subtitle}
      action={isSample && <span className="badge">Sample data</span>}
    >
      <div className="stats-grid">
        {data.stats.map((stat) => (
          <StatCard key={stat.id} {...stat} />
        ))}
      </div>
      <div className="charts-grid">
        <Card
          className="tile"
          title={data.trendTitle ?? 'Revenue trend'}
          subtitle={data.trendSubtitle ?? 'Monthly revenue · ₹ lakh'}
        >
          <TrendChart
            data={data.trend}
            valueLabel={data.trendValueLabel ?? 'Revenue'}
            valueFormat={data.trendFormat ?? 'lakh'}
          />
        </Card>
        <Card
          className="tile"
          title={data.categoryTitle ?? 'Category performance'}
          subtitle={data.categorySubtitle ?? (isSample ? 'Share of sample revenue' : 'Share of revenue')}
        >
          <CategoryBars items={data.categories} />
        </Card>
      </div>
    </Card>
  );
}
