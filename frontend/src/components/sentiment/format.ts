/** A score from -1 to 1 as a signed number: +0.42, -0.10, 0.00. */
export function signed(score: number | null | undefined): string {
  if (score === null || score === undefined) return '—';
  return `${score > 0 ? '+' : ''}${score.toFixed(2)}`;
}
