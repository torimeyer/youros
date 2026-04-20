import { useEffect, useState } from 'react';
import { api } from '../lib/api';
import Icon from './Icon';

interface ProbeResult {
  timestamp?: string;
  status?: string;
  error?: string;
}

export function ProbeFailureBanner() {
  const [probeResult, setProbeResult] = useState<ProbeResult | null>(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    fetchProbeStatus();
    const interval = setInterval(fetchProbeStatus, 60000);
    return () => clearInterval(interval);
  }, []);

  const fetchProbeStatus = async () => {
    try {
      const response = await api.get<{ last_probe_result: ProbeResult | null }>('/settings/probe');
      setProbeResult(response.last_probe_result);
    } catch (err) {
      console.error('Failed to fetch probe status:', err);
    }
  };

  const handleRetry = async () => {
    setLoading(true);
    try {
      const result = await api.post<ProbeResult>('/settings/probe/run');
      setProbeResult(result);
    } catch (err) {
      console.error('Failed to run probe:', err);
    } finally {
      setLoading(false);
    }
  };

  if (probeResult?.status === 'ok' || !probeResult) {
    return null;
  }

  return (
    <div className="bg-red-900/20 border-b border-red-800 px-4 py-3 flex items-center justify-between">
      <div className="flex items-center gap-3">
        <Icon name="warning" size={20} className="text-red-400" />
        <div>
          <p className="text-sm font-medium text-red-300">Service check failed</p>
          <p className="text-xs text-red-400">{probeResult.error || 'System health check did not pass'}</p>
        </div>
      </div>
      <button
        onClick={handleRetry}
        disabled={loading}
        className="ml-4 px-3 py-1.5 bg-red-700 hover:bg-red-600 disabled:opacity-60 rounded text-white text-sm font-medium transition-colors flex-shrink-0"
      >
        {loading ? 'Checking...' : 'Retry now'}
      </button>
    </div>
  );
}
