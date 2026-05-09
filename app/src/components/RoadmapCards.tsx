import type { RoadmapQuarter } from '../lib/parseRoadmapJson';

interface Props {
  quarters: RoadmapQuarter[];
  onNavigateToChat: () => void;
}

export function RoadmapCards({ quarters, onNavigateToChat }: Props) {
  function handleBuildIt(initiative: string) {
    try {
      sessionStorage.setItem('chat_pending_input', `Build ${initiative}`);
    } catch { /* ignore */ }
    onNavigateToChat();
  }

  if (quarters.length === 0) return null;

  return (
    <div className="mt-3 pt-3 border-t border-slate-800" data-testid="roadmap-cards">
      <p className="text-xs font-semibold text-slate-400 uppercase tracking-wider mb-3">Roadmap</p>
      <div className="flex flex-col gap-3">
        {quarters.map((q) => (
          <div key={q.quarter} className="bg-slate-950 rounded-lg p-3">
            <div className="flex items-baseline gap-2 mb-2">
              <span className="text-xs font-bold text-sky-400">{q.quarter}</span>
              <span className="text-xs text-slate-400">{q.theme}</span>
            </div>
            <div className="flex flex-wrap gap-2">
              {q.initiatives.map((initiative) => (
                <button
                  key={initiative}
                  data-testid="roadmap-build-btn"
                  onClick={() => handleBuildIt(initiative)}
                  className="inline-flex items-center gap-1.5 text-xs bg-slate-800 hover:bg-sky-900/60 border border-slate-700 hover:border-sky-700 text-slate-300 hover:text-sky-300 rounded-full px-3 py-1 transition-colors"
                >
                  <span>{initiative}</span>
                  <span className="text-sky-500 font-semibold ml-0.5">· Build it</span>
                </button>
              ))}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
