export default function CategoryBars({ items }) {
  return (
    <div>
      {items.map((item) => (
        <div className="bar-row" key={item.name}>
          <div className="bar-top">
            <span>{item.name}</span>
            <span>{item.share}%</span>
          </div>
          <div className="bar-track" role="progressbar" aria-label={item.name} aria-valuenow={item.share} aria-valuemin={0} aria-valuemax={100}>
            <div className="bar-fill" style={{ width: `${item.share}%`, background: item.color }} />
          </div>
        </div>
      ))}
    </div>
  );
}
