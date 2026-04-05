import { useState, useEffect } from "react";
import TopBar from "../components/TopBar";
import Icon from "../components/Icon";
import { api } from "../lib/api";

interface IdeasResponse {
  clusters: { name: string; count: number; items: string[] }[];
  unclustered: string[];
}

interface ConvertedItem {
  straw: string;
  task_id: string;
  converted_at: string;
}

interface ConvertedResponse {
  converted: ConvertedItem[];
}

type Tab = "active" | "converted";

export default function Ideas() {
  const [tab, setTab] = useState<Tab>("active");
  const [sortNewest, setSortNewest] = useState(true);
  const [input, setInput] = useState("");
  const [hayEntries, setHayEntries] = useState<string[]>([]);
  const [clusters, setClusters] = useState<IdeasResponse["clusters"]>([]);
  const [convertedItems, setConvertedItems] = useState<ConvertedItem[]>([]);
  const [successMessage, setSuccessMessage] = useState("");

  const fetchActive = async () => {
    try {
      const data = await api.get<IdeasResponse>("/ideas");
      const allItems: string[] = [];
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

  const fetchData = async () => {
    await Promise.all([fetchActive(), fetchConverted()]);
  };

  useEffect(() => {
    fetchData();
  }, []);

  const handleSend = async () => {
    if (!input.trim()) return;
    try {
      await api.post("/ideas", { thought: input });
      setInput("");
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
      await api.post("/ideas/convert", { straw });
      await fetchData();
      setSuccessMessage("Idea converted to a task!");
      setTimeout(() => setSuccessMessage(""), 3000);
    } catch {
      setSuccessMessage("Could not convert idea. Try again.");
      setTimeout(() => setSuccessMessage(""), 3000);
    }
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

  const displayEntries = sortNewest ? [...hayEntries] : [...hayEntries].reverse();
  const displayConverted = sortNewest
    ? [...convertedItems]
    : [...convertedItems].reverse();

  return (
    <>
      <TopBar title="Ideas" />
      <div data-tour="ideas" className="pt-16 p-8 max-w-6xl mx-auto">
        {/* Header */}
        <div className="flex items-center justify-between mb-6">
          <div className="flex items-center gap-3">
            <h1 className="text-2xl font-bold text-white">Ideas</h1>
            <span className="bg-pink-500 text-white text-xs rounded-full px-2">
              {hayEntries.length}
            </span>
          </div>
          <button
            onClick={() => setSortNewest(!sortNewest)}
            className="text-sm text-slate-400 hover:text-white transition-colors"
          >
            {sortNewest ? "Newest First" : "Oldest First"}
          </button>
        </div>

        {/* Tabs */}
        <div className="flex gap-1 mb-6 bg-slate-900/60 rounded-lg p-1 w-fit">
          <button
            onClick={() => setTab("active")}
            className={`px-4 py-2 rounded-md text-sm font-medium transition-colors ${
              tab === "active"
                ? "bg-pink-500 text-white"
                : "text-slate-400 hover:text-white"
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
            className={`px-4 py-2 rounded-md text-sm font-medium transition-colors ${
              tab === "converted"
                ? "bg-pink-500 text-white"
                : "text-slate-400 hover:text-white"
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

        {/* Quick capture (only on Active tab) */}
        {tab === "active" && (
          <div className="flex gap-3 mb-8">
            <input
              type="text"
              placeholder="What's on your mind?"
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
              Send
            </button>
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
                      key={item}
                      className="bg-slate-800 text-slate-300 text-xs rounded-full px-3 py-1"
                    >
                      {item}
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
                <p>No active ideas yet. Type one above to get started.</p>
              </div>
            ) : (
              <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                {displayEntries.map((entry, idx) => (
                  <div
                    key={`${entry}-${idx}`}
                    className="bg-slate-900/40 border border-slate-800 rounded-xl p-5"
                  >
                    <div className="flex items-center justify-between mb-3">
                      <div className="flex items-center gap-2">
                        <span className="w-2 h-2 rounded-full bg-pink-500" />
                        <span className="text-slate-500 text-sm">idea</span>
                      </div>
                      <button
                        onClick={() => handleDelete(entry)}
                        className="text-slate-600 hover:text-red-400 transition-colors"
                        title="Remove idea"
                      >
                        <Icon name="close" className="text-lg" />
                      </button>
                    </div>
                    <p className="text-white text-lg font-medium mb-3">{entry}</p>
                    <div className="flex gap-2">
                      <button
                        onClick={() => handleConvert(entry)}
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
    </>
  );
}
