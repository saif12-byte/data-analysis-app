// Fictional data. A real backend response should follow this same shape.
export const sampleData = {
  subtitle: 'Fictional retail dataset · January–June',
  stats: [
    { id: 'revenue', label: 'Revenue', value: 24.8, format: 'lakh', change: { value: 12.4, unit: '%' } },
    { id: 'orders', label: 'Orders', value: 8421, format: 'number', change: { value: 8.2, unit: '%' } },
    { id: 'aov', label: 'Average order', value: 2945, format: 'currency', change: { value: 3.9, unit: '%' } },
    { id: 'conversion', label: 'Conversion', value: 4.6, format: 'percent', change: { value: 0.5, unit: ' pts' } },
  ],
  trend: [
    { month: 'Jan', revenue: 19.6 },
    { month: 'Feb', revenue: 20.8 },
    { month: 'Mar', revenue: 21.9 },
    { month: 'Apr', revenue: 22.9 },
    { month: 'May', revenue: 24.1 },
    { month: 'Jun', revenue: 24.8 },
  ],
  categories: [
    { name: 'Electronics', share: 34, color: 'var(--primary)' },
    { name: 'Fashion', share: 27, color: 'var(--secondary)' },
    { name: 'Home', share: 21, color: 'var(--yellow)' },
    { name: 'Beauty', share: 18, color: 'var(--muted)' },
  ],
};
