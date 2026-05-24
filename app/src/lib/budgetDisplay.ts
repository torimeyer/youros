// Honest labels for per-agent limits.
//
// Tori runs Claude Code on a subscription plan (the `claude` CLI), not the
// per-token API. The backend still stores a numeric "budget" field for each
// agent, and uses it as a tokens x price throttle so no agent can run away
// with unbounded context. It is NOT a dollar charge. Nothing is debited.
//
// This module converts the stored numeric budget into a human-readable token
// figure (e.g. "2M tokens") for display only. The underlying enforcement,
// storage, and API payloads keep the same numeric value.
//
// Price source: api/routers/agents.py `_COST_PER_MILLION_TOKENS`
//   claude-opus-4-6   $30/M
//   claude-sonnet-4-6  $6/M
//   claude-haiku-4-5   $2/M
// We treat the stored budget number as dollars-equivalent and divide by the
// per-million-token rate for the agent's model to recover a token figure,
// then round to a human-friendly shape like "2M tokens" or "500k tokens".
// This keeps the conversion transparent and lets one edit in one place
// keep every surface consistent.

const DEFAULT_RATE_USD_PER_1M = 6.0; // sonnet fallback when model is unknown

const RATE_USD_PER_1M: Array<[RegExp, number]> = [
  [/opus/i, 30.0],
  [/sonnet/i, 6.0],
  [/haiku/i, 2.0],
];

function rateFor(model?: string | null): number {
  if (!model) return DEFAULT_RATE_USD_PER_1M;
  for (const [pattern, rate] of RATE_USD_PER_1M) {
    if (pattern.test(model)) return rate;
  }
  return DEFAULT_RATE_USD_PER_1M;
}

function roundToReadable(tokens: number): string {
  if (!isFinite(tokens) || tokens <= 0) return "0";
  if (tokens >= 1_000_000) {
    const m = Math.round(tokens / 1_000_000);
    return `${m}M`;
  }
  if (tokens >= 10_000) {
    const k = Math.round(tokens / 10_000) * 10;
    return `${k}k`;
  }
  if (tokens >= 1_000) {
    const k = Math.round(tokens / 1_000);
    return `${k}k`;
  }
  return String(Math.round(tokens));
}

function parseBudget(budget: number | string | null | undefined): number {
  if (budget === null || budget === undefined) return 0;
  if (typeof budget === "number") return budget;
  const parsed = parseFloat(budget);
  return isFinite(parsed) ? parsed : 0;
}

/**
 * Convert the stored budget number (treated as dollars) into an estimated
 * token count for the given model.
 */
export function budgetToTokens(
  budget: number | string | null | undefined,
  model?: string | null,
): number {
  const dollars = parseBudget(budget);
  if (dollars <= 0) return 0;
  const rate = rateFor(model);
  return (dollars / rate) * 1_000_000;
}

/**
 * Short form used in the Active Sessions stats pipe line. Example output:
 *   "2M tokens", "5M tokens", "500k tokens".
 * Returns an empty string when there is no budget to report.
 */
export function formatTokenBudget(
  budget: number | string | null | undefined,
  model?: string | null,
): string {
  const tokens = budgetToTokens(budget, model);
  if (tokens <= 0) return "";
  return `${roundToReadable(tokens)} tokens`;
}

/**
 * Longer form used in the agent detail panel, e.g. "~2M tokens".
 */
export function formatTokenBudgetApprox(
  budget: number | string | null | undefined,
  model?: string | null,
): string {
  const tokens = budgetToTokens(budget, model);
  if (tokens <= 0) return "";
  return `~${roundToReadable(tokens)} tokens`;
}
