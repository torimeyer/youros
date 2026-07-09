import { useState, useEffect, useCallback } from 'react';
import Icon from '../components/Icon';
import PageShell from '../components/PageShell';
import { Card, EmptyState, ErrorBanner, LoadingState } from '../components/ui';
import { api } from '../lib/api';
import { reportError } from '../lib/reportError';

// One task row inside a theme bucket.
interface RollupTaskRow {
  id: string;
  title: string;
  risk: string;
}

// One project row inside a theme bucket.
interface RollupProjectRow {
  name: string;
  risk: string;
  last_modified: string | null;
}

// A theme bucket. name is null for the catch-all "No theme yet" bucket.
interface ThemeBucket {
  name: string | null;
  projects: RollupProjectRow[];
  tasks: RollupTaskRow[];
  project_count: number;
  task_count: number;
  risk: string;
}

interface JiraTicket {
  key: string;
  summary: string;
  status: string;
  priority: string;
  type: string;
  updated: string;
  url: string;
}

interface RollupResponse {
  themes: ThemeBucket[];
  jira: { connected: boolean; tickets: JiraTicket[] };
}

// The org's configurable lists from GET /enterprise/lists. Themes live
// under "pillars"; job_roles belongs to other pages and is never touched here.
interface OrgLists {
  job_roles: string[];
  pillars: string[];
}

// Plain-words risk badge colors. "none" renders nothing.
const riskStyles: Record<string, string> = {
  overdue: 'bg-red-500/15 text-red-600 dark:text-red-400',
  blocked: 'bg-amber-500/15 text-amber-600 dark:text-amber-400',
  quiet: 'bg-slate-500/15 text-slate-500 dark:text-slate-400',
};

// What each flag means, in everyday words. The API keeps the short
// internal names; only the label shown to the user changes here.
const riskLabels: Record<string, string> = {
  overdue: 'past due',
  blocked: 'waiting on other work',
  quiet: 'no activity for a week',
};

function RiskBadge({ risk }: { risk: string }) {
  if (!risk || risk === 'none') return null;
  return (
    <span
      className={`px-2 py-0.5 rounded-full text-xs font-medium ${riskStyles[risk] ?? riskStyles.quiet}`}
    >
      {riskLabels[risk] ?? risk}
    </span>
  );
}

function countsLine(bucket: ThemeBucket): string {
  const projects = `${bucket.project_count} ${bucket.project_count === 1 ? 'project' : 'projects'}`;
  const tasks = `${bucket.task_count} ${bucket.task_count === 1 ? 'task' : 'tasks'}`;
  return `${projects}, ${tasks}`;
}

function ThemeCard({ bucket }: { bucket: ThemeBucket }) {
  return (
    <Card>
      <div className="flex items-center justify-between gap-3 mb-3">
        <div className="flex items-center gap-2">
          <h2 className="text-lg font-semibold">
            {bucket.name ?? 'No theme yet'}
          </h2>
          <RiskBadge risk={bucket.risk} />
        </div>
        <span className="text-xs text-slate-500 dark:text-slate-400">
          {countsLine(bucket)}
        </span>
      </div>

      {bucket.projects.length > 0 && (
        <ul className="mb-3 space-y-1.5">
          {bucket.projects.map((p) => (
            <li key={p.name} className="flex items-center gap-2 text-sm">
              <Icon name="folder" size={16} className="text-blue-600 dark:text-blue-400 shrink-0" />
              <span className="text-slate-700 dark:text-slate-300">{p.name}</span>
              <RiskBadge risk={p.risk} />
            </li>
          ))}
        </ul>
      )}

      {bucket.tasks.length > 0 && (
        <ul className="space-y-1.5">
          {bucket.tasks.map((t) => (
            <li key={t.id} className="flex items-center gap-2 text-sm">
              <Icon name="radio_button_unchecked" size={16} className="text-slate-400 dark:text-slate-500 shrink-0" />
              <span className="text-slate-700 dark:text-slate-300">{t.title}</span>
              <RiskBadge risk={t.risk} />
            </li>
          ))}
        </ul>
      )}

      {bucket.projects.length === 0 && bucket.tasks.length === 0 && (
        <p className="text-sm text-slate-500 dark:text-slate-400">
          Nothing here yet.
        </p>
      )}
    </Card>
  );
}

// Set up and manage the org's themes. Shown above the buckets so the
// page explains itself: add themes here, then tag work on its own pages.
function ThemeManagerCard({
  themes,
  newTheme,
  onNewThemeChange,
  onAdd,
  onRemove,
  saving,
}: {
  themes: string[];
  newTheme: string;
  onNewThemeChange: (value: string) => void;
  onAdd: () => void;
  onRemove: (name: string) => void;
  saving: boolean;
}) {
  const hasThemes = themes.length > 0;
  return (
    <div className="mb-4" data-testid="theme-setup-card">
      <Card>
        {hasThemes ? (
          <p className="text-sm font-semibold mb-3">Your themes</p>
        ) : (
          <div className="mb-3">
            <h2 className="text-lg font-semibold mb-1">Set up your themes</h2>
            <p className="text-sm text-slate-600 dark:text-slate-400">
              Themes are the big goals your work supports, like customer trust
              or faster onboarding. Add a few here, then give your tasks and
              projects a theme from their own pages, and this view will group
              everything for you.
            </p>
          </div>
        )}

        {hasThemes && (
          <div className="flex flex-wrap gap-2 mb-3">
            {themes.map((name) => (
              <span
                key={name}
                className="inline-flex items-center gap-1 px-2.5 py-1 rounded-full text-xs font-medium bg-blue-500/10 text-blue-700 dark:text-blue-300"
              >
                {name}
                <button
                  onClick={() => onRemove(name)}
                  disabled={saving}
                  title={`Remove ${name}`}
                  data-testid={`theme-remove-${name}`}
                  className="hover:text-red-600 dark:hover:text-red-400 transition-colors"
                >
                  <Icon name="close" size={12} />
                </button>
              </span>
            ))}
          </div>
        )}

        <div className="flex items-center gap-2">
          <input
            type="text"
            value={newTheme}
            onChange={(e) => onNewThemeChange(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter') onAdd();
            }}
            placeholder="A goal your work supports, like customer trust"
            data-testid="theme-input"
            className="flex-1 min-w-0 px-3 py-2 rounded-lg text-sm bg-slate-100 dark:bg-slate-800 text-slate-700 dark:text-slate-300 placeholder:text-slate-400 dark:placeholder:text-slate-500 focus:outline-none focus:ring-2 focus:ring-blue-500/40"
          />
          <button
            onClick={onAdd}
            disabled={saving || newTheme.trim() === ''}
            data-testid="theme-add"
            className="px-4 py-2 rounded-lg text-sm font-medium bg-blue-600 hover:bg-blue-700 text-white disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
          >
            Add theme
          </button>
        </div>
      </Card>
    </div>
  );
}

export default function PortfolioPage() {
  const [rollup, setRollup] = useState<RollupResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const fetchRollup = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await api.get<RollupResponse>('/themes/rollup');
      setRollup(res);
    } catch (e) {
      reportError('Failed to fetch the portfolio rollup', e);
      setError('Could not load your portfolio. Check that yourOS is running and try again.');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchRollup();
  }, [fetchRollup]);

  const [lists, setLists] = useState<OrgLists | null>(null);
  const [newTheme, setNewTheme] = useState('');
  const [savingTheme, setSavingTheme] = useState(false);

  const fetchLists = useCallback(async () => {
    try {
      const res = await api.get<Partial<OrgLists>>('/enterprise/lists');
      setLists({
        job_roles: res?.job_roles ?? [],
        pillars: res?.pillars ?? [],
      });
    } catch (e) {
      reportError('Failed to fetch the theme list', e);
    }
  }, []);

  useEffect(() => {
    fetchLists();
  }, [fetchLists]);

  // Save the full themes list, then refresh the rollup so the new
  // buckets show up right away. The backend stores each list under its
  // own key, so writing pillars never touches job_roles.
  const saveThemes = useCallback(
    async (pillars: string[]) => {
      setSavingTheme(true);
      try {
        await api.put('/enterprise/lists/pillars', { values: pillars });
        setLists((prev) => ({ job_roles: prev?.job_roles ?? [], pillars }));
        await fetchRollup();
      } catch (e) {
        reportError('Failed to save your themes', e);
        setError('Could not save your themes. Check that yourOS is running and try again.');
      } finally {
        setSavingTheme(false);
      }
    },
    [fetchRollup]
  );

  const addTheme = useCallback(() => {
    const name = newTheme.trim();
    if (!name || !lists) return;
    setNewTheme('');
    if (lists.pillars.includes(name)) return;
    saveThemes([...lists.pillars, name]);
  }, [newTheme, lists, saveThemes]);

  const removeTheme = useCallback(
    (name: string) => {
      if (!lists) return;
      saveThemes(lists.pillars.filter((p) => p !== name));
    },
    [lists, saveThemes]
  );

  const themes = rollup?.themes ?? [];
  const jira = rollup?.jira;
  // The catch-all bucket is always present. "Nothing themed yet" means
  // there are no named themes and the catch-all is empty too.
  const isEmpty =
    themes.length > 0 &&
    themes.every((t) => t.name === null && t.project_count === 0 && t.task_count === 0);
  // Hide an empty catch-all bucket when named themes exist, so the page
  // only shows "No theme yet" when there is actually untagged work.
  const visibleThemes = themes.filter(
    (t) => t.name !== null || t.project_count > 0 || t.task_count > 0
  );

  return (
    <PageShell title="Portfolio">
      <div className="flex flex-wrap items-center justify-between gap-3 mb-6 sm:mb-8">
        <div>
          <h1 className="text-2xl sm:text-3xl font-bold mb-1">Portfolio</h1>
          <p className="text-slate-600 dark:text-slate-400">
            Your work, grouped into the themes it supports.
          </p>
        </div>
        <button
          onClick={fetchRollup}
          className="flex items-center gap-2 px-4 py-2 bg-slate-100 dark:bg-slate-800 hover:bg-slate-200 dark:hover:bg-slate-700 rounded-lg text-sm text-slate-700 dark:text-slate-300 transition-colors"
        >
          <Icon name="refresh" size={16} />
          Refresh
        </button>
      </div>

      {loading && <LoadingState />}
      {error && <ErrorBanner message={error} />}

      {!loading && !error && lists && (
        <ThemeManagerCard
          themes={lists.pillars}
          newTheme={newTheme}
          onNewThemeChange={setNewTheme}
          onAdd={addTheme}
          onRemove={removeTheme}
          saving={savingTheme}
        />
      )}

      {!loading && !error && isEmpty && (
        <div data-testid="portfolio-empty">
          <EmptyState
            icon="donut_small"
            title="No themes yet"
            description="Themes are the goals your work supports. Tag your tasks and projects with a theme on their pages, and this view rolls everything up so you can see how each goal is doing at a glance."
          />
        </div>
      )}

      {!loading && !error && !isEmpty && (
        <div className="space-y-4">
          {visibleThemes.map((bucket) => (
            <ThemeCard key={bucket.name ?? '__none__'} bucket={bucket} />
          ))}
        </div>
      )}

      {!loading && !error && jira?.connected && (
        <div className="mt-8">
          <h2 className="text-lg font-semibold mb-3">Your Jira tickets</h2>
          {jira.tickets.length === 0 ? (
            <p className="text-sm text-slate-500 dark:text-slate-400">
              No tickets assigned to you right now.
            </p>
          ) : (
            <Card>
              <ul className="space-y-1.5">
                {jira.tickets.map((ticket) => (
                  <li key={ticket.key} className="flex items-center gap-2 text-sm">
                    <span className="font-mono text-xs text-blue-700 dark:text-blue-400 shrink-0">
                      {ticket.key}
                    </span>
                    <span className="text-slate-700 dark:text-slate-300">{ticket.summary}</span>
                    <span className="ml-auto text-xs text-slate-500 dark:text-slate-400 shrink-0">
                      {ticket.status}
                    </span>
                  </li>
                ))}
              </ul>
            </Card>
          )}
        </div>
      )}
    </PageShell>
  );
}
