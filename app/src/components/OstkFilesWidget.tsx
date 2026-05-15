import { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { api } from '../lib/api';
import Icon from './Icon';

export default function OstkFilesWidget() {
  const navigate = useNavigate();
  const [taskCount, setTaskCount] = useState(0);
  const [auditCount, setAuditCount] = useState(0);

  // Pre-fetch counts on mount so the badges are correct on first paint,
  // not 0 until the user navigates to /ostk and clicks each tab.
  useEffect(() => {
    Promise.allSettled([
      api.get<{ needles: unknown[] }>('/files/needles'),
      api.get<{ events: unknown[] }>('/files/audit'),
    ]).then(([needlesRes, auditRes]) => {
      if (needlesRes.status === 'fulfilled') {
        setTaskCount(needlesRes.value.needles?.length ?? 0);
      }
      if (auditRes.status === 'fulfilled') {
        setAuditCount(auditRes.value.events?.length ?? 0);
      }
    });
  }, []);

  return (
    <div className="grid grid-cols-2 gap-3" data-testid="ostk-files-widget">
      <button
        onClick={() => navigate('/ostk')}
        className="flex flex-col items-center p-4 bg-slate-900 rounded-lg border border-slate-800 hover:border-orange-500/60 transition-colors"
        data-testid="ostk-tasks-button"
      >
        <Icon name="push_pin" className="text-orange-400" size={24} />
        <span className="text-sm text-slate-300 mt-2" data-testid="ostk-tasks-label">
          Tasks ({taskCount})
        </span>
      </button>
      <button
        onClick={() => navigate('/ostk')}
        className="flex flex-col items-center p-4 bg-slate-900 rounded-lg border border-slate-800 hover:border-cyan-500/60 transition-colors"
        data-testid="ostk-audit-button"
      >
        <Icon name="receipt_long" className="text-cyan-400" size={24} />
        <span className="text-sm text-slate-300 mt-2" data-testid="ostk-audit-label">
          Audit ({auditCount})
        </span>
      </button>
    </div>
  );
}
