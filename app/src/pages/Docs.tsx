import { useState, useEffect } from "react";
import TopBar from "../components/TopBar";
import Icon from "../components/Icon";
import { api } from "../lib/api";

interface Doc {
  path: string;
  filename: string;
  title: string;
  status: "draft" | "spec";
  created_at: string;
  promoted_at: string;
  body: string;
}

interface DocsResponse {
  docs: Doc[];
}

type Tab = "all" | "drafts" | "specs";

export default function Docs() {
  const [tab, setTab] = useState<Tab>("all");
  const [docs, setDocs] = useState<Doc[]>([]);
  const [titleInput, setTitleInput] = useState("");
  const [message, setMessage] = useState("");
  const [messageType, setMessageType] = useState<"success" | "error">("success");
  const [loading, setLoading] = useState(false);

  const fetchDocs = async () => {
    try {
      const data = await api.get<DocsResponse>("/docs");
      setDocs(data.docs || []);
    } catch {
      // API not available, keep empty state
    }
  };

  useEffect(() => {
    fetchDocs();
  }, []);

  const showMessage = (text: string, type: "success" | "error" = "success") => {
    setMessage(text);
    setMessageType(type);
    setTimeout(() => setMessage(""), 4000);
  };

  const handleCreateDraft = async () => {
    const title = titleInput.trim();
    if (!title) return;
    setLoading(true);
    try {
      await api.post("/docs/draft", { title });
      setTitleInput("");
      await fetchDocs();
      showMessage("Draft created.");
    } catch {
      showMessage("Could not create draft. Try again.", "error");
    } finally {
      setLoading(false);
    }
  };

  const handlePromote = async (path: string) => {
    setLoading(true);
    try {
      const res = await api.post<{ result: string }>("/docs/promote", { path });
      await fetchDocs();
      showMessage(`Promoted to spec: ${res.result}`);
    } catch (err) {
      const msg = err instanceof Error ? err.message : "Unknown error";
      if (msg.includes("checkbox")) {
        showMessage("This draft needs at least one checklist item (acceptance criteria) before it can be promoted.", "error");
      } else {
        showMessage("Could not promote draft. Make sure it has acceptance criteria.", "error");
      }
    } finally {
      setLoading(false);
    }
  };

  const handleDecompose = async (path: string) => {
    setLoading(true);
    try {
      const res = await api.post<{ result: string }>("/docs/decompose", { path });
      await fetchDocs();
      showMessage(`Tasks created: ${res.result}`);
    } catch {
      showMessage("Could not break spec into tasks. Try again.", "error");
    } finally {
      setLoading(false);
    }
  };

  const drafts = docs.filter((d) => d.status === "draft");
  const specs = docs.filter((d) => d.status === "spec");
  const filtered =
    tab === "drafts" ? drafts : tab === "specs" ? specs : docs;

  return (
    <>
      <TopBar title="Docs" />
      <div data-tour="docs" className="pt-20 p-8 max-w-6xl mx-auto">
        {/* Header */}
        <div className="flex items-center justify-between mb-6">
          <div className="flex items-center gap-3">
            <h1 className="text-2xl font-bold text-white">Docs</h1>
            <span className="bg-blue-500 text-white text-xs rounded-full px-2">
              {docs.length}
            </span>
          </div>
        </div>

        {/* Workflow summary */}
        <div className="bg-slate-900/40 border border-slate-800 rounded-xl p-4 mb-6">
          <div className="flex items-center gap-6 text-sm text-slate-400">
            <div className="flex items-center gap-2">
              <Icon name="edit_note" className="text-yellow-400" />
              <span><strong className="text-white">{drafts.length}</strong> {drafts.length === 1 ? "draft" : "drafts"}</span>
            </div>
            <Icon name="arrow_forward" className="text-slate-600" />
            <div className="flex items-center gap-2">
              <Icon name="verified" className="text-green-400" />
              <span><strong className="text-white">{specs.length}</strong> {specs.length === 1 ? "spec" : "specs"}</span>
            </div>
            <Icon name="arrow_forward" className="text-slate-600" />
            <div className="flex items-center gap-2">
              <Icon name="checklist" className="text-blue-400" />
              <span>Tasks</span>
            </div>
          </div>
        </div>

        {/* Tabs */}
        <div className="flex gap-1 mb-6 bg-slate-900/60 rounded-lg p-1 w-fit">
          {(["all", "drafts", "specs"] as Tab[]).map((t) => (
            <button
              key={t}
              onClick={() => setTab(t)}
              className={`px-4 py-2 rounded-md text-sm font-medium transition-colors ${
                tab === t
                  ? "bg-blue-500 text-white"
                  : "text-slate-400 hover:text-white"
              }`}
            >
              {t === "all" ? "All" : t === "drafts" ? "Drafts" : "Specs"}
              {t === "drafts" && drafts.length > 0 && (
                <span className="ml-2 text-xs opacity-80">{drafts.length}</span>
              )}
              {t === "specs" && specs.length > 0 && (
                <span className="ml-2 text-xs opacity-80">{specs.length}</span>
              )}
            </button>
          ))}
        </div>

        {/* Create new draft */}
        <div className="flex gap-3 mb-8">
          <input
            type="text"
            placeholder="Name your plan..."
            value={titleInput}
            onChange={(e) => setTitleInput(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") handleCreateDraft();
            }}
            className="flex-1 bg-slate-900/40 border border-slate-800 rounded-lg px-4 py-2 text-white placeholder-slate-500 focus:outline-none focus:border-blue-500"
          />
          <button
            onClick={handleCreateDraft}
            disabled={loading || !titleInput.trim()}
            className="bg-blue-500 hover:bg-blue-600 disabled:opacity-50 text-white rounded-lg px-4 py-2 transition-colors"
          >
            New Draft
          </button>
        </div>

        {/* Status message */}
        {message && (
          <div
            className={`text-sm rounded-lg px-4 py-2 mb-4 ${
              messageType === "success"
                ? "bg-green-500/20 text-green-400"
                : "bg-red-500/20 text-red-400"
            }`}
          >
            {message}
          </div>
        )}

        {/* Document list */}
        {filtered.length === 0 ? (
          <div className="text-center py-16 text-slate-500">
            <Icon name="description" className="text-4xl mb-3 block" />
            <p>No documents yet. Create a draft to start planning.</p>
          </div>
        ) : (
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            {filtered.map((doc) => (
              <div
                key={doc.path}
                className="bg-slate-900/40 border border-slate-800 rounded-xl p-5"
              >
                <div className="flex items-center justify-between mb-3">
                  <div className="flex items-center gap-2">
                    <span
                      className={`w-2 h-2 rounded-full ${
                        doc.status === "draft" ? "bg-yellow-500" : "bg-green-500"
                      }`}
                    />
                    <span className="text-slate-500 text-sm">
                      {doc.status === "draft" ? "draft" : "spec"}
                    </span>
                  </div>
                  {doc.created_at && (
                    <span className="text-slate-600 text-xs">
                      {new Date(doc.created_at).toLocaleDateString()}
                    </span>
                  )}
                </div>

                <p className="text-white text-lg font-medium mb-2">
                  {doc.title}
                </p>

                {doc.body && (
                  <p className="text-slate-400 text-sm mb-3 line-clamp-2">
                    {doc.body}
                  </p>
                )}

                <div className="flex items-center gap-2 text-xs text-slate-600 mb-3">
                  <Icon name="folder" className="text-sm" />
                  <span className="font-mono">{doc.path}</span>
                </div>

                <div className="flex gap-2">
                  {doc.status === "draft" && (
                    <button
                      onClick={() => handlePromote(doc.path)}
                      disabled={loading}
                      className="bg-green-500/20 text-green-400 text-xs font-bold px-3 py-1 rounded hover:bg-green-500/30 transition-colors disabled:opacity-50"
                    >
                      Promote to Spec
                    </button>
                  )}
                  {doc.status === "spec" && (
                    <button
                      onClick={() => handleDecompose(doc.path)}
                      disabled={loading}
                      className="bg-blue-500/20 text-blue-400 text-xs font-bold px-3 py-1 rounded hover:bg-blue-500/30 transition-colors disabled:opacity-50"
                    >
                      Break into Tasks
                    </button>
                  )}
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </>
  );
}
