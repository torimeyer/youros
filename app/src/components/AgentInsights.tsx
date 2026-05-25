import { useState, useEffect } from "react";
import Icon from "./Icon";
import { api } from "../lib/api";
import { Card, Button } from "./ui";

interface Recommendation {
  template: string;
  type: string;
  message: string;
  severity: string;
}

interface TemplateStat {
  template: string;
  runs: number;
  success_rate: number;
  median_duration_s: number;
  best_model: string;
}

interface Adjustment {
  template: string;
  consecutive_failures: number;
  suggestion: string;
}

interface ProvenTemplate {
  name: string;
  success_rate: number;
  runs: number;
  promoted_at: string;
}

const SEVERITY_STYLES: Record<string, { bg: string; text: string; icon: string }> = {
  high: { bg: "bg-red-500/10", text: "text-red-600 dark:text-red-400", icon: "error" },
  medium: { bg: "bg-yellow-500/10", text: "text-yellow-600 dark:text-yellow-400", icon: "warning" },
  low: { bg: "bg-blue-500/10", text: "text-blue-600 dark:text-blue-400", icon: "info" },
};

function formatDuration(seconds: number): string {
  if (seconds < 60) return `${Math.round(seconds)}s`;
  const mins = Math.floor(seconds / 60);
  const secs = Math.round(seconds % 60);
  return secs > 0 ? `${mins}m ${secs}s` : `${mins}m`;
}

function formatRate(rate: number): string {
  return `${Math.round(rate * 100)}%`;
}

export default function AgentInsights() {
  const [recommendations, setRecommendations] = useState<Recommendation[]>([]);
  const [stats, setStats] = useState<TemplateStat[]>([]);
  const [adjustments, setAdjustments] = useState<Adjustment[]>([]);
  const [proven, setProven] = useState<ProvenTemplate[]>([]);
  const [loading, setLoading] = useState(true);
  const [promoting, setPromoting] = useState<string | null>(null);

  useEffect(() => {
    const load = async () => {
      try {
        const [recsData, statsData, adjData, provenData] = await Promise.all([
          api.get<{ recommendations: Recommendation[] }>("/agent-patterns/recommendations"),
          api.get<{ stats: TemplateStat[] }>("/agent-patterns/template-stats"),
          api.get<{ adjustments: Adjustment[] }>("/agent-patterns/adjustments"),
          api.get<{ proven: ProvenTemplate[] }>("/agent-patterns/proven"),
        ]);
        setRecommendations(recsData.recommendations || []);
        setStats(statsData.stats || []);
        setAdjustments(adjData.adjustments || []);
        setProven(provenData.proven || []);
      } catch {
        // graceful empty state
      } finally {
        setLoading(false);
      }
    };
    load();
  }, []);

  const handlePromote = async (templateName: string) => {
    setPromoting(templateName);
    try {
      await api.post(`/agent-patterns/promote/${encodeURIComponent(templateName)}`, {});
      const data = await api.get<{ proven: ProvenTemplate[] }>("/agent-patterns/proven");
      setProven(data.proven || []);
    } catch {
      // qualification check failed
    } finally {
      setPromoting(null);
    }
  };

  if (loading) {
    return (
      <div className="flex items-center justify-center py-16">
        <Icon name="autorenew" size={24} className="animate-spin text-slate-500" />
      </div>
    );
  }

  const hasData = stats.length > 0;

  return (
    <div className="space-y-8 mt-4" data-testid="agent-insights">
      {/* Recommendations */}
      {recommendations.length > 0 && (
        <section>
          <h3 className="text-sm font-semibold text-white mb-3 flex items-center gap-2">
            <Icon name="lightbulb" size={18} className="text-yellow-600 dark:text-yellow-400" />
            Recommendations
          </h3>
          <div className="space-y-2">
            {recommendations.map((rec, i) => {
              const style = SEVERITY_STYLES[rec.severity] || SEVERITY_STYLES.low;
              return (
                <div key={i} className={`${style.bg} rounded-lg px-4 py-3 flex items-start gap-3`}>
                  <Icon name={style.icon} size={18} className={`${style.text} mt-0.5 shrink-0`} />
                  <div>
                    <span className="text-sm text-white font-medium">{rec.template}</span>
                    <p className="text-sm text-slate-700 dark:text-slate-300 mt-0.5">{rec.message}</p>
                  </div>
                </div>
              );
            })}
          </div>
        </section>
      )}

      {/* Adjustments (failing templates) */}
      {adjustments.length > 0 && (
        <section>
          <h3 className="text-sm font-semibold text-white mb-3 flex items-center gap-2">
            <Icon name="tune" size={18} className="text-orange-600 dark:text-orange-400" />
            Suggested adjustments
          </h3>
          <div className="space-y-2">
            {adjustments.map((adj, i) => (
              <div key={i} className="bg-orange-500/10 rounded-lg px-4 py-3">
                <div className="flex items-center gap-2">
                  <span className="text-sm text-white font-medium">{adj.template}</span>
                  <span className="text-xs text-orange-600 dark:text-orange-400">{adj.consecutive_failures} failures in a row</span>
                </div>
                <p className="text-sm text-slate-700 dark:text-slate-300 mt-1">{adj.suggestion}</p>
              </div>
            ))}
          </div>
        </section>
      )}

      {/* Template performance */}
      {hasData && (
        <section>
          <h3 className="text-sm font-semibold text-white mb-3 flex items-center gap-2">
            <Icon name="analytics" size={18} className="text-blue-600 dark:text-blue-400" />
            Template performance
          </h3>
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-slate-600 dark:text-slate-400 text-xs uppercase tracking-wider border-b border-slate-200 dark:border-slate-800">
                  <th className="text-left py-2 pr-4">Template</th>
                  <th className="text-right py-2 px-4">Runs</th>
                  <th className="text-right py-2 px-4">Success</th>
                  <th className="text-right py-2 px-4">Median time</th>
                  <th className="text-right py-2 px-4">Best model</th>
                  <th className="text-right py-2 pl-4"></th>
                </tr>
              </thead>
              <tbody>
                {stats.map((stat) => {
                  const isProven = proven.some((p) => p.name === stat.template);
                  const rateColor =
                    stat.success_rate >= 0.8 ? "text-green-600 dark:text-green-400" :
                    stat.success_rate >= 0.5 ? "text-yellow-600 dark:text-yellow-400" : "text-red-600 dark:text-red-400";
                  return (
                    <tr key={stat.template} className="border-b border-slate-200 dark:border-slate-800/50 hover:bg-slate-100 dark:hover:bg-slate-800/30">
                      <td className="py-2.5 pr-4 text-white flex items-center gap-2">
                        {stat.template}
                        {isProven && (
                          <span className="bg-green-500/20 text-green-600 dark:text-green-400 text-xs px-1.5 py-0.5 rounded-full">proven</span>
                        )}
                      </td>
                      <td className="text-right py-2.5 px-4 text-slate-700 dark:text-slate-300">{stat.runs}</td>
                      <td className={`text-right py-2.5 px-4 font-medium ${rateColor}`}>
                        {formatRate(stat.success_rate)}
                      </td>
                      <td className="text-right py-2.5 px-4 text-slate-700 dark:text-slate-300">
                        {formatDuration(stat.median_duration_s)}
                      </td>
                      <td className="text-right py-2.5 px-4 text-slate-600 dark:text-slate-400">
                        {stat.best_model || "—"}
                      </td>
                      <td className="text-right py-2.5 pl-4">
                        {!isProven && stat.runs >= 2 && stat.success_rate >= 0.75 && (
                          <Button
                            variant="secondary"
                            size="sm"
                            onClick={() => handlePromote(stat.template)}
                            disabled={promoting === stat.template}
                          >
                            {promoting === stat.template ? "..." : "Promote"}
                          </Button>
                        )}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </section>
      )}

      {/* Proven templates */}
      {proven.length > 0 && (
        <section>
          <h3 className="text-sm font-semibold text-white mb-3 flex items-center gap-2">
            <Icon name="verified" size={18} className="text-green-600 dark:text-green-400" />
            Proven templates
          </h3>
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
            {proven.map((p) => (
              <Card key={p.name} className="p-4">
                <div className="flex items-center justify-between">
                  <span className="text-sm text-white font-medium">{p.name}</span>
                  <span className="text-green-600 dark:text-green-400 text-xs font-medium">{formatRate(p.success_rate)}</span>
                </div>
                <p className="text-xs text-slate-600 dark:text-slate-400 mt-1">
                  {p.runs} runs
                </p>
              </Card>
            ))}
          </div>
        </section>
      )}

      {/* Empty state */}
      {!hasData && recommendations.length === 0 && (
        <div className="text-center py-12">
          <Icon name="analytics" size={40} className="text-slate-600 mx-auto mb-3" />
          <p className="text-slate-600 dark:text-slate-400">No agent data yet.</p>
          <p className="text-slate-500 text-sm mt-1">
            Run a few agents and insights will appear here.
          </p>
        </div>
      )}
    </div>
  );
}
