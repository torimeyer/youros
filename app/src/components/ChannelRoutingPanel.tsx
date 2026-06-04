import { useEffect, useState } from 'react';
import { api } from '../lib/api';

// Wave 6 (→1872): Channel Routing settings panel.
//
// Lets you text commands to your phone number and have them routed to
// agents. Shows the supported commands and offers a quick "what would this
// do?" preview that parses a sample message without running anything.

interface RoutingRule {
  intent: string;
  example: string;
  action: string;
}

interface ParsePreview {
  intent: string;
  prompt?: string;
  task_id?: string | null;
  needle_id?: string | null;
  agent?: string;
  message?: string;
}

export default function ChannelRoutingPanel() {
  const [rules, setRules] = useState<RoutingRule[]>([]);
  const [sample, setSample] = useState('spawn diagnose for Task 1654');
  const [preview, setPreview] = useState<ParsePreview | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    api
      .get<{ rules: RoutingRule[] }>('/channel-routing/rules')
      .then((data) => {
        if (!cancelled) setRules(data.rules || []);
      })
      .catch(() => {
        // Non-fatal: the panel still shows the static help text.
        if (!cancelled) setRules([]);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  async function runPreview() {
    setLoading(true);
    setError(null);
    setPreview(null);
    try {
      const result = await api.post<ParsePreview>('/channel-routing/parse', {
        text: sample,
        reply_to: 'preview',
      });
      setPreview(result);
    } catch {
      setError('Could not check that message. Is the app running?');
    } finally {
      setLoading(false);
    }
  }

  function describePreview(p: ParsePreview): string {
    switch (p.intent) {
      case 'spawn': {
        if (p.task_id) return `Start an agent on Task ${p.task_id}.`;
        if (p.needle_id) return `Start an agent on →${p.needle_id}.`;
        return `Start an agent to: ${p.prompt || '(no detail)'}.`;
      }
      case 'nudge':
        return `Send "${p.message}" to the agent named ${p.agent}.`;
      case 'status':
        return 'Text back how many agents are working.';
      default:
        return 'Not recognized. You would get a short help message back.';
    }
  }

  return (
    <div data-testid="channel-routing-panel">
      <h3 className="text-base font-semibold text-slate-800 dark:text-slate-100">Text-to-agent</h3>
      <p className="text-sm text-slate-600 dark:text-slate-300 mt-1">
        Text a command to your own number and have it routed to an agent. The
        app reads new incoming messages, figures out what you meant, runs it,
        and texts you back when it is done.
      </p>

      <div className="mt-4 space-y-2">
        {(rules.length
          ? rules
          : [
              {
                intent: 'spawn',
                example: 'spawn diagnose for Task 1654',
                action: 'Starts an agent on the task and texts you back.',
              },
              {
                intent: 'nudge',
                example: 'nudge builder please hurry',
                action: 'Sends a message to a running agent.',
              },
              {
                intent: 'status',
                example: 'status',
                action: 'Texts back how many agents are working.',
              },
            ]
        ).map((rule) => (
          <div
            key={rule.intent}
            className="rounded-lg border border-slate-200 dark:border-slate-700 p-3 bg-white dark:bg-slate-800"
          >
            <code className="text-xs font-mono text-slate-800 dark:text-slate-200">{rule.example}</code>
            <p className="text-xs text-slate-500 dark:text-slate-400 mt-1">{rule.action}</p>
          </div>
        ))}
      </div>

      <div className="mt-4">
        <label className="text-sm font-medium text-slate-700 dark:text-slate-300">
          Try it: type a message to see what it would do
        </label>
        <div className="flex gap-2 mt-1">
          <input
            type="text"
            value={sample}
            onChange={(e) => setSample(e.target.value)}
            className="flex-1 rounded-lg border border-slate-300 dark:border-slate-600 dark:bg-slate-800 dark:text-slate-100 px-3 py-2 text-sm"
            data-testid="channel-routing-sample-input"
          />
          <button
            type="button"
            onClick={runPreview}
            disabled={loading || !sample.trim()}
            className="rounded-lg bg-slate-800 dark:bg-slate-600 px-4 py-2 text-sm text-white disabled:opacity-50"
            data-testid="channel-routing-preview-btn"
          >
            {loading ? 'Checking…' : 'Check'}
          </button>
        </div>
        {error && <p className="text-xs text-red-600 dark:text-red-400 mt-2">{error}</p>}
        {preview && (
          <div
            className="mt-2 rounded-lg bg-slate-50 dark:bg-slate-800/60 border border-slate-200 dark:border-slate-700 p-3"
            data-testid="channel-routing-preview-result"
          >
            <p className="text-xs text-slate-500 dark:text-slate-400">
              Recognized as: <strong>{preview.intent}</strong>
            </p>
            <p className="text-sm text-slate-700 dark:text-slate-300 mt-1">{describePreview(preview)}</p>
          </div>
        )}
      </div>
    </div>
  );
}
