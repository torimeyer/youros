import Icon from "./Icon";

interface ActiveAgent {
  name: string;
  task: string;
  current_step: string;
  status: string;
}

interface RecentAgent {
  name: string;
  task: string;
  summary: string;
  status: string;
  completed_at: string;
}

interface Task {
  id: string;
  title: string;
  priority?: string;
  labels: string[];
}

interface ContextRebuild {
  active_agents: ActiveAgent[];
  recent_agents: RecentAgent[];
  in_progress_tasks: Task[];
  last_chat: {
    role: string;
    preview: string;
    timestamp?: string;
  } | null;
  next_step: string;
}

interface Props {
  context: ContextRebuild;
  onDismiss: () => void;
}

export default function WelcomeBack({ context, onDismiss }: Props) {
  const hasContent =
    context.active_agents.length > 0 ||
    context.recent_agents.length > 0 ||
    context.in_progress_tasks.length > 0 ||
    context.last_chat;

  if (!hasContent) return null;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 backdrop-blur-sm">
      <div className="bg-slate-900 border border-slate-700 rounded-2xl p-6 max-w-lg w-full mx-4 shadow-2xl">
        <div className="flex items-center justify-between mb-4">
          <h2 className="text-lg font-semibold text-white flex items-center gap-2">
            <Icon name="waving_hand" size={24} className="text-yellow-400" />
            Welcome back
          </h2>
          <button
            onClick={onDismiss}
            className="text-slate-500 hover:text-slate-300 transition-colors"
          >
            <Icon name="close" size={20} />
          </button>
        </div>

        <div className="bg-blue-500/10 border border-blue-500/20 rounded-lg p-3 mb-4">
          <p className="text-sm text-blue-300">{context.next_step}</p>
        </div>

        {context.active_agents.length > 0 && (
          <div className="mb-4">
            <h3 className="text-xs font-semibold text-slate-400 uppercase tracking-wider mb-2">
              Still running
            </h3>
            <div className="space-y-2">
              {context.active_agents.map((agent) => (
                <div
                  key={agent.name}
                  className="flex items-center gap-2 text-sm"
                >
                  <div className="w-2 h-2 rounded-full bg-green-400 animate-pulse flex-shrink-0" />
                  <span className="text-slate-300 truncate">
                    {agent.task || agent.name.split("/").pop()}
                  </span>
                  {agent.current_step && (
                    <span className="text-slate-500 truncate">
                      {agent.current_step.slice(0, 40)}
                    </span>
                  )}
                </div>
              ))}
            </div>
          </div>
        )}

        {context.recent_agents.length > 0 && (
          <div className="mb-4">
            <h3 className="text-xs font-semibold text-slate-400 uppercase tracking-wider mb-2">
              Recently finished
            </h3>
            <div className="space-y-2">
              {context.recent_agents.slice(0, 3).map((agent) => (
                <div key={agent.name} className="text-sm">
                  <div className="flex items-center gap-2">
                    <Icon
                      name={agent.status === "completed" || agent.status === "done" ? "check_circle" : "error"}
                      size={16}
                      className={agent.status === "completed" || agent.status === "done" ? "text-green-400" : "text-red-400"}
                    />
                    <span className="text-slate-300 truncate">
                      {agent.task.slice(0, 50)}
                    </span>
                  </div>
                  {agent.summary && (
                    <p className="text-xs text-slate-500 ml-6 mt-0.5">
                      {agent.summary.slice(0, 80)}
                    </p>
                  )}
                </div>
              ))}
            </div>
          </div>
        )}

        {context.in_progress_tasks.length > 0 && (
          <div className="mb-4">
            <h3 className="text-xs font-semibold text-slate-400 uppercase tracking-wider mb-2">
              Open tasks
            </h3>
            <div className="space-y-1.5">
              {context.in_progress_tasks.slice(0, 5).map((task) => (
                <div
                  key={task.id}
                  className="flex items-center gap-2 text-sm"
                >
                  <Icon name="radio_button_unchecked" size={14} className="text-blue-400 flex-shrink-0" />
                  <span className="text-slate-300 truncate">
                    {task.title}
                  </span>
                  {task.priority && (
                    <span className={`text-xs px-1.5 py-0.5 rounded ${
                      task.priority === "P0" ? "bg-red-500/20 text-red-400" :
                      task.priority === "P1" ? "bg-orange-500/20 text-orange-400" :
                      "bg-slate-700 text-slate-400"
                    }`}>
                      {task.priority}
                    </span>
                  )}
                </div>
              ))}
            </div>
          </div>
        )}

        {context.last_chat && (
          <div className="mb-4">
            <h3 className="text-xs font-semibold text-slate-400 uppercase tracking-wider mb-2">
              Last thing you said
            </h3>
            <p className="text-sm text-slate-400 italic">
              "{context.last_chat.preview.slice(0, 120)}{context.last_chat.preview.length > 120 ? "..." : ""}"
            </p>
          </div>
        )}

        <button
          onClick={onDismiss}
          className="w-full mt-2 py-2 bg-blue-600 hover:bg-blue-500 text-white text-sm font-medium rounded-lg transition-colors"
        >
          Got it, let's go
        </button>
      </div>
    </div>
  );
}
