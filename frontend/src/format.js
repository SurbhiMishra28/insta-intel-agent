/* Shared number formatting for every display surface.
   Big counts (followers, likes, comment totals) render as K/M so
   680000000 reads "680M" and 291000 reads "291K". Decimal metrics
   (avg comments 0.2, engagement 4.579%) keep their exact value. */

export function fmtCompact(n) {
  if (n == null || n === '' || Number.isNaN(Number(n))) return '—';
  const x = Number(n);
  const neg = x < 0;
  const v = Math.abs(x);
  let out;
  if (v >= 1_000_000_000) out = trim(v / 1_000_000_000) + 'B';
  else if (v >= 1_000_000) out = trim(v / 1_000_000) + 'M';
  else if (v >= 1_000) out = trim(v / 1_000) + 'K';
  else out = Number.isInteger(v) ? String(v) : v.toFixed(1);
  return (neg ? '-' : '') + out;
}

function trim(x) {
  // 1.0 -> "1", 1.25 -> "1.25", 12.3 -> "12.3"
  const s = x.toFixed(2);
  return s.replace(/\.?0+$/, '');
}

/* Exact values: keep decimals, never abbreviate (0.2 must stay 0.2). */
export function fmtExact(n) {
  if (n == null || n === '' || Number.isNaN(Number(n))) return '—';
  const x = Number(n);
  if (Number.isInteger(x) && Math.abs(x) < 10000) return x.toLocaleString('en-US');
  if (!Number.isInteger(x)) return x.toFixed(1);
  return fmtCompact(x);
}

/* Per-post comment/like cells: small ints stay plain, huge counts K/M. */
export function fmtCount(n) {
  return fmtCompact(n);
}
