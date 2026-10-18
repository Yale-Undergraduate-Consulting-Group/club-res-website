export function trackingTime(value?: string | null) {
  if (!value) return 'Not yet';
  const normalized = value.includes('T') ? value : `${value.replace(' ', 'T')}Z`;
  return new Date(normalized).toLocaleString();
}
