import { useState } from "react";
import { api } from "../lib/api";

interface LastChange {
  commit_sha: string;
  message: string;
  files_changed: string[];
  diff: string;
}

interface Props {
  agentName: string;
}

type Phase = "idle" | "loading" | "confirm" | "applying" | "done" | "error";

export default function UndoAgentChange({ agentName }: Props) {
  const [phase, setPhase] = useState<Phase>("idle");
  const [change, setChange] = useState<LastChange | null>(null);
  const [errorMsg, setErrorMsg] = useState("");

  async function handleClick() {
    setPhase("loading");
    setErrorMsg("");
    try {
      const data = await api.get<LastChange>(`/agents/${encodeURIComponent(agentName)}/last-change`);
      setChange(data);
      setPhase("confirm");
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "Could not find this agent's last change.";
      setErrorMsg(msg);
      setPhase("error");
    }
  }

  async function handleConfirm() {
    if (!change) return;
    setPhase("applying");
    try {
      await api.post(`/agents/${encodeURIComponent(agentName)}/undo`, {
        commit_sha: change.commit_sha,
        confirm: true,
      });
      setPhase("done");
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "Something went wrong. The change was not undone.";
      setErrorMsg(msg);
      setPhase("error");
    }
  }

  function handleClose() {
    setPhase("idle");
    setChange(null);
    setErrorMsg("");
  }

  const showModal = phase === "confirm" || phase === "applying" || phase === "done" || phase === "error";

  return (
    <>
      <button
        data-testid="undo-agent-change-btn"
        onClick={handleClick}
        disabled={phase === "loading"}
        className="text-xs text-slate-600 dark:text-slate-400 hover:text-amber-600 dark:hover:text-amber-400 border border-slate-200 dark:border-slate-700 hover:border-amber-500 rounded px-2.5 py-1 transition-colors disabled:opacity-50"
      >
        {phase === "loading" ? "Checking..." : "Undo last change"}
      </button>

      {showModal && (
        <div
          role="dialog"
          aria-modal="true"
          aria-label="Undo agent change"
          data-testid="undo-agent-change-modal"
          onClick={(e) => { if (e.target === e.currentTarget) handleClose(); }}
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4"
        >
          <div className="bg-white dark:bg-slate-900 border border-slate-200 dark:border-slate-700 rounded-xl w-full max-w-2xl shadow-2xl flex flex-col max-h-[80vh]">
            <div className="flex items-center justify-between p-5 border-b border-slate-200 dark:border-slate-800">
              <div>
                <h2 className="text-white font-semibold text-base">Undo this agent's last change</h2>
                {change && (
                  <p className="text-slate-600 dark:text-slate-400 text-xs mt-0.5 font-mono">{change.message}</p>
                )}
              </div>
              <button
                data-testid="undo-agent-change-close"
                onClick={handleClose}
                className="text-slate-500 hover:text-white transition-colors ml-4 shrink-0"
                aria-label="Close"
              >
                ✕
              </button>
            </div>

            {phase === "done" ? (
              <div className="p-5 text-center">
                <p data-testid="undo-success-msg" className="text-green-600 dark:text-green-400 font-medium">
                  Done. The change has been undone.
                </p>
                <p className="text-slate-500 text-xs mt-1">A new "undo" commit was added to git.</p>
                <button
                  onClick={handleClose}
                  className="mt-4 px-4 py-2 text-sm bg-slate-100 dark:bg-slate-800 hover:bg-slate-200 dark:hover:bg-slate-700 text-white rounded-lg transition-colors"
                >
                  Close
                </button>
              </div>
            ) : phase === "error" ? (
              <div className="p-5 text-center">
                <p data-testid="undo-error-msg" className="text-red-600 dark:text-red-400 text-sm">{errorMsg}</p>
                <button
                  onClick={handleClose}
                  className="mt-4 px-4 py-2 text-sm bg-slate-100 dark:bg-slate-800 hover:bg-slate-200 dark:hover:bg-slate-700 text-white rounded-lg transition-colors"
                >
                  Close
                </button>
              </div>
            ) : (
              <>
                {change && (
                  <>
                    <div className="px-5 pt-4 pb-2">
                      <p className="text-xs text-slate-600 dark:text-slate-400 mb-1">
                        Files affected: <span className="text-slate-700 dark:text-slate-300">{change.files_changed.join(", ") || "none"}</span>
                      </p>
                    </div>
                    <div
                      data-testid="undo-diff-view"
                      className="flex-1 overflow-auto mx-5 mb-4 bg-white dark:bg-slate-950 border border-slate-200 dark:border-slate-800 rounded-lg p-3 font-mono text-xs text-slate-700 dark:text-slate-300 whitespace-pre"
                    >
                      {change.diff || "(no diff)"}
                    </div>
                  </>
                )}

                <div className="flex items-center justify-end gap-2 p-4 border-t border-slate-200 dark:border-slate-800">
                  <button
                    data-testid="undo-agent-change-cancel"
                    onClick={handleClose}
                    disabled={phase === "applying"}
                    className="px-4 py-2 text-slate-600 dark:text-slate-400 hover:text-white text-sm rounded-lg transition-colors disabled:opacity-50"
                  >
                    Cancel
                  </button>
                  <button
                    data-testid="undo-agent-change-confirm"
                    onClick={handleConfirm}
                    disabled={phase === "applying"}
                    className="px-4 py-2 text-sm bg-amber-600 hover:bg-amber-700 text-white rounded-lg transition-colors disabled:opacity-50"
                  >
                    {phase === "applying" ? "Undoing..." : "Yes, undo this change"}
                  </button>
                </div>
              </>
            )}
          </div>
        </div>
      )}
    </>
  );
}
