import { useState } from 'react';
import Icon from './Icon';
import { api } from '../lib/api';

interface Props {
  onComplete: () => void;
  onCancel: () => void;
}

type Mode = 'quick' | 'guided';

const AUDIENCES = ['exec', 'team', 'board', 'stakeholders'];
const WINDOW_OPTIONS = [7, 14, 30];

export default function ExecUpdateComposer({ onComplete, onCancel }: Props) {
  const [mode, setMode] = useState<Mode>('quick');
  const [audience, setAudience] = useState('exec');
  const [windowDays, setWindowDays] = useState(7);
  const [sourceIds, setSourceIds] = useState<string[]>([]);
  const [generating, setGenerating] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleGenerate = async () => {
    try {
      setGenerating(true);
      setError(null);
      await api.post('/narrative/draft', {
        audience,
        window_days: windowDays,
        source_ids: sourceIds,
      });
      onComplete();
    } catch {
      setError('Could not generate draft. Try again.');
      setGenerating(false);
    }
  };

  const handleBackdropClick = (e: React.MouseEvent<HTMLDivElement>) => {
    if (e.target === e.currentTarget) onCancel();
  };

  return (
    <div
      role="dialog"
      aria-label="New exec update"
      onClick={handleBackdropClick}
      data-testid="exec-update-composer"
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4"
    >
      <div className="bg-slate-900 border border-slate-700 rounded-xl w-full max-w-md p-6 shadow-2xl">
        {/* Header */}
        <div className="flex items-start justify-between mb-5">
          <div className="flex items-center gap-2">
            <Icon name="edit_note" className="text-blue-400" size={20} />
            <h2 className="text-white font-semibold text-lg">New exec update</h2>
          </div>
          <button
            onClick={onCancel}
            className="text-slate-500 hover:text-white transition-colors"
            aria-label="Close"
          >
            <Icon name="close" size={20} />
          </button>
        </div>

        {/* Mode picker — mirrors SpecWizard.tsx two-mode header */}
        <div className="flex gap-1 bg-slate-800 rounded-lg p-1 mb-5">
          <button
            type="button"
            data-testid="mode-quick"
            onClick={() => setMode('quick')}
            className={`flex-1 py-1.5 px-3 rounded-md text-sm font-medium transition-colors ${
              mode === 'quick'
                ? 'bg-blue-600 text-white'
                : 'text-slate-400 hover:text-white'
            }`}
          >
            Quick draft
          </button>
          <button
            type="button"
            data-testid="mode-guided"
            onClick={() => setMode('guided')}
            className={`flex-1 py-1.5 px-3 rounded-md text-sm font-medium transition-colors ${
              mode === 'guided'
                ? 'bg-blue-600 text-white'
                : 'text-slate-400 hover:text-white'
            }`}
          >
            Guided
          </button>
        </div>

        {/* Quick mode */}
        {mode === 'quick' && (
          <div data-testid="quick-mode" className="space-y-4">
            <div>
              <label className="block text-xs text-slate-400 mb-1.5">Audience</label>
              <div className="flex flex-wrap gap-2">
                {AUDIENCES.map((a) => (
                  <button
                    key={a}
                    type="button"
                    onClick={() => setAudience(a)}
                    className={`px-3 py-1 rounded-full text-xs font-medium transition-colors ${
                      audience === a
                        ? 'bg-blue-600 text-white'
                        : 'bg-slate-700 text-slate-300 hover:bg-slate-600'
                    }`}
                  >
                    {a}
                  </button>
                ))}
              </div>
            </div>

            <div>
              <label className="block text-xs text-slate-400 mb-1.5">
                Time window: <span className="text-white font-medium">{windowDays} days</span>
              </label>
              <input
                type="range"
                min={7}
                max={30}
                step={7}
                value={windowDays}
                onChange={(e) => setWindowDays(Number(e.target.value))}
                className="w-full accent-blue-500"
                aria-label="Window days"
              />
              <div className="flex justify-between text-xs text-slate-500 mt-1">
                {WINDOW_OPTIONS.map((d) => (
                  <span key={d}>{d}d</span>
                ))}
              </div>
            </div>

            <p className="text-xs text-slate-400">
              Will pull sources from all connected tools automatically.
            </p>
          </div>
        )}

        {/* Guided mode */}
        {mode === 'guided' && (
          <div data-testid="guided-mode" className="space-y-4">
            <div>
              <label className="block text-xs text-slate-400 mb-1.5">Audience</label>
              <select
                value={audience}
                onChange={(e) => setAudience(e.target.value)}
                className="w-full bg-slate-800 border border-slate-600 text-white text-sm rounded-lg px-3 py-2 focus:outline-none focus:border-blue-500"
                aria-label="Audience"
              >
                {AUDIENCES.map((a) => (
                  <option key={a} value={a}>{a}</option>
                ))}
              </select>
            </div>

            <div>
              <label className="block text-xs text-slate-400 mb-1.5">
                Time window: <span className="text-white font-medium">{windowDays} days</span>
              </label>
              <input
                type="range"
                min={7}
                max={30}
                step={7}
                value={windowDays}
                onChange={(e) => setWindowDays(Number(e.target.value))}
                className="w-full accent-blue-500"
                aria-label="Window days"
              />
            </div>

            <div>
              <label className="block text-xs text-slate-400 mb-1.5">
                Limit to specific sources
                <span className="text-slate-500 ml-1">(optional — leave empty to use all)</span>
              </label>
              <input
                type="text"
                placeholder="e.g. PROJ-1, strategy-doc"
                value={sourceIds.join(', ')}
                onChange={(e) =>
                  setSourceIds(
                    e.target.value
                      .split(',')
                      .map((s) => s.trim())
                      .filter(Boolean),
                  )
                }
                className="w-full bg-slate-800 border border-slate-600 text-white text-sm rounded-lg px-3 py-2 focus:outline-none focus:border-blue-500"
                aria-label="Source IDs"
              />
            </div>
          </div>
        )}

        {error && (
          <p className="mt-3 text-xs text-red-400">{error}</p>
        )}

        {/* Actions */}
        <div className="flex items-center justify-end gap-2 mt-6">
          <button
            onClick={onCancel}
            className="px-4 py-2 text-slate-400 hover:text-white text-sm rounded-lg transition-colors"
          >
            Cancel
          </button>
          <button
            onClick={handleGenerate}
            disabled={generating}
            className="px-4 py-2 bg-blue-500 hover:bg-blue-600 disabled:opacity-50 text-white text-sm rounded-lg transition-colors flex items-center gap-2"
          >
            {generating && <Icon name="progress_activity" size={14} className="animate-spin" />}
            {generating ? 'Generating…' : 'Generate draft'}
          </button>
        </div>
      </div>
    </div>
  );
}
