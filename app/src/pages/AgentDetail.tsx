import { formatTokenBudgetApprox } from "../lib/budgetDisplay";

interface CapabilitiesSectionProps {
  writesTo?: string | null;
  budget?: number | string | null;
  model?: string | null;
  timeLimit?: string | null;
}

export function CapabilitiesSection({ writesTo, budget, model, timeLimit }: CapabilitiesSectionProps) {
  const tokenBudget = formatTokenBudgetApprox(budget, model);

  const hasAny = writesTo || tokenBudget || timeLimit;
  if (!hasAny) return null;

  return (
    <div data-testid="capabilities-section">
      <p className="text-xs font-semibold text-slate-400 uppercase tracking-wider mb-2">Capabilities</p>
      <div className="grid grid-cols-2 gap-2 text-xs text-slate-300">
        {writesTo && (
          <div className="bg-slate-800/60 rounded-lg px-3 py-2">
            <span className="text-slate-500 block text-[10px]">Writes to</span>
            {writesTo}
          </div>
        )}
        {tokenBudget && (
          <div className="bg-slate-800/60 rounded-lg px-3 py-2">
            <span className="text-slate-500 block text-[10px]">Token budget</span>
            {tokenBudget}
          </div>
        )}
        {timeLimit && (
          <div className="bg-slate-800/60 rounded-lg px-3 py-2">
            <span className="text-slate-500 block text-[10px]">Time limit</span>
            {timeLimit}
          </div>
        )}
      </div>
    </div>
  );
}
