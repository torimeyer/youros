import { useState, useEffect, useCallback } from "react";
import { Link } from "react-router-dom";
import TopBar from "../components/TopBar";
import Icon from "../components/Icon";
import { api } from "../lib/api";
import { reportError } from "../lib/reportError";
import { Card, EmptyState, ErrorBanner, LoadingState } from "../components/ui";

// --- Data types (mirror GET /api/portfolio/health) ---

type Health = "on_track" | "at_risk" | "off_track";

interface InitiativeAudit {
  missing_initiative_parent: boolean;
  missing_kr_link: boolean;
  missing_description: boolean;
  missing_ref_docs: boolean;
}

interface Initiative {
  key: string;
  title: string;
  health: Health;
  reasons: string[];
  audit: InitiativeAudit;
}

interface Kr {
  key: string;
  title: string;
  health: Health;
  reasons: string[];
  initiatives: Initiative[];
}

interface AuditFinding {
  key: string;
  finding: string;
  detail: string;
}

interface PendingApproval {
  key: string;
  title: string;
  draft_value: Health;
  draft_note: string;
  why: string;
}

interface HealthResponse {
  configured: boolean;
  krs: Kr[];
  audit_findings: AuditFinding[];
  pending_approvals: PendingApproval[];
}

interface ApproveResponse {
  key: string;
  written_value: string;
  comment_id: string | null;
  ok: boolean;
}

// --- Health chip (plain labels, no jargon) ---

const HEALTH_CHIP: Record<Health, { bg: string; text: string; label: string }> = {
  on_track: { bg: "bg-green-500/20", text: "text-green-600 dark:text-green-400", label: "On track" },
  at_risk: { bg: "bg-yellow-500/20", text: "text-yellow-600 dark:text-yellow-400", label: "At risk" },
  off_track: { bg: "bg-red-500/20", text: "text-red-600 dark:text-red-400", label: "Off track" },
};

function HealthChip({ health }: { health: Health }) {
  const style = HEALTH_CHIP[health] ?? HEALTH_CHIP.at_risk;
  return (
    <span
      className={`inline-flex items-center justify-center text-center px-2 py-1 min-h-[20px] rounded-full text-xs font-medium ${style.bg} ${style.text}`}
      data-testid="health-chip"
    >
      {style.label}
    </span>
  );
}

// Plain-language labels for the coverage checklist (no field-ID jargon).
const AUDIT_LABELS: { key: keyof InitiativeAudit; label: string }[] = [
  { key: "missing_initiative_parent", label: "Needs an initiative parent" },
  { key: "missing_kr_link", label: "Needs a link to a top-level goal" },
  { key: "missing_description", label: "Needs a description" },
  { key: "missing_ref_docs", label: "Needs reference docs" },
];

// --- Section 1: rollup hygiene checklist ---

function RollupHygiene({ findings }: { findings: AuditFinding[] }) {
  return (
    <section data-testid="section-rollup-hygiene" className="flex flex-col gap-3">
      <div className="flex flex-col gap-1">
        <h2 className="text-lg font-semibold text-slate-800 dark:text-slate-100">Rollup hygiene</h2>
        <p className="text-sm text-slate-500">
          Tickets that need attention before the board reads clean.
        </p>
      </div>
      {findings.length === 0 ? (
        <Card>
          <div className="flex items-center gap-2 text-sm text-green-600 dark:text-green-400">
            <Icon name="check_circle" size={18} />
            <span>Everything is in order. No gaps found.</span>
          </div>
        </Card>
      ) : (
        <Card padding="sm">
          <ul className="flex flex-col divide-y divide-slate-200 dark:divide-slate-800">
            {findings.map((f) => (
              <li
                key={f.key}
                data-testid={`audit-row-${f.key}`}
                className="flex items-start justify-between gap-3 py-3 first:pt-1 last:pb-1"
              >
                <div className="flex items-start gap-2">
                  <Icon name="warning" size={18} className="text-yellow-600 dark:text-yellow-400 mt-0.5 shrink-0" />
                  <div className="flex flex-col gap-0.5">
                    <span className="text-sm font-medium text-slate-800 dark:text-slate-200">{f.finding}</span>
                    <span className="text-xs text-slate-500">{f.detail}</span>
                  </div>
                </div>
                <Link
                  to={`/jira/${f.key}`}
                  data-testid={`audit-link-${f.key}`}
                  className="text-xs font-medium text-blue-600 dark:text-blue-400 hover:underline whitespace-nowrap shrink-0 mt-0.5"
                >
                  {f.key}
                </Link>
              </li>
            ))}
          </ul>
        </Card>
      )}
    </section>
  );
}

// --- Section 2: KR -> initiative rollup tree ---

function RollupTree({ krs }: { krs: Kr[] }) {
  return (
    <section data-testid="section-rollup-tree" className="flex flex-col gap-3">
      <div className="flex flex-col gap-1">
        <h2 className="text-lg font-semibold text-slate-800 dark:text-slate-100">Goals and the work under them</h2>
        <p className="text-sm text-slate-500">
          Each top-level goal with the initiatives feeding it and how each one is doing.
        </p>
      </div>
      {krs.length === 0 ? (
        <Card>
          <p className="text-sm text-slate-500">No goals to show yet.</p>
        </Card>
      ) : (
        <div className="flex flex-col gap-3">
          {krs.map((kr) => (
            <Card key={kr.key}>
              <div className="flex flex-col gap-3">
                <div className="flex items-start justify-between gap-3">
                  <div className="flex items-start gap-2">
                    <Link
                      to={`/jira/${kr.key}`}
                      className="text-xs font-medium text-blue-600 dark:text-blue-400 hover:underline mt-0.5 shrink-0"
                    >
                      {kr.key}
                    </Link>
                    <span className="text-sm font-semibold text-slate-800 dark:text-slate-200">{kr.title}</span>
                  </div>
                  <HealthChip health={kr.health} />
                </div>
                {kr.reasons.length > 0 && (
                  <ul className="flex flex-col gap-1 pl-1">
                    {kr.reasons.map((r, i) => (
                      <li key={i} className="text-xs text-slate-500">{r}</li>
                    ))}
                  </ul>
                )}
                <div className="flex flex-col gap-2 border-l-2 border-slate-200 dark:border-slate-800 pl-4 ml-1">
                  {kr.initiatives.map((init) => {
                    const gaps = AUDIT_LABELS.filter((a) => init.audit[a.key]);
                    return (
                      <div
                        key={init.key}
                        data-testid={`initiative-${init.key}`}
                        className="flex flex-col gap-1 py-1"
                      >
                        <div className="flex items-start justify-between gap-3">
                          <div className="flex items-start gap-2">
                            <Link
                              to={`/jira/${init.key}`}
                              className="text-xs font-medium text-blue-600 dark:text-blue-400 hover:underline mt-0.5 shrink-0"
                            >
                              {init.key}
                            </Link>
                            <span className="text-sm text-slate-700 dark:text-slate-300">{init.title}</span>
                          </div>
                          <HealthChip health={init.health} />
                        </div>
                        {gaps.length > 0 && (
                          <div className="flex flex-wrap gap-1 pl-1">
                            {gaps.map((g) => (
                              <span
                                key={g.key}
                                className="inline-flex items-center gap-1 text-xs text-yellow-700 dark:text-yellow-400"
                              >
                                <Icon name="warning" size={12} />
                                {g.label}
                              </span>
                            ))}
                          </div>
                        )}
                      </div>
                    );
                  })}
                </div>
              </div>
            </Card>
          ))}
        </div>
      )}
    </section>
  );
}

// --- Section 3: confidence updates awaiting approval ---

function PendingApprovals({
  approvals,
  onApproved,
}: {
  approvals: PendingApproval[];
  onApproved: (key: string) => void;
}) {
  return (
    <section data-testid="section-pending-approvals" className="flex flex-col gap-3">
      <div className="flex flex-col gap-1">
        <h2 className="text-lg font-semibold text-slate-800 dark:text-slate-100">Updates waiting for your approval</h2>
        <p className="text-sm text-slate-500">
          Draft status and next steps. Review, edit if needed, then approve to write it to Jira.
        </p>
      </div>
      {approvals.length === 0 ? (
        <Card>
          <p className="text-sm text-slate-500">Nothing is waiting for approval right now.</p>
        </Card>
      ) : (
        <div className="flex flex-col gap-3">
          {approvals.map((a) => (
            <ApprovalRow key={a.key} approval={a} onApproved={onApproved} />
          ))}
        </div>
      )}
    </section>
  );
}

function ApprovalRow({
  approval,
  onApproved,
}: {
  approval: PendingApproval;
  onApproved: (key: string) => void;
}) {
  const [editing, setEditing] = useState(false);
  const [value, setValue] = useState<Health>(approval.draft_value);
  const [note, setNote] = useState(approval.draft_note);
  const [submitting, setSubmitting] = useState(false);
  const [done, setDone] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleApprove() {
    setSubmitting(true);
    setError(null);
    try {
      await api.post<ApproveResponse>(`/portfolio/confidence/${approval.key}/approve`, {
        value,
        note,
        why: approval.why,
      });
      setDone(true);
      onApproved(approval.key);
    } catch (err) {
      reportError("executive-summary-approve", err);
      setError("Could not write to Jira. Please try again.");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <Card>
      <div className="flex flex-col gap-3">
        <div className="flex items-start justify-between gap-3">
          <div className="flex items-start gap-2">
            <Link
              to={`/jira/${approval.key}`}
              className="text-xs font-medium text-blue-600 dark:text-blue-400 hover:underline mt-0.5 shrink-0"
            >
              {approval.key}
            </Link>
            <span className="text-sm font-semibold text-slate-800 dark:text-slate-200">{approval.title}</span>
          </div>
          <HealthChip health={value} />
        </div>

        {editing ? (
          <div className="flex flex-col gap-2">
            <div className="flex gap-2">
              {(Object.keys(HEALTH_CHIP) as Health[]).map((h) => (
                <button
                  key={h}
                  type="button"
                  data-testid={`edit-value-${approval.key}-${h}`}
                  onClick={() => setValue(h)}
                  className={`px-2 py-1 rounded-full text-xs font-medium border ${
                    value === h
                      ? `${HEALTH_CHIP[h].bg} ${HEALTH_CHIP[h].text} border-transparent`
                      : "border-slate-300 dark:border-slate-700 text-slate-500"
                  }`}
                >
                  {HEALTH_CHIP[h].label}
                </button>
              ))}
            </div>
            <textarea
              data-testid={`edit-note-${approval.key}`}
              value={note}
              onChange={(e) => setNote(e.target.value)}
              rows={3}
              className="w-full text-sm rounded-lg border border-slate-300 dark:border-slate-700 bg-white dark:bg-slate-900 p-2 text-slate-800 dark:text-slate-200"
            />
          </div>
        ) : (
          <p data-testid={`note-${approval.key}`} className="text-sm text-slate-700 dark:text-slate-300 whitespace-pre-wrap">
            {note}
          </p>
        )}

        {error && <ErrorBanner message={error} />}

        {done ? (
          <div className="flex items-center gap-2 text-sm text-green-600 dark:text-green-400">
            <Icon name="check_circle" size={18} />
            <span>Written to Jira.</span>
          </div>
        ) : (
          <div className="flex items-center gap-2">
            <button
              type="button"
              data-testid={`edit-${approval.key}`}
              onClick={() => setEditing((e) => !e)}
              className="px-3 py-1.5 rounded-lg text-sm font-medium border border-slate-300 dark:border-slate-700 text-slate-700 dark:text-slate-300 hover:bg-slate-100 dark:hover:bg-slate-800"
            >
              {editing ? "Done editing" : "Edit"}
            </button>
            <button
              type="button"
              data-testid={`approve-${approval.key}`}
              onClick={handleApprove}
              disabled={submitting}
              className="px-3 py-1.5 rounded-lg text-sm font-medium bg-blue-600 text-white hover:bg-blue-700 disabled:opacity-60"
            >
              {submitting ? "Writing..." : "Approve & write to Jira"}
            </button>
          </div>
        )}
      </div>
    </Card>
  );
}

// --- Page ---

export default function ExecutiveSummary() {
  const [data, setData] = useState<HealthResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await api.get<HealthResponse>("/portfolio/health");
      setData(res);
    } catch (err) {
      reportError("executive-summary-load", err);
      setError("Could not load the Executive Summary. Please try again.");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  function handleApproved(key: string) {
    setData((prev) =>
      prev
        ? { ...prev, pending_approvals: prev.pending_approvals.filter((a) => a.key !== key) }
        : prev,
    );
  }

  return (
    <div>
      <TopBar />
      <div className="max-w-4xl mx-auto px-6 py-6 flex flex-col gap-8">
        <div className="flex flex-col gap-1">
          <h1 className="text-2xl font-bold text-slate-900 dark:text-slate-50">Executive Summary</h1>
          <p className="text-sm text-slate-500">
            A clean, up-to-date read of how the work is going, with one-click updates back to Jira.
          </p>
        </div>

        {loading && <LoadingState message="Loading the latest view..." />}

        {!loading && error && (
          <ErrorBanner message={error} action={{ label: "Retry", onClick: load }} />
        )}

        {!loading && !error && data && !data.configured && (
          <EmptyState
            icon="settings"
            title="Not set up yet"
            description="Once your Jira goals and confidence field are connected, this page fills in automatically. Nothing is written until it is set up."
          />
        )}

        {!loading && !error && data && data.configured && (
          <>
            <RollupHygiene findings={data.audit_findings} />
            <RollupTree krs={data.krs} />
            <PendingApprovals approvals={data.pending_approvals} onApproved={handleApproved} />
          </>
        )}
      </div>
    </div>
  );
}
