export interface MemoryBullet {
  text: string;
  added: Date | null;
}

const PROVENANCE_RE = /\s*<!--\s*added\s+([\w:.+\-]+)\s*-->\s*$/;

export function parseMemoryProvenance(content: string): MemoryBullet[] {
  return content
    .split('\n')
    .filter((line) => line.trimStart().startsWith('- '))
    .map((line) => {
      const raw = line.trimStart().slice(2);
      const match = raw.match(PROVENANCE_RE);
      if (match) {
        return {
          text: raw.slice(0, raw.length - match[0].length).trim(),
          added: new Date(match[1]),
        };
      }
      return { text: raw.trim(), added: null };
    });
}
