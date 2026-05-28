import { useState, useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import Icon from './Icon';
import { Card, SkeletonLine } from './ui';
import { api } from '../lib/api';
import { reportError } from '../lib/reportError';

interface ConfluencePage {
  id: string;
  title: string;
  space_id: string;
  updated: string;
  url: string;
}

interface ConfluencePagesResponse {
  pages: ConfluencePage[];
}

export default function ConfluenceWidget() {
  const navigate = useNavigate();
  const [pages, setPages] = useState<ConfluencePage[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const fetchPages = async () => {
      try {
        setLoading(true);
        setError(null);
        let spaceKey = '';
        try {
          const settings = await api.get<{ default_confluence_space?: string }>('/settings');
          spaceKey = (settings.default_confluence_space || '').trim();
        } catch {
          // settings fetch is best-effort
        }
        const url = spaceKey
          ? `/atlassian/confluence/pages?space_key=${encodeURIComponent(spaceKey)}`
          : '/atlassian/confluence/pages';
        const res = await api.get<ConfluencePagesResponse>(url);
        setPages((res.pages || []).slice(0, 5));
      } catch (err) {
        reportError('Failed to fetch confluence pages', err);
        setError('Not connected or failed to load');
      } finally {
        setLoading(false);
      }
    };

    fetchPages();
  }, []);

  return (
    <div data-testid="widget-confluence">
      <Card hover padding="sm" className="sm:p-6" onClick={() => navigate('/confluence')}>
        <div className="flex items-center justify-between mb-4 pr-8">
          <div className="flex items-center gap-2">
            <Icon name="menu_book" className="text-emerald-600 dark:text-emerald-400" size={20} />
            <h2 className="text-lg font-semibold">Recent Pages</h2>
          </div>
          <span className="text-xs text-slate-500">{pages.length} recent</span>
        </div>

        {loading ? (
          <div className="space-y-3">
            <SkeletonLine width="w-3/4" />
            <SkeletonLine width="w-2/3" />
          </div>
        ) : error ? (
          <p className="text-sm text-slate-500">{error}</p>
        ) : pages.length === 0 ? (
          <p className="text-sm text-slate-500">No recent pages.</p>
        ) : (
          <div className="space-y-2">
            {pages.map((page) => (
              <div
                key={page.id}
                onClick={(e) => {
                  e.stopPropagation();
                  navigate(`/confluence/${page.id}`);
                }}
                className="flex items-center gap-3 p-2 rounded-lg hover:bg-slate-800/50 transition-colors cursor-pointer"
              >
                <Icon name="article" size={14} className="text-slate-500 shrink-0" />
                <span className="flex-1 text-sm font-medium text-white truncate">
                  {page.title}
                </span>
              </div>
            ))}
          </div>
        )}
      </Card>
    </div>
  );
}
