import { useState } from 'react';

interface SearchResult {
  text: string;
  source_id: string;
  source_title: string;
  deep_link: string | null;
  score: number;
  access_denied: boolean;
  provider: string;
}

interface CrossSearchResponse {
  results: SearchResult[];
  providers_used: string[];
  providers_skipped: { provider: string; reason: string }[];
}

export function CrossSourceSearch() {
  const [query, setQuery] = useState('');
  const [results, setResults] = useState<SearchResult[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleSearch(e: React.FormEvent) {
    e.preventDefault();
    if (!query.trim()) return;
    setLoading(true);
    setError(null);
    try {
      const res = await fetch('/api/cross-source', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ query }),
      });
      if (!res.ok) throw new Error(`Search failed: ${res.status}`);
      const data: CrossSearchResponse = await res.json();
      setResults(data.results);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Search failed');
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="cross-source-search">
      <form onSubmit={handleSearch}>
        <input
          type="text"
          value={query}
          onChange={e => setQuery(e.target.value)}
          placeholder="Search across all connected sources..."
          aria-label="Search query"
        />
        <button type="submit" disabled={loading}>
          {loading ? 'Searching...' : 'Search'}
        </button>
      </form>
      {error && <p className="error">{error}</p>}
      {results.length > 0 && (
        <ol className="search-results">
          {results.map((r, i) => (
            <li key={`${r.provider}-${r.source_id}-${i}`}>
              <span className="provider-badge">{r.provider}</span>{' '}
              {r.deep_link ? (
                <a href={r.deep_link} target="_blank" rel="noopener noreferrer">
                  {r.source_title}
                </a>
              ) : (
                <strong>{r.source_title}</strong>
              )}
              <p>{r.text}</p>
            </li>
          ))}
        </ol>
      )}
    </div>
  );
}
