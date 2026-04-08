import { useState, useEffect, useRef } from "react";
import TopBar from "../components/TopBar";
import Icon from "../components/Icon";
import ClarificationPrompt from "../components/ClarificationPrompt";
import TemplateManager from "../components/TemplateManager";
import { api } from "../lib/api";

interface IdeaItem {
  straw: string;
  staleness: "stale" | "very_stale" | null;
  source: string | null;
}

interface IdeasResponse {
  clusters: { name: string; count: number; items: IdeaItem[] }[];
  unclustered: IdeaItem[];
}

interface ConvertedItem {
  straw: string;
  task_id: string;
  converted_at: string;
}

interface ConvertedResponse {
  converted: ConvertedItem[];
}

interface ConvertResponse {
  status: "created" | "needs_clarification";
  question?: string;
  tasks?: Array<{
    id: string | null;
    title: string;
    description: string;
    priority: string;
    order: number;
  }>;
  straw?: string;
}

interface Template {
  id: string;
  name: string;
  description: string;
  icon: string;
  prompt_template: string;
  breakdown_hint: string;
  builtin: boolean;
}

type Tab = "active" | "converted";
type SortMode = "newest" | "oldest" | "stalest";

export default function Ideas() {
  const [tab, setTab] = useState<Tab>("active");
  const [sortMode, setSortMode] = useState<SortMode>("newest");
  const [input, setInput] = useState("");
  const [hayEntries, setHayEntries] = useState<IdeaItem[]>([]);
  const [clusters, setClusters] = useState<IdeasResponse["clusters"]>([]);
  const [convertedItems, setConvertedItems] = useState<ConvertedItem[]>([]);
  const [successMessage, setSuccessMessage] = useState("");
  const [clarification, setClarification] = useState<{
    straw: string;
    question: string;
  } | null>(null);
  const [templates, setTemplates] = useState<Template[]>([]);
  const [selectedTemplateId, setSelectedTemplateId] = useState<string | null>(null);
  const [showTemplateManager, setShowTemplateManager] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

  const fetchActive = async () => {
    try {
      const data = await api.get<IdeasResponse>("/ideas");
      const allItems: IdeaItem[] = [];
      if (data.clusters) {
        setClusters(data.clusters);
        for (const cluster of data.clusters) {
          allItems.push(...cluster.items);
        }
      }
      if (data.unclustered) {
        allItems.push(...data.unclustered);
      }
      setHayEntries(allItems);
    } catch {
      // API not available, keep empty state
    }
  };

  const fetchConverted = async () => {
    try {
      const data = await api.get<ConvertedResponse>("/ideas?status=converted");
      setConvertedItems(data.converted || []);
    } catch {
      // API not available, keep empty state
    }
  };

  const fetchTemplates = async () => {
    try {
      const data = await api.get<{ templates: Template[] }>("/ideas/templates");
      setTemplates(data.templates || []);
    } catch {
      // If templates endpoint is unavailable, leave empty
    }
  };

  const fetchData = async () => {
    await Promise.all([fetchActive(), fetchConverted()]);
  };

  useEffect(() => {
    fetchData();
    fetchTemplates();
  }, []);

  const handleTemplateClick = (template: Template) => {
    if (selectedTemplateId === template.id) {
      setSelectedTemplateId(null);
      return;
    }
    setSelectedTemplateId(template.id);
    setInput(template.prompt_template);
    // Focus and select the first placeholder so user can immediately type
    setTimeout(() => {
      if (inputRef.current) {
        const text = template.prompt_template;
        const start = text.indexOf("[");
        const end = text.indexOf("]") + 1;
        inputRef.current.focus();
        if (start >= 0 && end > start) {
          inputRef.current.setSelectionRange(start, end);
        }
      }
    }, 50);
  };

  const handleSend = async () => {
    if (!input.trim()) return;
    try {
      await api.post("/ideas", { thought: input, template_id: selectedTemplateId });
      setInput("");
      setSelectedTemplateId(null);
      await fetchActive();
    } catch {
      // handle error silently
    }
  };

  const handleCompile = async () => {
    try {
      await api.post("/ideas/compile");
      await fetchData();
      setSuccessMessage("Ideas compiled into tasks!");
      setTimeout(() => setSuccessMessage(""), 3000);
    } catch {
      setSuccessMessage("Compile failed. Try again.");
      setTimeout(() => setSuccessMessage(""), 3000);
    }
  };

  const handleConvert = async (straw: string) => {
    try {
      const resp = await api.post<ConvertResponse>("/ideas/convert", { straw });
      if (resp.status === "needs_clarification") {
        setClarification({
          straw,
          question: resp.question || "Tell me a little more about this idea.",
        });
        return;
      }
      const count = resp.tasks ? resp.tasks.length : 0;
      await fetchData();
      setSuccessMessage(
        count === 1 ? "Created 1 task." : `Created ${count} tasks.`
      );
      setTimeout(() => setSuccessMessage(""), 3000);
    } catch {
      setSuccessMessage("Could not break this idea into tasks. Try again.");
      setTimeout(() => setSuccessMessage(""), 3000);
    }
  };

  const handleClarificationCreated = async (count: number) => {
    setClarification(null);
    await fetchData();
    setSuccessMessage(
      count === 1 ? "Created 1 task." : `Created ${count} tasks.`
    );
    setTimeout(() => setSuccessMessage(""), 3000);
  };

  const handleDelete = async (straw: string) => {
    try {
      await api.delete(`/ideas/${encodeURIComponent(straw)}`);
      await fetchData();
      setSuccessMessage("Idea removed.");
      setTimeout(() => setSuccessMessage(""), 3000);
    } catch {
      setSuccessMessage("Could not remove idea. Try again.");
      setTimeout(() => setSuccessMessage(""), 3000);
    }
  };

  const sortEntries = (entries: IdeaItem[]): IdeaItem[] => {
    if (sortMode === "oldest") return [...entries].reverse();
    if (sortMode === "stalest") {
      return [...entries].sort((a, b) => {
        const rank = (s: IdeaItem["staleness"]) =>
          s === "very_stale" ? 0 : s === "stale" ? 1 : 2;
        return rank(a.staleness) - rank(b.staleness);
      });
    }
    return [...entries]; // newest: already in newest-first order from server
  };

  const displayEntries = sortEntries(hayEntries);
  const displayConverted = sortMode === "oldest"
    ? [...convertedItems].reverse()
    : [...convertedItems];

  const cycleSortMode = () => {
    setSortMode((prev) => {
      if (prev === "newest") return "oldest";
      if (prev === "oldest") return "stalest";
      return "newest";
    });
  };

  const sortLabel = sortMode === "newest"
    ? "Newest first"
    : sortMode === "oldest"
    ? "Oldest first"
    : "Stalest first";

  return (
    <>
      <TopBar title="Ideas" />
      <div data-tour="ideas" className="pt-20 p-8 max-w-6xl mx-auto">
        {/* Header */}
        <div className="flex items-center justify-between mb-6">
          <div className="flex items-center gap-3">
            <h1 className="text-2xl font-bold text-white">Ideas</h1>
            <span className="bg-pink-500 text-white text-xs rounded-full px-2">
              {hayEntries.length}
            </span>
          </div>
          <button
            onClick={cycleSortMode}
            className="text-sm text-slate-400 hover:text-white transition-colors"
          >
            {sortLabel}
          </button>
        </div>

        {/* Tabs */}
        <div className="flex gap-2 mb-6">
          <button
            onClick={() => setTab("active")}
            className={`px-4 py-2 rounded-lg text-sm font-medium transition-colors ${
              tab === "active"
                ? "bg-pink-500 text-white"
                : "bg-slate-800/50 text-slate-400 hover:text-white border border-slate-700"
            }`}
          >
            Active
            {hayEntries.length > 0 && (
              <span className="ml-2 text-xs opacity-80">
                {hayEntries.length}
              </span>
            )}
          </button>
          <button
            onClick={() => setTab("converted")}
            className={`px-4 py-2 rounded-lg text-sm font-medium transition-colors ${
              tab === "converted"
                ? "bg-pink-500 text-white"
                : "bg-slate-800/50 text-slate-400 hover:text-white border border-slate-700"
            }`}
          >
            Converted
            {convertedItems.length > 0 && (
              <span className="ml-2 text-xs opacity-80">
                {convertedItems.length}
              </span>
            )}
          </button>
        </div>

        {/* Template picker + Quick capture (only on Active tab) */}
        {tab === "active" && (
          <div className="mb-8">
            {/* Template cards */}
            {templates.length > 0 && (
              <div className="mb-3">
                <div className="flex flex-wrap gap-2 mb-2">
                  {templates.map((tpl) => (
                    <button
                      key={tpl.id}
                      onClick={() => handleTemplateClick(tpl)}
                      title={tpl.description}
                      className={`flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium border transition-colors ${
                        selectedTemplateId === tpl.id
                          ? "bg-pink-500 text-white border-pink-500"
                          : "bg-slate-800/50 text-slate-400 hover:text-white border-slate-700 hover:border-slate-500"
                      }`}
                    >
                      <Icon name={tpl.icon} className="text-sm" />
                      {tpl.name}
                    </button>
                  ))}
                </div>
                <button
                  onClick={() => setShowTemplateManager(true)}
                  className="text-xs text-slate-500 hover:text-pink-400 transition-colors"
                >
                  Manage templates
                </button>
              </div>
            )}

            {/* Text input */}
            <div className="flex gap-3">
              <input
                ref={inputRef}
                type="text"
                placeholder="Quick dump an idea (or just mention it in chat)"
                value={input}
                onChange={(e) => setInput(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter") handleSend();
                }}
                className="flex-1 bg-slate-900/40 border border-slate-800 rounded-lg px-4 py-2 text-white placeholder-slate-500 focus:outline-none focus:border-pink-500"
              />
              <button
                onClick={handleSend}
                className="bg-pink-500 hover:bg-pink-600 text-white rounded-lg px-4 py-2 transition-colors"
              >
                Save
              </button>
            </div>
          </div>
        )}

        {/* Success message */}
        {successMessage && (
          <div className="bg-green-500/20 text-green-400 text-sm rounded-lg px-4 py-2 mb-4">
            {successMessage}
          </div>
        )}

        {/* Active tab content */}
        {tab === "active" && (
          <>
            {/* Suggested Compilations */}
            {clusters.length > 0 && (
              <div className="bg-slate-900/40 border border-slate-800 rounded-xl p-4 mb-8">
                <div className="flex items-center gap-2 mb-2">
                  <Icon name="auto_awesome" className="text-pink-400 text-lg" />
                  <span className="text-white font-semibold">
                    Suggested Compilations
                  </span>
                </div>
                <p className="text-slate-400 text-sm mb-3">
                  I've grouped {clusters.reduce((sum, c) => sum + c.items.length, 0)} related ideas about{" "}
                  {clusters.map((c) => c.name.toLowerCase()).join(", ")}.
                </p>
                <div className="flex flex-wrap gap-2 mb-4">
                  {clusters.flatMap((c) => c.items).map((item) => (
                    <span
                      key={item.straw}
                      className="bg-slate-800 text-slate-300 text-xs rounded-full px-3 py-1"
                    >
                      {item.straw}
                    </span>
                  ))}
                </div>
                <button
                  onClick={handleCompile}
                  className="bg-pink-500 text-white rounded-lg px-4 py-2 hover:bg-pink-600 transition-colors"
                >
                  Create Tasks
                </button>
              </div>
            )}

            {/* Active hay entries */}
            {displayEntries.length === 0 ? (
              <div className="text-center py-16 text-slate-500">
                <Icon name="lightbulb" className="text-4xl mb-3 block" />
                <p>No active ideas yet. Pick a template above or type one in.</p>
              </div>
            ) : (
              <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                {displayEntries.map((entry, idx) => (
                  <div
                    key={`${entry.straw}-${idx}`}
                    className="bg-slate-900/40 border border-slate-800 rounded-xl p-5"
                  >
                    <div className="flex items-center justify-between mb-3">
                      <div className="flex items-center gap-2 flex-wrap">
                        <span className="w-2 h-2 rounded-full bg-pink-500 flex-shrink-0" />
                        <span className="text-slate-500 text-sm">idea</span>
                        {entry.source === "chat" && (
                          <span className="bg-blue-500/20 text-blue-400 text-xs px-2 py-0.5 rounded-full">
                            From chat
                          </span>
                        )}
                        {entry.staleness === "very_stale" && (
                          <span className="bg-red-500/20 text-red-400 text-xs px-2 py-0.5 rounded-full">
                            Very stale
                          </span>
                        )}
                        {entry.staleness === "stale" && (
                          <span className="bg-yellow-500/20 text-yellow-400 text-xs px-2 py-0.5 rounded-full">
                            Getting stale
                          </span>
                        )}
                      </div>
                      <button
                        onClick={() => handleDelete(entry.straw)}
                        className="text-slate-600 hover:text-red-400 transition-colors"
                        title="Remove idea"
                      >
                        <Icon name="close" className="text-lg" />
                      </button>
                    </div>
                    <p className="text-white text-lg font-medium mb-3">{entry.straw}</p>
                    <div className="flex gap-2">
                      <button
                        onClick={() => handleConvert(entry.straw)}
                        className="bg-pink-500/20 text-pink-500 text-xs font-bold px-3 py-1 rounded hover:bg-pink-500/30 transition-colors"
                      >
                        Break into tasks
                      </button>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </>
        )}

        {/* Converted tab content */}
        {tab === "converted" && (
          <>
            {displayConverted.length === 0 ? (
              <div className="text-center py-16 text-slate-500">
                <Icon name="check_circle" className="text-4xl mb-3 block" />
                <p>No converted ideas yet. Use "Break into tasks" on an active idea to move it here.</p>
              </div>
            ) : (
              <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                {displayConverted.map((item, idx) => (
                  <div
                    key={`${item.straw}-${idx}`}
                    className="bg-slate-900/40 border border-slate-800 rounded-xl p-5"
                  >
                    <div className="flex items-center justify-between mb-3">
                      <div className="flex items-center gap-2">
                        <span className="w-2 h-2 rounded-full bg-green-500" />
                        <span className="text-slate-500 text-sm">converted to task</span>
                      </div>
                      {item.task_id && (
                        <span className="text-slate-500 text-xs font-mono">
                          {item.task_id}
                        </span>
                      )}
                    </div>
                    <p className="text-white text-lg font-medium mb-3">{item.straw}</p>
                    {item.converted_at && (
                      <p className="text-slate-500 text-xs">
                        Converted {new Date(item.converted_at).toLocaleDateString()}
                      </p>
                    )}
                  </div>
                ))}
              </div>
            )}
          </>
        )}
      </div>

      {clarification && (
        <ClarificationPrompt
          straw={clarification.straw}
          question={clarification.question}
          onClose={() => setClarification(null)}
          onCreated={handleClarificationCreated}
        />
      )}

      {showTemplateManager && (
        <TemplateManager
          onClose={() => {
            setShowTemplateManager(false);
            fetchTemplates();
          }}
        />
      )}
    </>
  );
}
