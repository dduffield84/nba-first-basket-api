export function pct(x: number | null | undefined, digits = 1): string {
  return x === null || x === undefined ? '–' : (x * 100).toFixed(digits) + '%';
}

export function american(x: number | null | undefined): string {
  if (x === null || x === undefined) return '–';
  const n = Math.round(x);
  return n > 0 ? '+' + n : String(n);
}

export function ev(x: number | null | undefined): string {
  if (x === null || x === undefined) return '–';
  return (x >= 0 ? '+' : '') + (x * 100).toFixed(1) + '%';
}

export function when(iso: string | null | undefined): string {
  if (!iso) return '–';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString('en-US', { timeZone: 'America/New_York',
    month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' }) + ' ET';
}
