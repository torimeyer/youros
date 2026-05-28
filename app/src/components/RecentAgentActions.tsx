import Icon from "./Icon";
import { type AgentInfo } from "../lib/agentUtils";

interface Props {
  agent: AgentInfo;
  onRunAgain?: (name: string, template: string) => void;
  onSaveGem?: (agentName: string) => void;
}

function templateAction(tpl: string): { label: string; icon: string } | null {
  if (tpl.includes("planner") || tpl.includes("planning")) {
    return { label: "Replan", icon: "refresh" };
  }
  if (tpl === "gem-builder") {
    return { label: "Save to gems", icon: "star" };
  }
  return null;
}

export function RecentAgentActions({ agent, onRunAgain, onSaveGem }: Props) {
  const tpl = (agent.template || "").toLowerCase().trim();
  if (!tpl) return null;

  const specific = templateAction(tpl);
  const runAgainName = `${tpl.replace(/\s+/g, "-")}-${Date.now()}`;

  const handleRunAgain = () => onRunAgain?.(runAgainName, agent.template!);
  const handleSaveGem = () => onSaveGem?.(agent.name);

  return (
    <div className="flex flex-wrap gap-2 mt-2">
      {specific ? (
        <>
          <button
            data-testid={`action-${specific.label.toLowerCase().replace(/\s+/g, "-")}`}
            onClick={specific.label === "Save to gems" ? handleSaveGem : handleRunAgain}
            className="inline-flex items-center gap-1.5 text-xs bg-slate-100 dark:bg-slate-800 hover:bg-blue-900/60 border border-slate-200 dark:border-slate-700 hover:border-blue-600 text-slate-700 dark:text-slate-300 hover:text-blue-700 dark:hover:text-blue-300 rounded-full px-3 py-1 transition-colors"
          >
            <Icon name={specific.icon} size={12} />
            {specific.label}
          </button>
          <button
            data-testid="action-run-again"
            onClick={handleRunAgain}
            className="inline-flex items-center gap-1.5 text-xs bg-slate-100 dark:bg-slate-800 hover:bg-slate-200 dark:hover:bg-slate-700 border border-slate-200 dark:border-slate-700 text-slate-600 dark:text-slate-400 hover:text-slate-800 dark:hover:text-slate-200 rounded-full px-3 py-1 transition-colors"
          >
            <Icon name="replay" size={12} />
            Run again
          </button>
        </>
      ) : (
        <button
          data-testid="action-run-again"
          onClick={handleRunAgain}
          className="inline-flex items-center gap-1.5 text-xs bg-slate-100 dark:bg-slate-800 hover:bg-slate-200 dark:hover:bg-slate-700 border border-slate-200 dark:border-slate-700 text-slate-600 dark:text-slate-400 hover:text-slate-800 dark:hover:text-slate-200 rounded-full px-3 py-1 transition-colors"
        >
          <Icon name="replay" size={12} />
          Run again
        </button>
      )}
    </div>
  );
}
