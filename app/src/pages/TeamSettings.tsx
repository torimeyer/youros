import { useState, useEffect } from "react";
import { Link } from "react-router-dom";
import TopBar from "../components/TopBar";

interface TeamConfig {
  team_repo: string;
  member_id: string;
  display_name: string;
  category_defaults?: Record<string, string>;
  auto_sync?: boolean;
}

interface TeamStatus {
  configured: boolean;
  config?: TeamConfig;
}

const CATEGORIES = ["decisions", "tasks", "notes", "transcripts"] as const;
type Category = (typeof CATEGORIES)[number];

const CATEGORY_LABELS: Record<Category, string> = {
  decisions: "Decisions",
  tasks: "Needles",
  notes: "Notes",
  transcripts: "Transcripts",
};

export default function TeamSettings() {
  const [teamRepo, setTeamRepo] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [memberId, setMemberId] = useState("");
  const [autoSync, setAutoSync] = useState(false);
  const [categoryDefaults, setCategoryDefaults] = useState<
    Record<Category, string>
  >({
    decisions: "shared",
    tasks: "shared",
    notes: "shared",
    transcripts: "private",
  });
  const [saving, setSaving] = useState(false);
  const [success, setSuccess] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    fetch("/api/team")
      .then((r) => r.json())
      .then((d: TeamStatus) => {
        if (d.config) {
          setTeamRepo(d.config.team_repo ?? "");
          setDisplayName(d.config.display_name ?? "");
          setMemberId(d.config.member_id ?? "");
          setAutoSync(d.config.auto_sync ?? false);
          if (d.config.category_defaults) {
            setCategoryDefaults((prev) => ({
              ...prev,
              ...d.config!.category_defaults,
            }));
          }
        }
      })
      .catch(() => {});
  }, []);

  const handleSave = async (e: React.FormEvent) => {
    e.preventDefault();
    setSaving(true);
    setError(null);
    setSuccess(false);
    try {
      const res = await fetch("/api/team/configure", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          team_repo: teamRepo,
          member_id: memberId,
          display_name: displayName,
          category_defaults: categoryDefaults,
          auto_sync: autoSync,
        }),
      });
      if (!res.ok) {
        const text = await res.text();
        throw new Error(text || "Save failed");
      }
      setSuccess(true);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Save failed");
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="min-h-screen bg-white dark:bg-slate-950">
      <TopBar title="Team Settings" />
      <div className="pt-16 sm:pt-20 px-4 sm:px-8 max-w-2xl mx-auto py-8">
        <div className="flex items-center gap-3 mb-8">
          <Link
            to="/team"
            className="text-sm text-slate-600 dark:text-slate-400 hover:text-white transition-colors"
            data-testid="back-to-team"
          >
            ← Back to Team
          </Link>
        </div>

        <form onSubmit={handleSave} className="space-y-6">
          <div>
            <label className="block text-sm font-medium text-slate-700 dark:text-slate-300 mb-1.5">
              Team folder path
            </label>
            <input
              type="text"
              value={teamRepo}
              onChange={(e) => setTeamRepo(e.target.value)}
              placeholder="/path/to/shared/team-folder"
              className="w-full bg-white dark:bg-slate-900 border border-slate-200 dark:border-slate-700 rounded-xl px-4 py-2.5 text-white placeholder-slate-500 focus:outline-none focus:border-blue-500 transition-colors"
              data-testid="field-team-repo"
            />
          </div>

          <div>
            <label className="block text-sm font-medium text-slate-700 dark:text-slate-300 mb-1.5">
              Your display name
            </label>
            <input
              type="text"
              value={displayName}
              onChange={(e) => setDisplayName(e.target.value)}
              placeholder="Alex"
              className="w-full bg-white dark:bg-slate-900 border border-slate-200 dark:border-slate-700 rounded-xl px-4 py-2.5 text-white placeholder-slate-500 focus:outline-none focus:border-blue-500 transition-colors"
              data-testid="field-display-name"
            />
          </div>

          <div>
            <label className="block text-sm font-medium text-slate-700 dark:text-slate-300 mb-1.5">
              Your email / member ID
            </label>
            <input
              type="text"
              value={memberId}
              onChange={(e) => setMemberId(e.target.value)}
              placeholder="alex@example.com"
              className="w-full bg-white dark:bg-slate-900 border border-slate-200 dark:border-slate-700 rounded-xl px-4 py-2.5 text-white placeholder-slate-500 focus:outline-none focus:border-blue-500 transition-colors"
              data-testid="field-member-id"
            />
          </div>

          <div>
            <h3 className="text-sm font-medium text-slate-700 dark:text-slate-300 mb-3">
              Category defaults
            </h3>
            <div className="space-y-2">
              {CATEGORIES.map((cat) => (
                <div
                  key={cat}
                  className="flex items-center justify-between bg-white dark:bg-slate-900 rounded-xl px-4 py-2.5 border border-slate-200 dark:border-slate-800"
                >
                  <span className="text-sm text-slate-700 dark:text-slate-300">
                    {CATEGORY_LABELS[cat]}
                  </span>
                  <select
                    value={categoryDefaults[cat]}
                    onChange={(e) =>
                      setCategoryDefaults((prev) => ({
                        ...prev,
                        [cat]: e.target.value,
                      }))
                    }
                    className="bg-slate-100 dark:bg-slate-800 border border-slate-200 dark:border-slate-700 rounded-lg px-3 py-1 text-sm text-white focus:outline-none focus:border-blue-500"
                    data-testid={`category-default-${cat}`}
                  >
                    <option value="shared">Shared with team</option>
                    <option value="private">Private</option>
                  </select>
                </div>
              ))}
            </div>
          </div>

          <div className="flex items-center justify-between bg-white dark:bg-slate-900 rounded-xl px-4 py-3 border border-slate-200 dark:border-slate-800">
            <div>
              <p className="text-sm font-medium text-slate-700 dark:text-slate-300">
                Auto-sync on decisions
              </p>
              <p className="text-xs text-slate-500 mt-0.5">
                Push to team folder whenever you log a decision
              </p>
            </div>
            <input
              type="checkbox"
              checked={autoSync}
              onChange={(e) => setAutoSync(e.target.checked)}
              className="w-4 h-4 accent-blue-500"
              data-testid="field-auto-sync"
            />
          </div>

          {success && (
            <div
              className="rounded-xl px-4 py-3 bg-green-500/10 border border-green-500/30 text-green-600 dark:text-green-400 text-sm"
              data-testid="success-banner"
            >
              Settings saved.
            </div>
          )}
          {error && (
            <div
              className="rounded-xl px-4 py-3 bg-red-500/10 border border-red-500/30 text-red-600 dark:text-red-400 text-sm"
              data-testid="error-banner"
            >
              {error}
            </div>
          )}

          <button
            type="submit"
            disabled={saving}
            className="w-full py-2.5 bg-blue-600 hover:bg-blue-500 text-white font-medium rounded-xl transition-colors disabled:opacity-50"
            data-testid="save-button"
          >
            {saving ? "Saving..." : "Save"}
          </button>
        </form>
      </div>
    </div>
  );
}
