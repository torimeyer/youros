import { useEffect, useState } from "react";
import Icon from "./Icon";
import { api } from "../lib/api";

interface CreatedTask {
  id: string | null;
  title: string;
  description: string;
  priority: string;
  order: number;
}

interface AnswerResponse {
  status: "created" | "needs_clarification";
  question?: string;
  tasks?: CreatedTask[];
  task_id?: string;
  straw?: string;
  result?: string;
}

interface Props {
  straw: string;
  question: string;
  /** Called when the user closes the prompt without finishing. */
  onClose: () => void;
  /** Called when tasks are finally created. Receives the count. */
  onCreated: (count: number) => void;
}

const MAX_ROUNDS = 3;

export default function ClarificationPrompt({
  straw,
  question: initialQuestion,
  onClose,
  onCreated,
}: Props) {
  const [question, setQuestion] = useState(initialQuestion);
  const [answer, setAnswer] = useState("");
  const [round, setRound] = useState(1);
  const [sending, setSending] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    setQuestion(initialQuestion);
  }, [initialQuestion]);

  const handleSend = async () => {
    if (!answer.trim() || sending) return;
    setSending(true);
    setError("");
    try {
      const resp = await api.post<AnswerResponse>("/ideas/answer", {
        straw,
        answer: answer.trim(),
      });
      if (resp.status === "created") {
        const count = resp.tasks ? resp.tasks.length : 0;
        onCreated(count);
        return;
      }
      // Still needs clarification. Swap the question and let the user try again.
      if (round >= MAX_ROUNDS) {
        // Give up asking and let the parent know we could not finish.
        setError(
          "I still need more info to break this down. Try again later with a bit more detail."
        );
        setSending(false);
        return;
      }
      setQuestion(resp.question || "Could you share a little more?");
      setAnswer("");
      setRound(round + 1);
    } catch {
      setError("Something went wrong. Try again.");
    } finally {
      setSending(false);
    }
  };

  const handleKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  };

  return (
    <div
      role="dialog"
      aria-label="Quick question before breaking down this idea"
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4"
    >
      <div className="bg-slate-900 border border-slate-700 rounded-xl p-6 max-w-lg w-full shadow-2xl">
        <div className="flex items-start justify-between mb-4">
          <div className="flex items-center gap-2">
            <Icon name="help_outline" className="text-pink-400 text-xl" />
            <span className="text-white font-semibold">Quick question</span>
          </div>
          <button
            onClick={onClose}
            className="text-slate-500 hover:text-white transition-colors"
            aria-label="Close"
          >
            <Icon name="close" className="text-lg" />
          </button>
        </div>

        <p className="text-slate-300 text-sm mb-2">
          I need a little more info before I can break this down.
        </p>
        <p className="text-white text-base mb-4">{question}</p>

        <input
          type="text"
          value={answer}
          onChange={(e) => setAnswer(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder="Type your answer"
          autoFocus
          className="w-full bg-slate-800 border border-slate-700 rounded-lg px-4 py-2 text-white placeholder-slate-500 focus:outline-none focus:border-pink-500 mb-3"
        />

        {error && (
          <div className="text-red-400 text-sm mb-3">{error}</div>
        )}

        <div className="flex items-center justify-between">
          <span className="text-slate-500 text-xs">
            Round {round} of {MAX_ROUNDS}
          </span>
          <div className="flex gap-2">
            <button
              onClick={onClose}
              className="px-4 py-2 text-slate-400 hover:text-white text-sm rounded-lg transition-colors"
            >
              Cancel
            </button>
            <button
              onClick={handleSend}
              disabled={!answer.trim() || sending}
              className="bg-pink-500 hover:bg-pink-600 disabled:bg-pink-500/40 disabled:cursor-not-allowed text-white rounded-lg px-4 py-2 text-sm transition-colors"
            >
              {sending ? "Sending..." : "Send answer"}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
