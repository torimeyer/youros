export interface RoadmapQuarter {
  quarter: string;
  theme: string;
  initiatives: string[];
}

export function parseRoadmapJson(content: string): RoadmapQuarter[] | null {
  let body = content;
  if (body.startsWith('---')) {
    const end = body.indexOf('---', 3);
    if (end !== -1) body = body.slice(end + 3);
  }
  const start = body.indexOf('[');
  const last = body.lastIndexOf(']');
  if (start === -1 || last === -1 || last <= start) return null;
  try {
    const parsed = JSON.parse(body.slice(start, last + 1));
    if (!Array.isArray(parsed)) return null;
    return parsed.filter(
      (q): q is RoadmapQuarter =>
        q != null &&
        typeof q.quarter === 'string' &&
        typeof q.theme === 'string' &&
        Array.isArray(q.initiatives),
    );
  } catch {
    return null;
  }
}
