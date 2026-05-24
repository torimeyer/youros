import { describe, it, expect } from 'vitest';
import { parseMemoryProvenance } from './parseMemoryProvenance';

describe('parseMemoryProvenance', () => {
  it('parses bullet with provenance comment', () => {
    const result = parseMemoryProvenance('- I prefer plain language <!-- added 2026-05-17T22:18:30Z -->');
    expect(result).toHaveLength(1);
    expect(result[0].text).toBe('I prefer plain language');
    expect(result[0].added).toEqual(new Date('2026-05-17T22:18:30Z'));
  });

  it('returns null added for bullet without provenance comment', () => {
    const result = parseMemoryProvenance('- I like coffee');
    expect(result).toHaveLength(1);
    expect(result[0].text).toBe('I like coffee');
    expect(result[0].added).toBeNull();
  });

  it('filters out non-bullet lines', () => {
    const content = '# Header\n\nSome text\n- bullet one <!-- added 2026-05-17T10:00:00Z -->';
    const result = parseMemoryProvenance(content);
    expect(result).toHaveLength(1);
    expect(result[0].text).toBe('bullet one');
  });

  it('handles multiple bullets mixed provenance', () => {
    const content = [
      '- First bullet <!-- added 2026-05-17T22:18:30Z -->',
      '- Second bullet no timestamp',
      '- Third bullet <!-- added 2026-05-18T09:00:00Z -->',
    ].join('\n');
    const result = parseMemoryProvenance(content);
    expect(result).toHaveLength(3);
    expect(result[0].added).toEqual(new Date('2026-05-17T22:18:30Z'));
    expect(result[1].added).toBeNull();
    expect(result[2].added).toEqual(new Date('2026-05-18T09:00:00Z'));
  });

  it('returns empty array for empty content', () => {
    expect(parseMemoryProvenance('')).toHaveLength(0);
  });
});
