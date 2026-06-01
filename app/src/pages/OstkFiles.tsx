import { useState, useEffect, useCallback } from 'react';
import { formatDateTime } from '../lib/time';
import Icon from '../components/Icon';
import TopBar from '../components/TopBar';
import { api } from '../lib/api';

// --- Types ---

interface Decision {
  name: string;
  title: string;
  modified_at: string;
  size: number;
  content: string;
}

interface Needle {
  id?: string;
  title?: string;
  description?: string;
  status?: string;
  priority?: string;
  created_at?: string;
  closed_at?: string;
  tags?: string[];
  issue_type?: string;
  [key: string]: unknown;
}

interface AuditEvent {
  event?: string;
  tool?: string;
  ts?: string;
  timestamp?: string;
  [key: string]: unknown;
}

type Tab = 'decisions' | 'needles' | 'audit';

// --- Helpers ---

function timeAgo(iso: string | null | undefined): string {
  if (!iso) return '';
  const diff = Date.now() - new Date(iso).getTime();
  const mins = Math.floor(diff / 60000);
  if (mins < 1) return 'just now';
  if (mins < 60) return `${mins}m ago`;
  const hours = Math.floor(mins / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.floor(hours / 24);
  if (days === 1) return 'yesterday';
  return `${days}d ago`;
}

function statusColor(status: string | undefined): string {
  switch (status) {
    case 'open': return 'text-blue-600 dark:text-blue-400 bg-blue-500/10 border-blue-500/30';
    case 'in_progress': return 'text-yellow-600 dark:text-yellow-400 bg-yellow-500/10 border-yellow-500/30';
    case 'closed': return 'text-slate-500 bg-slate-500/10 border-slate-500/30';
    default: return 'text-slate-600 dark:text-slate-400 bg-slate-500/10 border-slate-500/20';
  }
}

function priorityColor(priority: string | undefined): string {
  switch (priority) {
    case 'P0': return 'text-red-600 dark:text-red-400';
    case 'P1': return 'text-orange-600 dark:text-orange-400';
    case 'P2': return 'text-yellow-600 dark:text-yellow-400';
    default: return 'text-slate-500';
  }
}

// --- Sub-components ---

function DecisionCard({ decision, query }: { decision: Decision; query: string }) {
  const [expanded, setExpanded] = useState(false);
  const lines = decision.content.split('\n').filter(Boolean);
  const preview = lines.slice(0, 3).join(' ').slice(0, 200);
  const highlight = (text: string) => {
    if (!query) return text;
    return text;
  };
  return (
    <div
      className="bg-white/60 dark:bg-slate-900/60 border border-slate-200 dark:border-slate-800 rounded-lg px-4 py-3 hover:border-slate-200 dark:hover:border-slate-700 transition-colors"
      data-testid="decision-card"
    >
      <div className="flex items-start justify-between gap-3">
        <div className="flex items-start gap-2 min-w-0 flex-1">
          <Icon name="article" className="text-blue-600 dark:text-blue-400 text-base mt-0.5 flex-shrink-0" />
          <div className="min-w-0">
            <p className="text-sm font-medium text-slate-800 dark:text-slate-200 truncate">{highlight(decision.title)}</p>
            <p className="text-[11px] text-slate-500 mt-0.5">{decision.name}</p>
          </div>
        </div>
        <div className="flex items-center gap-2 flex-shrink-0">
          <span className="text-[11px] text-slate-500">{timeAgo(decision.modified_at)}</span>
          <button
            onClick={() => setExpanded((v) => !v)}
            className="text-slate-500 hover:text-slate-700 dark:hover:text-slate-300 transition-colors"
            aria-label={expanded ? 'Collapse' : 'Expand'}
          >
            <Icon name={expanded ? 'expand_less' : 'expand_more'} className="text-base" />
          </button>
        </div>
      </div>
      {!expanded && preview && (
        <p className="text-[11px] text-slate-500 mt-1.5 ml-6 line-clamp-2">{preview}</p>
      )}
      {expanded && (
        <pre className="mt-3 ml-6 text-[11px] text-slate-600 dark:text-slate-400 whitespace-pre-wrap font-mono bg-white dark:bg-slate-950/60 rounded p-3 max-h-64 overflow-y-auto">
          {decision.content}
        </pre>
      )}
    </div>
  );
}

function NeedleCard({ needle, query }: { needle: Needle; query: string }) {
  const highlight = (text: string) => text;
  void query; void highlight;
  return (
    <div
      className="bg-white/60 dark:bg-slate-900/60 border border-slate-200 dark:border-slate-800 rounded-lg px-4 py-3 hover:border-slate-200 dark:hover:border-slate-700 transition-colors"
      data-testid="needle-card"
    >
      <div className="flex items-start justify-between gap-3">
        <div className="flex items-start gap-2 min-w-0 flex-1">
          <Icon name="push_pin" className="text-slate-600 dark:text-slate-400 text-base mt-0.5 flex-shrink-0" />
          <div className="min-w-0">
            <div className="flex items-center gap-2 flex-wrap">
              {needle.id && (
                <span className="text-[11px] font-mono text-slate-500 flex-shrink-0">{needle.id}</span>
              )}
              <span className={`text-[10px] font-bold flex-shrink-0 ${priorityColor(needle.priority)}`}>
                {needle.priority}
              </span>
            </div>
            <p className="text-sm font-medium text-slate-800 dark:text-slate-200 mt-0.5">{needle.title}</p>
            {needle.description && (
              <p className="text-[11px] text-slate-500 mt-1 line-clamp-2">{needle.description}</p>
            )}
            {needle.tags && needle.tags.length > 0 && (
              <div className="flex flex-wrap gap-1 mt-1.5">
                {needle.tags.map((tag) => (
                  <span key={tag} className="text-[10px] px-1.5 py-0.5 bg-slate-100 dark:bg-slate-800 text-slate-600 dark:text-slate-400 rounded">
                    {tag}
                  </span>
                ))}
              </div>
            )}
          </div>
        </div>
        <div className="flex flex-col items-end gap-1 flex-shrink-0">
          {needle.status && (
            <span className={`text-[10px] px-2 py-0.5 rounded border font-medium ${statusColor(needle.status)}`}>
              {needle.status}
            </span>
          )}
          <span className="text-[11px] text-slate-500">{timeAgo(needle.created_at)}</span>
        </div>
      </div>
    </div>
  );
}

function AuditDetailModal({ event, onClose }: { event: AuditEvent; onClose: () => void }) {
  const ts = event.ts || event.timestamp;
  const knownKeys = new Set(['event', 'tool', 'ts', 'timestamp']);
  const extraEntries = Object.entries(event).filter(([k]) => !knownKeys.has(k));

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose(); };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onClose]);

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4"
      data-testid="audit-detail-modal"
      role="dialog"
      aria-label="Audit event detail"
      onClick={onClose}
    >
      <div
        className="w-full max-w-lg max-h-[85vh] overflow-y-auto bg-white dark:bg-slate-900 border border-slate-200 dark:border-slate-800 rounded-xl shadow-2xl"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-start justify-between px-5 py-4 border-b border-slate-200 dark:border-slate-800">
          <div className="flex items-start gap-2 min-w-0">
            <Icon name="receipt_long" className="text-slate-600 dark:text-slate-400 text-lg mt-0.5 flex-shrink-0" />
            <div className="min-w-0">
              <h2 className="text-sm font-medium text-slate-900 dark:text-slate-100 truncate" data-testid="audit-detail-event">
                {event.event || '(event)'}
              </h2>
              {ts && (
                <p className="text-[11px] text-slate-500 mt-0.5">
                  {formatDateTime(ts as string)}
                </p>
              )}
            </div>
          </div>
          <button
            onClick={onClose}
            className="p-1 text-slate-500 hover:text-slate-800 dark:hover:text-slate-200 shrink-0"
            aria-label="Close"
            data-testid="audit-detail-close"
          >
            <Icon name="close" className="text-lg" />
          </button>
        </div>

        <div className="px-5 py-4 space-y-3">
          <div className="space-y-2">
            {event.event && (
              <div>
                <p className="text-[11px] text-slate-500 uppercase tracking-wide mb-0.5">Event</p>
                <p className="text-sm text-slate-800 dark:text-slate-200 font-mono">{event.event}</p>
              </div>
            )}
            {ts && (
              <div>
                <p className="text-[11px] text-slate-500 uppercase tracking-wide mb-0.5">Time</p>
                <p className="text-sm text-slate-800 dark:text-slate-200 font-mono">{formatDateTime(ts as string)}</p>
              </div>
            )}
            {event.tool && (
              <div>
                <p className="text-[11px] text-slate-500 uppercase tracking-wide mb-0.5">Tool</p>
                <p className="text-sm text-slate-800 dark:text-slate-200 font-mono">{String(event.tool)}</p>
              </div>
            )}
          </div>
          {extraEntries.length > 0 && (
            <div className="border-t border-slate-200 dark:border-slate-800 pt-3 space-y-2">
              {extraEntries.map(([key, value]) => (
                <div key={key}>
                  <p className="text-[11px] text-slate-500 uppercase tracking-wide mb-0.5">{key}</p>
                  <pre className="text-[11px] text-slate-700 dark:text-slate-300 font-mono whitespace-pre-wrap bg-white dark:bg-slate-950/60 rounded p-2 overflow-x-auto">
                    {typeof value === 'object' ? JSON.stringify(value, null, 2) : String(value ?? '')}
                  </pre>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

function AuditCard({ event, onClick }: { event: AuditEvent; onClick: () => void }) {
  const ts = event.ts || event.timestamp;
  const label = event.event || '(event)';
  const tool = event.tool;
  return (
    <button
      className="w-full text-left bg-white/40 dark:bg-slate-900/40 border border-slate-200/60 dark:border-slate-800/60 rounded px-3 py-2 font-mono text-[11px] flex items-start gap-3 hover:border-slate-200 dark:hover:border-slate-700 hover:bg-white/60 dark:hover:bg-slate-900/60 transition-colors"
      data-testid="audit-card"
      onClick={onClick}
      aria-label={`View details for ${label}`}
    >
      <span className="text-slate-500 flex-shrink-0 w-32 truncate">{timeAgo(ts)}</span>
      <span className="text-slate-700 dark:text-slate-300 flex-1 truncate">{label}</span>
      {tool && <span className="text-slate-500 flex-shrink-0">{String(tool)}</span>}
    </button>
  );
}

// --- Page ---

export default function OstkFiles() {
  const [tab, setTab] = useState<Tab>('decisions');
  const [query, setQuery] = useState('');

  const [decisions, setDecisions] = useState<Decision[]>([]);
  const [needles, setNeedles] = useState<Needle[]>([]);
  const [audit, setAudit] = useState<AuditEvent[]>([]);
  const [selectedAuditEvent, setSelectedAuditEvent] = useState<AuditEvent | null>(null);

  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const fetchTab = useCallback(async (t: Tab) => {
    setLoading(true);
    setError(null);
    try {
      if (t === 'decisions') {
        const res = await api.get<{ decisions: Decision[] }>('/files/decisions');
        setDecisions(res.decisions ?? []);
      } else if (t === 'needles') {
        const res = await api.get<{ needles: Needle[] }>('/files/needles');
        setNeedles(res.needles ?? []);
      } else {
        const res = await api.get<{ events: AuditEvent[] }>('/files/audit');
        setAudit(res.events ?? []);
      }
    } catch {
      setError('Could not load data. Make sure the backend is running.');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchTab(tab);
  }, [tab, fetchTab]);

  // Pre-fetch counts for inactive tabs on mount so the (##) badges are
  // correct on first paint rather than showing (0) until each tab is clicked.
  useEffect(() => {
    const prefetch = async () => {
      const [needlesRes, auditRes] = await Promise.allSettled([
        api.get<{ needles: Needle[] }>('/files/needles'),
        api.get<{ events: AuditEvent[] }>('/files/audit'),
      ]);
      if (needlesRes.status === 'fulfilled') setNeedles(needlesRes.value.needles ?? []);
      if (auditRes.status === 'fulfilled') setAudit(auditRes.value.events ?? []);
    };
    prefetch();
  }, []); // mount-only: seeds inactive-tab counts without touching loading state

  const q = query.toLowerCase();

  const filteredDecisions = decisions.filter(
    (d) => !q || d.title.toLowerCase().includes(q) || d.name.toLowerCase().includes(q) || d.content.toLowerCase().includes(q)
  );
  const filteredNeedles = needles.filter(
    (n) => !q || (n.title ?? '').toLowerCase().includes(q) || (n.description ?? '').toLowerCase().includes(q) || (n.id ?? '').toLowerCase().includes(q)
  );
  const filteredAudit = audit.filter(
    (e) => !q || (e.event ?? '').toLowerCase().includes(q) || (e.tool ?? '').toLowerCase().includes(q)
  );

  const tabs: { id: Tab; label: string; icon: string; count: number }[] = [
    { id: 'decisions', label: 'Decisions', icon: 'article', count: filteredDecisions.length },
    { id: 'needles', label: 'Tasks', icon: 'push_pin', count: filteredNeedles.length },
    { id: 'audit', label: 'Audit', icon: 'receipt_long', count: filteredAudit.length },
  ];

  return (
    <div className="min-h-dvh bg-white dark:bg-slate-950 text-slate-900 dark:text-white">
      <TopBar title="ostk" />

      <div className="px-4 pb-4 sm:px-8 sm:pb-8 max-w-4xl mx-auto">
        <div className="flex flex-wrap items-center justify-between gap-3 mb-5">
          <div>
            <h1 className="text-xl sm:text-2xl font-bold">ostk browser</h1>
            <p className="text-xs text-slate-500 mt-0.5">Decisions, tasks, and audit events from .ostk/</p>
          </div>
          <button
            onClick={() => fetchTab(tab)}
            className="flex items-center gap-2 px-3 py-1.5 bg-slate-100 dark:bg-slate-800 hover:bg-slate-200 dark:hover:bg-slate-700 rounded-lg text-sm text-slate-700 dark:text-slate-300 transition-colors border border-slate-200 dark:border-slate-700"
            aria-label="Refresh"
          >
            <Icon name="refresh" className="text-base" />
            Refresh
          </button>
        </div>

        {/* Search */}
        <div className="relative mb-4">
          <Icon name="search" className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-500 text-base pointer-events-none" />
          <input
            type="text"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Search…"
            className="w-full bg-white dark:bg-slate-900 border border-slate-200 dark:border-slate-700 rounded-lg pl-9 pr-4 py-2 text-sm text-slate-800 dark:text-slate-200 placeholder-slate-500 focus:outline-none focus:border-blue-500/60"
            data-testid="ostk-search"
          />
        </div>

        {/* Tab bar */}
        <div className="flex gap-1 mb-4 bg-white/60 dark:bg-slate-900/60 rounded-lg p-1 border border-slate-200 dark:border-slate-800">
          {tabs.map((t) => (
            <button
              key={t.id}
              onClick={() => setTab(t.id)}
              data-testid={`tab-${t.id}`}
              className={`flex items-center gap-1.5 flex-1 justify-center px-3 py-1.5 rounded-md text-sm font-medium transition-colors ${
                tab === t.id
                  ? 'bg-slate-200 dark:bg-slate-700 text-white'
                  : 'text-slate-600 dark:text-slate-400 hover:text-slate-800 dark:hover:text-slate-200 hover:bg-slate-50/60 dark:hover:bg-slate-800/60'
              }`}
            >
              <Icon name={t.icon} className="text-base" />
              <span>{t.label}</span>
              {!loading && (
                <span className="text-[11px] text-slate-500 tabular-nums">({t.count})</span>
              )}
            </button>
          ))}
        </div>

        {/* Content */}
        {loading && (
          <p className="text-sm text-slate-500 py-8 text-center">Loading…</p>
        )}

        {error && !loading && (
          <div className="flex items-center gap-3 p-4 bg-red-500/10 border border-red-500/30 rounded-lg text-red-600 dark:text-red-400 text-sm">
            <Icon name="error" className="text-lg" />
            <span>{error}</span>
          </div>
        )}

        {!loading && !error && tab === 'decisions' && (
          <div className="flex flex-col gap-2" data-testid="decisions-list">
            {filteredDecisions.length === 0 ? (
              <div className="text-center py-12 text-slate-500">
                <Icon name="article" className="text-3xl mb-2" />
                <p>{query ? 'No decisions match your search.' : 'No decision docs found in .ostk/.'}</p>
              </div>
            ) : (
              filteredDecisions.map((d) => (
                <DecisionCard key={d.name} decision={d} query={q} />
              ))
            )}
          </div>
        )}

        {!loading && !error && tab === 'needles' && (
          <div className="flex flex-col gap-2" data-testid="needles-list">
            {filteredNeedles.length === 0 ? (
              <div className="text-center py-12 text-slate-500">
                <Icon name="push_pin" className="text-3xl mb-2" />
                <p>{query ? 'No tasks match your search.' : 'No tasks found.'}</p>
              </div>
            ) : (
              filteredNeedles.map((n, i) => (
                <NeedleCard key={n.id ?? i} needle={n} query={q} />
              ))
            )}
          </div>
        )}

        {!loading && !error && tab === 'audit' && (
          <div className="flex flex-col gap-1" data-testid="audit-list">
            {filteredAudit.length === 0 ? (
              <div className="text-center py-12 text-slate-500">
                <Icon name="receipt_long" className="text-3xl mb-2" />
                <p>{query ? 'No events match your search.' : 'No audit events found.'}</p>
              </div>
            ) : (
              filteredAudit.map((e, i) => (
                <AuditCard key={i} event={e} onClick={() => setSelectedAuditEvent(e)} />
              ))
            )}
          </div>
        )}

      </div>

      {selectedAuditEvent && (
        <AuditDetailModal
          event={selectedAuditEvent}
          onClose={() => setSelectedAuditEvent(null)}
        />
      )}
    </div>
  );
}
