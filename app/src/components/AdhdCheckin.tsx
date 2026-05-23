import { useEffect, useState } from "react";
import Icon from "./Icon";

interface CheckInAgent {
  name: string;
  task: string;
  current_step: string;
}

interface Props {
  running_count: number;
  agents: CheckInAgent[];
  intervalSeconds: number;
}

export default function AdhdCheckin({ running_count, agents, intervalSeconds }: Props) {
  const [visible, setVisible] = useState(false);
  const [lastShown, setLastShown] = useState(0);

  useEffect(() => {
    if (running_count === 0) {
      setVisible(false);
      return;
    }

    const now = Date.now();
    if (now - lastShown >= intervalSeconds * 1000) {
      setVisible(true);
      setLastShown(now);
      const timer = setTimeout(() => setVisible(false), 5000);
      return () => clearTimeout(timer);
    }
  }, [running_count, agents, intervalSeconds, lastShown]);

  if (!visible || running_count === 0) return null;

  return (
    <div className="fixed bottom-4 right-4 z-50 max-w-sm animate-slide-up">
      <div className="bg-slate-800 border border-slate-700 rounded-xl p-4 shadow-2xl">
        <div className="flex items-center gap-2 mb-2">
          <div className="w-2 h-2 rounded-full bg-green-400 animate-pulse" />
          <span className="text-sm font-medium text-white">
            {running_count} agent{running_count > 1 ? "s" : ""} working
          </span>
          <button
            onClick={() => setVisible(false)}
            className="ml-auto text-slate-500 hover:text-slate-300"
          >
            <Icon name="close" size={16} />
          </button>
        </div>
        <div className="space-y-1.5">
          {agents.slice(0, 3).map((agent) => (
            <div key={agent.name} className="text-xs text-slate-400">
              <span className="text-slate-300 font-medium">
                {agent.task || agent.name.split("/").pop()}
              </span>
              {agent.current_step && (
                <span> — {agent.current_step.slice(0, 60)}</span>
              )}
            </div>
          ))}
          {agents.length > 3 && (
            <div className="text-xs text-slate-500">
              +{agents.length - 3} more
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
