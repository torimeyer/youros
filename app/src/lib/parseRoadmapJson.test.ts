import { describe, it, expect } from 'vitest';
import { parseRoadmapJson } from './parseRoadmapJson';

const SAMPLE = JSON.stringify([
  { quarter: 'Q1 2026', theme: 'Foundation', initiatives: ['Auth', 'Onboarding'] },
  { quarter: 'Q2 2026', theme: 'Growth', initiatives: ['Analytics', 'Integrations', 'Mobile'] },
]);

describe('parseRoadmapJson', () => {
  it('parses a bare JSON array', () => {
    const result = parseRoadmapJson(SAMPLE);
    expect(result).toHaveLength(2);
    expect(result![0].quarter).toBe('Q1 2026');
    expect(result![1].initiatives).toHaveLength(3);
  });

  it('strips YAML front matter before parsing', () => {
    const withFrontMatter =
      '---\nsource: roadmap-abc\nkind: roadmap\n---\n\n# Roadmap\n\n' + SAMPLE;
    const result = parseRoadmapJson(withFrontMatter);
    expect(result).toHaveLength(2);
  });

  it('returns null for non-JSON content', () => {
    expect(parseRoadmapJson('# Not JSON\n\nSome markdown here.')).toBeNull();
  });

  it('returns null for empty string', () => {
    expect(parseRoadmapJson('')).toBeNull();
  });

  it('filters out malformed quarter entries', () => {
    const mixed = JSON.stringify([
      { quarter: 'Q1 2026', theme: 'Good', initiatives: ['A'] },
      { quarter: 'Q2 2026' },
      null,
      { theme: 'Missing quarter', initiatives: [] },
    ]);
    const result = parseRoadmapJson(mixed);
    expect(result).toHaveLength(1);
    expect(result![0].quarter).toBe('Q1 2026');
  });

  it('returns null when content has no JSON array', () => {
    expect(parseRoadmapJson('{"quarter":"Q1","theme":"x"}')).toBeNull();
  });
});
