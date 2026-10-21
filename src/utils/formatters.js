const number = new Intl.NumberFormat('en-IN'); // 8,421 and lakh-style grouping

const FORMATTERS = {
  lakh: (v) => `₹${v}L`,
  currency: (v) => `₹${number.format(v)}`,
  number: (v) => number.format(v),
  percent: (v) => `${v}%`,
};

// To support a new display type, add one line above. Nothing else changes.
export function formatValue(value, format = 'number') {
  return (FORMATTERS[format] ?? FORMATTERS.number)(value);
}

export function formatChange({ value, unit = '%' }) {
  const arrow = value >= 0 ? '↑' : '↓';
  return `${arrow} ${Math.abs(value)}${unit} vs. prior period`;
}
