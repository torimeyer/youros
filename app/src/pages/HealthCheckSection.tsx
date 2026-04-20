import { useState, useEffect } from 'react';
import { api } from '../lib/api';

interface ProbeResult {
  timestamp?: string;
  status?: string;
  error?: string;
}

export function HealthCheckSection() {
  const [probeResult, setProbeResult] = useState<ProbeResult | null>(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    fetchProbeStatus();
  }, []);

  const fetchProbeStatus = async () => {
    try {
      const response = await api.get<{ last_probe_result: ProbeResult | null }>('/settings/probe');
      setProbeResult(response.last_probe_result);
    } catch (err) {
      console.error('Failed to fetch probe status:', err);
    }
  };

  const handleRunProbe = async () => {
    setLoading(true);
    try {
      const result = await api.post<ProbeResult>('/settings/probe/run');
      setProbeResult(result);
    } catch (err) {
      console.error('Failed to run probe:', err);
      setProbeResult({
        status: 'failed',
        error: err instanceof Error ? err.message : 'Unknown error',
        timestamp: new Date().toISOString(),
      });
    } finally {
      setLoading(false);
    }
  };

  const formatTime = (isoString?: string) => {
    if (!isoString) return 'Never';
    try {
      const date = new Date(isoString);
      return date.toLocaleTimeString('en-US', {
        hour: '2-digit',
        minute: '2-digit',
        second: '2-digit'
      });
    } catch {
      return 'Invalid time';
    }
  };

  const isHealthy = probeResult?.status === 'ok';

  return (
    <div className="bg-slate-900/40 border border-slate-800 p-4 sm:p-6 rounded-xl hover:border-slate-700 transition-colors">
      <div className="flex items-center justify-between mb-4">
        <h2 className="text-lg font-semibold">System health</h2>
        <button
          onClick={handleRunProbe}
          disabled={loading}
          className="px-3 py-1.5 bg-blue-600 hover:bg-blue-500 disabled:opacity-60 rounded-lg text-white text-sm font-medium transition-colors"
        >
          {loading ? 'Checking...' : 'Retry now'}
        </button>
      </div>

      <div className="flex items-center gap-3">
        <div className={`w-3 h-3 rounded-full ${isHealthy ? 'bg-green-500' : probeResult ? 'bg-red-500' : 'bg-slate-600'}`} />
        <div className="flex-1">
          {probeResult ? (
            <>
              <p className="text-sm font-medium text-slate-200">
                Last checked: {formatTime(probeResult.timestamp)}
              </p>
              {isHealthy ? (
                <p className="text-sm text-green-400">All systems operational</p>
              ) : (
                <p className="text-sm text-red-400">
                  {probeResult.error || 'Service check failed'}
                </p>
              )}
            </>
          ) : (
            <p className="text-sm text-slate-400">No probe data yet</p>
          )}
        </div>
      </div>
    </div>
  );
}
