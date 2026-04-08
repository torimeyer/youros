import { useState, useEffect } from "react";
import { useNavigate } from "react-router-dom";
import TopBar from "../components/TopBar";
import Icon from "../components/Icon";
import { api } from "../lib/api";

interface WorkflowStep {
  id: string;
  agent_name: string;
  prompt: string;
  model: string;
  budget: number;
  depends_on: string[];
  status: string;
}

interface Workflow {
  id: string;
  name: string;
  steps: WorkflowStep[];
  status: string;
  created_at: string;
  completed_at: string | null;
}

interface WorkflowsResponse {
  workflows: Workflow[];
}

const statusStyles: Record<string, string> = {
  pending: "bg-slate-700 text-slate-300",
  running: "bg-blue-500/20 text-blue-400",
  done: "bg-green-500/20 text-green-400",
  failed: "bg-red-500/20 text-red-400",
};

export default function Workflows() {
  const navigate = useNavigate();
  const [workflows, setWorkflows] = useState<Workflow[]>([]);
  const [loading, setLoading] = useState(true);
  const [message, setMessage] = useState("");
  const [messageType, setMessageType] = useState<"success" | "error">("success");

  const fetchWorkflows = async () => {
    try {
      const data = await api.get<WorkflowsResponse>("/workflows");
      setWorkflows(data.workflows || []);
    } catch {
      // keep empty state
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchWorkflows();
  }, []);

  const showMessage = (text: string, type: "success" | "error" = "success") => {
    setMessage(text);
    setMessageType(type);
    setTimeout(() => setMessage(""), 4000);
  };

  const handleRun = async (id: string) => {
    try {
      await api.post(`/workflows/${id}/run`);
      showMessage("Workflow started.");
      fetchWorkflows();
    } catch {
      showMessage("Could not start workflow.", "error");
    }
  };

  const handleDelete = async (id: string) => {
    if (!confirm("Delete this workflow?")) return;
    try {
      await api.delete(`/workflows/${id}`);
      setWorkflows((prev) => prev.filter((w) => w.id !== id));
      showMessage("Workflow deleted.");
    } catch {
      showMessage("Could not delete workflow.", "error");
    }
  };

  return (
    <div className="flex flex-col h-full">
      <TopBar title="Workflows" />
      <div className="flex-1 overflow-y-auto p-6 max-w-4xl mx-auto w-full">
        {message && (
          <div
            className={`mb-4 px-4 py-3 rounded-lg text-sm font-medium ${
              messageType === "success"
                ? "bg-green-500/20 text-green-400"
                : "bg-red-500/20 text-red-400"
            }`}
          >
            {message}
          </div>
        )}

        <div className="flex items-center justify-between mb-6">
          <div>
            <h2 className="text-lg font-semibold text-slate-100">Saved workflows</h2>
            <p className="text-sm text-slate-400 mt-0.5">
              Each workflow is a series of steps that run in order.
            </p>
          </div>
          <button
            onClick={() => navigate("/workflows/builder")}
            className="flex items-center gap-2 px-4 py-2 bg-indigo-600 hover:bg-indigo-500 text-white text-sm font-medium rounded-lg transition-colors"
          >
            <Icon name="add" className="text-base" />
            New workflow
          </button>
        </div>

        {loading ? (
          <div className="text-slate-400 text-sm">Loading...</div>
        ) : workflows.length === 0 ? (
          <div className="text-center py-20 text-slate-500">
            <Icon name="account_tree" className="text-4xl mb-3 block mx-auto opacity-40" />
            <p className="text-sm">No workflows yet.</p>
            <button
              onClick={() => navigate("/workflows/builder")}
              className="mt-4 px-4 py-2 bg-indigo-600 hover:bg-indigo-500 text-white text-sm font-medium rounded-lg transition-colors"
            >
              Build your first workflow
            </button>
          </div>
        ) : (
          <div className="flex flex-col gap-3">
            {workflows.map((wf) => (
              <div
                key={wf.id}
                className="bg-slate-900 border border-slate-800 rounded-xl p-5 flex items-start justify-between gap-4"
              >
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-3 mb-1">
                    <span className="text-slate-100 font-medium truncate">{wf.name}</span>
                    <span
                      className={`px-2 py-0.5 rounded-full text-[11px] font-semibold uppercase tracking-wide ${
                        statusStyles[wf.status] ?? "bg-slate-700 text-slate-300"
                      }`}
                    >
                      {wf.status}
                    </span>
                  </div>
                  <p className="text-xs text-slate-500">
                    {wf.steps.length} step{wf.steps.length !== 1 ? "s" : ""}
                    {" · "}
                    Created {new Date(wf.created_at).toLocaleDateString()}
                  </p>
                </div>
                <div className="flex items-center gap-2 shrink-0">
                  <button
                    onClick={() => navigate(`/workflows/builder/${wf.id}`)}
                    className="flex items-center gap-1.5 px-3 py-1.5 text-slate-300 hover:text-white bg-slate-800 hover:bg-slate-700 text-xs font-medium rounded-lg transition-colors"
                  >
                    <Icon name="edit" className="text-sm" />
                    Edit
                  </button>
                  <button
                    onClick={() => handleRun(wf.id)}
                    disabled={wf.status === "running"}
                    className="flex items-center gap-1.5 px-3 py-1.5 text-green-400 hover:text-white bg-green-500/10 hover:bg-green-500/20 text-xs font-medium rounded-lg transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
                  >
                    <Icon name="play_arrow" className="text-sm" />
                    Run
                  </button>
                  <button
                    onClick={() => handleDelete(wf.id)}
                    className="flex items-center gap-1.5 px-3 py-1.5 text-red-400 hover:text-white bg-red-500/10 hover:bg-red-500/20 text-xs font-medium rounded-lg transition-colors"
                  >
                    <Icon name="delete" className="text-sm" />
                    Delete
                  </button>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
