import { useEffect, useMemo, useState, useCallback, useRef } from 'react';
import Icon from './Icon';
import { api } from '../lib/api';
import { bumpSpecs } from '../lib/sidebarBus';
import SlideBeautifier from './SlideBeautifier';

// --- Types shared with the backend ---

interface FileEntry {
  name: string;
  path: string;
  size_display?: string;
}

interface ReadResponse {
  content: string | null;
  type: 'text' | 'image' | 'binary';
  size: number;
  mime?: string;
}

interface PreviewSheet {
  name: string;
  rows: unknown[][];
  total_rows: number;
  truncated: boolean;
}

interface SpreadsheetPreview {
  kind: 'spreadsheet';
  sheets: PreviewSheet[];
  name: string;
  size: number;
}

interface DocxPreview {
  kind: 'docx';
  html: string;
  name: string;
  size: number;
}

interface PptxSlide {
  index: number;
  title: string;
  body: string;
}

interface SlidesPreview {
  kind: 'slides';
  slides: PptxSlide[];
  name: string;
  size: number;
}

interface PdfPage {
  index: number;
  text: string;
}

interface PdfPreview {
  kind: 'pdf';
  pages: PdfPage[];
  total_pages: number;
  truncated: boolean;
  name: string;
  size: number;
}

type RichPreview = SpreadsheetPreview | DocxPreview | SlidesPreview | PdfPreview;

interface Props {
  entry: FileEntry | null;
  onClose: () => void;
  onOpenExternally?: (path: string) => void;
  onDelete?: (path: string, name: string) => void;
}

// --- Extension classification ---

const MARKDOWN_EXTS = new Set(['md', 'markdown']);
const CODE_EXTS = new Set([
  'ts', 'tsx', 'js', 'jsx', 'mjs', 'cjs',
  'py', 'rb', 'go', 'rs', 'java', 'kt', 'swift',
  'c', 'cc', 'cpp', 'h', 'hpp', 'cs',
  'json', 'yaml', 'yml', 'toml', 'ini', 'cfg',
  'html', 'css', 'scss', 'less',
  'sh', 'zsh', 'bash', 'fish',
  'sql', 'xml',
]);
const TEXT_EXTS = new Set(['txt', 'log', 'env', 'gitignore', 'editorconfig']);
const IMAGE_EXTS = new Set(['png', 'jpg', 'jpeg', 'gif', 'webp', 'svg', 'ico']);
const SPREADSHEET_EXTS = new Set(['xlsx', 'xlsm', 'csv']);
const DOC_EXTS = new Set(['docx']);
const SLIDE_EXTS = new Set(['pptx']);
const PDF_EXTS = new Set(['pdf']);

type Category =
  | 'markdown'
  | 'code'
  | 'text'
  | 'image'
  | 'spreadsheet'
  | 'document'
  | 'slides'
  | 'pdf'
  | 'unknown';

export function categorizeFile(name: string): Category {
  const ext = (name.split('.').pop() || '').toLowerCase();
  if (MARKDOWN_EXTS.has(ext)) return 'markdown';
  if (CODE_EXTS.has(ext)) return 'code';
  if (TEXT_EXTS.has(ext)) return 'text';
  if (IMAGE_EXTS.has(ext)) return 'image';
  if (SPREADSHEET_EXTS.has(ext)) return 'spreadsheet';
  if (DOC_EXTS.has(ext)) return 'document';
  if (SLIDE_EXTS.has(ext)) return 'slides';
  if (PDF_EXTS.has(ext)) return 'pdf';
  return 'unknown';
}

// --- Tiny Markdown renderer ---

// Supports: headings, bold, italic, inline code, fenced code blocks,
// unordered lists, ordered lists, links, and paragraphs.
export function renderMarkdown(source: string): React.ReactNode {
  const lines = source.split(/\r?\n/);
  const blocks: React.ReactNode[] = [];
  let i = 0;
  let key = 0;

  const inline = (text: string): React.ReactNode => {
    // Escape HTML first
    const escaped = text
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;');
    // Replace patterns with placeholders, then split and rebuild as React nodes
    const parts: React.ReactNode[] = [];
    let rest = escaped;
    let idx = 0;
    const pattern =
      /(\*\*([^*]+)\*\*|\*([^*]+)\*|`([^`]+)`|\[([^\]]+)\]\(([^)]+)\))/;
    while (rest.length > 0) {
      const m = rest.match(pattern);
      if (!m || m.index === undefined) {
        parts.push(rest);
        break;
      }
      if (m.index > 0) parts.push(rest.slice(0, m.index));
      if (m[2] !== undefined) {
        parts.push(<strong key={`i${idx++}`}>{m[2]}</strong>);
      } else if (m[3] !== undefined) {
        parts.push(<em key={`i${idx++}`}>{m[3]}</em>);
      } else if (m[4] !== undefined) {
        parts.push(
          <code
            key={`i${idx++}`}
            className="bg-slate-800 text-blue-300 px-1.5 py-0.5 rounded text-[0.85em]"
          >
            {m[4]}
          </code>
        );
      } else if (m[5] !== undefined && m[6] !== undefined) {
        parts.push(
          <a
            key={`i${idx++}`}
            href={m[6]}
            target="_blank"
            rel="noreferrer"
            className="text-blue-400 hover:text-blue-300 underline"
          >
            {m[5]}
          </a>
        );
      }
      rest = rest.slice(m.index + m[0].length);
    }
    return parts;
  };

  while (i < lines.length) {
    const line = lines[i];

    // Fenced code block
    const fence = line.match(/^```(\w*)\s*$/);
    if (fence) {
      const lang = fence[1] || '';
      i++;
      const codeLines: string[] = [];
      while (i < lines.length && !/^```\s*$/.test(lines[i])) {
        codeLines.push(lines[i]);
        i++;
      }
      if (i < lines.length) i++; // skip closing fence
      blocks.push(
        <pre
          key={key++}
          className="bg-slate-950 border border-slate-800 rounded-lg p-4 overflow-auto text-xs font-mono text-slate-200 my-3"
          data-lang={lang}
        >
          <code>{codeLines.join('\n')}</code>
        </pre>
      );
      continue;
    }

    // Heading
    const heading = line.match(/^(#{1,6})\s+(.*)$/);
    if (heading) {
      const level = heading[1].length;
      const text = heading[2];
      const sizes: Record<number, string> = {
        1: 'text-2xl font-bold mt-4 mb-3',
        2: 'text-xl font-bold mt-4 mb-2',
        3: 'text-lg font-semibold mt-3 mb-2',
        4: 'text-base font-semibold mt-3 mb-1',
        5: 'text-sm font-semibold mt-2 mb-1',
        6: 'text-sm font-medium mt-2 mb-1',
      };
      const Tag = (`h${level}` as unknown) as keyof React.JSX.IntrinsicElements;
      blocks.push(
        <Tag key={key++} className={`text-slate-100 ${sizes[level]}`}>
          {inline(text)}
        </Tag>
      );
      i++;
      continue;
    }

    // Unordered list
    if (/^\s*[-*]\s+/.test(line)) {
      const items: string[] = [];
      while (i < lines.length && /^\s*[-*]\s+/.test(lines[i])) {
        items.push(lines[i].replace(/^\s*[-*]\s+/, ''));
        i++;
      }
      blocks.push(
        <ul key={key++} className="list-disc list-inside text-slate-300 my-2 space-y-1">
          {items.map((it, idx) => (
            <li key={idx}>{inline(it)}</li>
          ))}
        </ul>
      );
      continue;
    }

    // Ordered list
    if (/^\s*\d+\.\s+/.test(line)) {
      const items: string[] = [];
      while (i < lines.length && /^\s*\d+\.\s+/.test(lines[i])) {
        items.push(lines[i].replace(/^\s*\d+\.\s+/, ''));
        i++;
      }
      blocks.push(
        <ol key={key++} className="list-decimal list-inside text-slate-300 my-2 space-y-1">
          {items.map((it, idx) => (
            <li key={idx}>{inline(it)}</li>
          ))}
        </ol>
      );
      continue;
    }

    // Blank line
    if (line.trim() === '') {
      i++;
      continue;
    }

    // Paragraph (collect consecutive non-blank lines)
    const paraLines: string[] = [line];
    i++;
    while (
      i < lines.length &&
      lines[i].trim() !== '' &&
      !/^#{1,6}\s+/.test(lines[i]) &&
      !/^```/.test(lines[i]) &&
      !/^\s*[-*]\s+/.test(lines[i]) &&
      !/^\s*\d+\.\s+/.test(lines[i])
    ) {
      paraLines.push(lines[i]);
      i++;
    }
    blocks.push(
      <p key={key++} className="text-slate-300 leading-relaxed my-2">
        {inline(paraLines.join(' '))}
      </p>
    );
  }

  return blocks;
}

// --- Simple code highlighter ---

const CODE_KEYWORDS = new Set([
  'function', 'return', 'if', 'else', 'for', 'while', 'const', 'let',
  'var', 'class', 'import', 'export', 'from', 'default', 'new', 'this',
  'async', 'await', 'try', 'catch', 'finally', 'throw', 'typeof',
  'instanceof', 'in', 'of', 'break', 'continue', 'switch', 'case',
  'def', 'lambda', 'pass', 'yield', 'global', 'nonlocal', 'is', 'not',
  'and', 'or', 'with', 'as', 'True', 'False', 'None', 'null', 'true',
  'false', 'undefined', 'public', 'private', 'protected', 'static',
  'void', 'int', 'float', 'string', 'bool', 'struct', 'enum', 'fn',
  'impl', 'trait', 'pub', 'use', 'mod', 'where', 'mut', 'self',
  'package', 'interface', 'extends', 'implements', 'abstract', 'final',
]);

function highlightCode(source: string): React.ReactNode {
  // Tokenize: strings, comments, numbers, identifiers, other.
  // This is deliberately minimal. It's a visual aid, not a full lexer.
  const tokens: React.ReactNode[] = [];
  let i = 0;
  let key = 0;
  while (i < source.length) {
    const ch = source[i];
    // Line comment
    if ((ch === '/' && source[i + 1] === '/') || ch === '#') {
      let end = source.indexOf('\n', i);
      if (end === -1) end = source.length;
      tokens.push(
        <span key={key++} className="text-slate-500 italic">
          {source.slice(i, end)}
        </span>
      );
      i = end;
      continue;
    }
    // Block comment
    if (ch === '/' && source[i + 1] === '*') {
      const end = source.indexOf('*/', i + 2);
      const stop = end === -1 ? source.length : end + 2;
      tokens.push(
        <span key={key++} className="text-slate-500 italic">
          {source.slice(i, stop)}
        </span>
      );
      i = stop;
      continue;
    }
    // String literal (single, double, backtick)
    if (ch === '"' || ch === "'" || ch === '`') {
      const quote = ch;
      let j = i + 1;
      while (j < source.length && source[j] !== quote) {
        if (source[j] === '\\') j += 2;
        else j++;
      }
      j = Math.min(j + 1, source.length);
      tokens.push(
        <span key={key++} className="text-green-300">
          {source.slice(i, j)}
        </span>
      );
      i = j;
      continue;
    }
    // Number
    if (/\d/.test(ch)) {
      let j = i;
      while (j < source.length && /[\d._]/.test(source[j])) j++;
      tokens.push(
        <span key={key++} className="text-amber-300">
          {source.slice(i, j)}
        </span>
      );
      i = j;
      continue;
    }
    // Identifier / keyword
    if (/[A-Za-z_$]/.test(ch)) {
      let j = i;
      while (j < source.length && /[A-Za-z0-9_$]/.test(source[j])) j++;
      const word = source.slice(i, j);
      if (CODE_KEYWORDS.has(word)) {
        tokens.push(
          <span key={key++} className="text-purple-300 font-medium">
            {word}
          </span>
        );
      } else {
        tokens.push(<span key={key++}>{word}</span>);
      }
      i = j;
      continue;
    }
    // Everything else: push as plain
    tokens.push(<span key={key++}>{ch}</span>);
    i++;
  }
  return tokens;
}

// --- Spreadsheet viewer ---

function SpreadsheetView({ preview }: { preview: SpreadsheetPreview }) {
  const [activeSheet, setActiveSheet] = useState(0);
  const sheet = preview.sheets[activeSheet];
  if (!sheet) {
    return <p className="text-sm text-slate-500">This spreadsheet is empty.</p>;
  }
  const [header, ...body] = sheet.rows;

  return (
    <div>
      {preview.sheets.length > 1 && (
        <div className="flex gap-1 mb-3 border-b border-slate-800">
          {preview.sheets.map((s, idx) => (
            <button
              key={s.name}
              onClick={() => setActiveSheet(idx)}
              className={`px-3 py-1.5 text-xs rounded-t transition-colors ${
                idx === activeSheet
                  ? 'bg-slate-800 text-slate-100 border-t border-l border-r border-slate-700'
                  : 'text-slate-400 hover:text-slate-200'
              }`}
            >
              {s.name}
            </button>
          ))}
        </div>
      )}

      <div className="overflow-auto border border-slate-800 rounded-lg">
        <table className="min-w-full text-xs">
          {header && (
            <thead className="bg-slate-800/80 text-slate-200 sticky top-0">
              <tr>
                {(header as unknown[]).map((cell, idx) => (
                  <th
                    key={idx}
                    className="px-3 py-2 text-left font-semibold border-b border-slate-700"
                  >
                    {String(cell ?? '')}
                  </th>
                ))}
              </tr>
            </thead>
          )}
          <tbody>
            {body.map((row, rIdx) => (
              <tr
                key={rIdx}
                className={rIdx % 2 === 0 ? 'bg-slate-900/40' : 'bg-slate-900/20'}
              >
                {(row as unknown[]).map((cell, cIdx) => (
                  <td
                    key={cIdx}
                    className="px-3 py-1.5 border-b border-slate-800/60 text-slate-300 whitespace-nowrap"
                  >
                    {cell === null || cell === undefined ? '' : String(cell)}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {sheet.truncated && (
        <p className="mt-3 text-xs text-slate-500 text-center">
          Showing the first {body.length} rows of {sheet.total_rows - 1}.
        </p>
      )}
    </div>
  );
}

// --- Main component ---

export default function FilePreviewPane({ entry, onClose, onOpenExternally, onDelete }: Props) {
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [readData, setReadData] = useState<ReadResponse | null>(null);
  const [richData, setRichData] = useState<RichPreview | null>(null);
  const [retryKey, setRetryKey] = useState(0);

  const category = useMemo(
    () => (entry ? categorizeFile(entry.name) : 'unknown'),
    [entry]
  );

  const load = useCallback(async () => {
    if (!entry) return;
    setLoading(true);
    setError(null);
    setReadData(null);
    setRichData(null);

    try {
      if (
        category === 'spreadsheet' ||
        category === 'document' ||
        category === 'slides' ||
        category === 'pdf'
      ) {
        const res = await api.get<RichPreview>(
          `/files/preview?path=${encodeURIComponent(entry.path)}`
        );
        setRichData(res);
      } else if (
        category === 'markdown' ||
        category === 'code' ||
        category === 'text' ||
        category === 'image' ||
        category === 'unknown'
      ) {
        const res = await api.get<ReadResponse>(
          `/files/read?path=${encodeURIComponent(entry.path)}`
        );
        setReadData(res);
      }
    } catch {
      setError("Couldn't open this file.");
    } finally {
      setLoading(false);
    }
  }, [entry, category]);

  useEffect(() => {
    if (entry) load();
  }, [entry, load, retryKey]);

  // Close on Escape
  useEffect(() => {
    if (!entry) return;
    const handler = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    window.addEventListener('keydown', handler);
    return () => window.removeEventListener('keydown', handler);
  }, [entry, onClose]);

  if (!entry) return null;

  return (
    <div
      className="fixed inset-0 z-50 flex justify-end bg-black/50 backdrop-blur-sm"
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
      data-testid="file-preview-overlay"
    >
      <aside
        className="bg-slate-900 border-l border-slate-700 shadow-2xl w-full max-w-3xl h-full flex flex-col"
        role="dialog"
        aria-label={`Preview of ${entry.name}`}
      >
        {/* Header */}
        <div className="flex items-center justify-between px-5 py-3 border-b border-slate-700 flex-shrink-0">
          <div className="flex items-center gap-3 min-w-0">
            <Icon name="description" className="text-xl text-slate-400 flex-shrink-0" />
            <div className="min-w-0">
              <p className="text-sm font-medium text-slate-100 truncate">{entry.name}</p>
              {entry.size_display && (
                <p className="text-[11px] text-slate-500">{entry.size_display}</p>
              )}
            </div>
          </div>
          <div className="flex items-center gap-2 flex-shrink-0 ml-3">
            {onOpenExternally && (
              <button
                onClick={() => onOpenExternally(entry.path)}
                className="flex items-center gap-1.5 px-3 py-1.5 bg-slate-800 hover:bg-slate-700 rounded-lg text-xs text-slate-300 transition-colors border border-slate-600"
                title="Open in system app"
              >
                <Icon name="open_in_new" size={14} />
                Open externally
              </button>
            )}
            {onDelete && (
              <button
                onClick={() => { onDelete(entry.path, entry.name); onClose(); }}
                className="flex items-center gap-1.5 px-3 py-1.5 bg-red-500/10 hover:bg-red-500/20 rounded-lg text-xs text-red-400 hover:text-red-300 transition-colors border border-red-500/30"
                title={`Delete ${entry.name}`}
                data-testid="preview-delete-button"
              >
                <Icon name="delete" size={14} />
                Delete
              </button>
            )}
            <button
              onClick={onClose}
              className="flex items-center justify-center w-8 h-8 hover:bg-slate-800 rounded-lg text-slate-400 hover:text-white transition-colors"
              title="Close preview"
              aria-label="Close preview"
            >
              <Icon name="close" size={18} />
            </button>
          </div>
        </div>

        {/* Body */}
        <div className="flex-1 overflow-auto p-5">
          {loading && (
            <div className="flex items-center justify-center py-16">
              <div
                className="w-6 h-6 border-2 border-slate-700 border-t-blue-400 rounded-full animate-spin"
                role="status"
                aria-label="Loading preview"
              />
            </div>
          )}

          {error && (
            <div className="flex flex-col items-center gap-3 py-12 text-center">
              <Icon name="error" className="text-3xl text-red-400" />
              <p className="text-sm text-red-300">{error}</p>
              <button
                onClick={() => setRetryKey((k) => k + 1)}
                className="px-4 py-2 bg-slate-800 hover:bg-slate-700 rounded-lg text-sm text-slate-200 border border-slate-600"
              >
                Try again
              </button>
            </div>
          )}

          {!loading && !error && (
            <PreviewBody
              category={category}
              entry={entry}
              readData={readData}
              richData={richData}
              onOpenExternally={onOpenExternally}
            />
          )}
        </div>
      </aside>
    </div>
  );
}

// --- Roadmap detection and renderer ---

// A roadmap preview shows each initiative with a plus button so the
// user can spin up a plan without leaving the Files page. Detection:
// the file has ``kind: roadmap`` in the front matter, OR the body
// is a JSON array with the quarter/theme/initiatives shape that the
// Roadmap agent template emits.
export interface RoadmapQuarter {
  quarter: string;
  theme: string;
  initiatives: string[];
}

interface ParsedRoadmap {
  quarters: RoadmapQuarter[];
  // Raw prose bullets (when the roadmap is markdown bullets instead of
  // JSON). Each entry is the bullet text already stripped of the "-"
  // or "*" prefix. Heuristic: line starts with "- " or "* " and has
  // at least 3 words of content.
  bullets: string[];
}

export function isRoadmapDoc(content: string): boolean {
  // Front matter must contain ``kind: roadmap``. The writer in the
  // backend always stamps this for Roadmap template runs, so a missing
  // line means this is a different artifact.
  const fm = content.match(/^---\n([\s\S]*?)\n---/);
  if (!fm) return false;
  return /^\s*kind:\s*roadmap\s*$/m.test(fm[1]);
}

export function parseRoadmap(content: string): ParsedRoadmap {
  // Strip front matter before we hunt for the payload.
  const stripped = content.replace(/^---\n[\s\S]*?\n---\n?/, '');
  // Also strip a leading "# Roadmap" heading (the writer adds one).
  const body = stripped.replace(/^#\s+Roadmap\s*\n+/, '').trim();

  // Try JSON first. The Roadmap template prompt asks for a bare JSON
  // array, so we pull the first [...] substring and parse it.
  const jsonMatch = body.match(/\[[\s\S]*\]/);
  if (jsonMatch) {
    try {
      const parsed: unknown = JSON.parse(jsonMatch[0]);
      if (Array.isArray(parsed)) {
        const quarters: RoadmapQuarter[] = [];
        for (const item of parsed) {
          if (
            item &&
            typeof item === 'object' &&
            typeof (item as { quarter?: unknown }).quarter === 'string' &&
            Array.isArray((item as { initiatives?: unknown }).initiatives)
          ) {
            const q = item as {
              quarter: string;
              theme?: string;
              initiatives: unknown[];
            };
            quarters.push({
              quarter: q.quarter,
              theme: typeof q.theme === 'string' ? q.theme : '',
              initiatives: q.initiatives
                .filter((x): x is string => typeof x === 'string' && x.trim().length > 0)
                .map((x) => x.trim()),
            });
          }
        }
        if (quarters.length > 0) return { quarters, bullets: [] };
      }
    } catch {
      // Fall through to bullet parsing.
    }
  }

  // Bullet fallback: any line that begins with "- " or "* " and has
  // at least three words of content is treated as an initiative. This
  // keeps prose roadmaps working while excluding short markers like
  // "- " alone or "- tbd".
  const bullets: string[] = [];
  for (const rawLine of body.split(/\r?\n/)) {
    const m = rawLine.match(/^\s*[-*]\s+(.+?)\s*$/);
    if (!m) continue;
    const text = m[1].trim();
    if (text.split(/\s+/).length >= 3) bullets.push(text);
  }
  return { quarters: [], bullets };
}

// Render a prose-style roadmap (headings, paragraphs, bullet lists) while
// injecting a "+ Make spec" button next to each bullet that looks like an
// initiative (3+ words). Preserves the document's structure so the reader
// sees Year/Theme/Goals/Milestones in context, not a flat list.
//
// The per-bullet status map drives an inline "Spec: <state>" pill that
// appears after the user promotes that bullet. The pill keeps the user
// in context (no nav required) and answers "what is this spec doing now?"
function renderRoadmapStructured(
  content: string,
  onMakeSpec: (text: string) => void,
  busy: string | null,
  statusByBullet: Record<string, SpecRowState>,
  onViewSpec: (promotedPath: string) => void
): React.ReactNode[] {
  const nodes: React.ReactNode[] = [];
  const lines = content.split(/\r?\n/);

  // Skip YAML front matter if present.
  let startIdx = 0;
  if (lines[0]?.trim() === '---') {
    for (let j = 1; j < lines.length; j++) {
      if (lines[j]?.trim() === '---') {
        startIdx = j + 1;
        break;
      }
    }
  }

  let paraBuf: string[] = [];
  let key = 0;

  const flushPara = () => {
    if (paraBuf.length === 0) return;
    const text = paraBuf.join('\n');
    nodes.push(
      <div key={`rp-p-${key++}`} className="text-sm text-slate-300 leading-relaxed my-2">
        {renderMarkdown(text)}
      </div>
    );
    paraBuf = [];
  };

  for (let i = startIdx; i < lines.length; i++) {
    const line = lines[i];
    const trimmed = line.trim();

    if (trimmed.startsWith('### ')) {
      flushPara();
      nodes.push(
        <h3 key={`rp-h3-${key++}`} className="text-base font-semibold text-slate-100 mt-4 mb-1">
          {trimmed.slice(4)}
        </h3>
      );
    } else if (trimmed.startsWith('## ')) {
      flushPara();
      nodes.push(
        <h2 key={`rp-h2-${key++}`} className="text-lg font-bold text-white mt-5 mb-2">
          {trimmed.slice(3)}
        </h2>
      );
    } else if (trimmed.startsWith('# ')) {
      flushPara();
      nodes.push(
        <h1 key={`rp-h1-${key++}`} className="text-xl font-bold text-white mt-4 mb-2">
          {trimmed.slice(2)}
        </h1>
      );
    } else if (trimmed === '---') {
      flushPara();
      nodes.push(<hr key={`rp-hr-${key++}`} className="border-slate-800 my-4" />);
    } else if (/^[-*]\s+/.test(trimmed)) {
      flushPara();
      const bulletText = trimmed.replace(/^[-*]\s+/, '').trim();
      const words = bulletText.split(/\s+/).length;
      if (words >= 3) {
        const row = statusByBullet[bulletText];
        nodes.push(
          <div
            key={`rp-li-${key++}`}
            className="flex items-start gap-2 py-1 pl-4"
            data-testid="roadmap-initiative"
          >
            <span className="text-slate-500 mt-1 flex-shrink-0">•</span>
            <span className="text-sm text-slate-300 flex-1">{bulletText}</span>
            {row ? (
              <SpecStatusChip row={row} onView={onViewSpec} />
            ) : (
              <button
                type="button"
                onClick={() => onMakeSpec(bulletText)}
                disabled={busy !== null}
                className="flex-shrink-0 text-xs px-2 py-0.5 bg-blue-600/20 hover:bg-blue-600/40 border border-blue-600/40 rounded text-blue-200 disabled:opacity-50"
                title="Make a spec from this initiative"
                data-testid="roadmap-make-spec-button"
                aria-label={`Make a spec from "${bulletText}"`}
              >
                {busy === bulletText ? '...' : '+ Make spec'}
              </button>
            )}
          </div>
        );
      } else {
        nodes.push(
          <div key={`rp-li-${key++}`} className="flex items-start gap-2 py-1 pl-4">
            <span className="text-slate-500 mt-1 flex-shrink-0">•</span>
            <span className="text-sm text-slate-400">{bulletText}</span>
          </div>
        );
      }
    } else if (trimmed === '') {
      flushPara();
    } else {
      paraBuf.push(line);
    }
  }
  flushPara();

  return nodes;
}

// Per-bullet row state. Written after a successful POST to
// /specs/from-roadmap-line and refreshed by a light /specs poll while
// any tracked row is still in a non-terminal state.
//
// status values map to the backend's computed spec status:
//   draft        - acceptance criteria generation failed, file is a draft
//   ready        - promoted to plan, waiting for the user to click Build
//   in-progress  - one or more linked tasks are still open
//   complete     - all linked tasks closed and AC ticked
//   failed       - POST itself errored (shown as a red pill)
interface SpecRowState {
  status: string;
  promotedPath: string | null;
  title: string;
}

// Map the backend status string to a color-coded pill style and a
// human-readable label. Keeps all color/label decisions in one place.
function specStatusStyle(status: string): { label: string; classes: string } {
  switch (status) {
    case 'draft':
      return {
        label: 'Spec: draft',
        classes: 'bg-slate-700/60 border-slate-500/50 text-slate-200',
      };
    case 'ready':
      return {
        label: 'Spec: ready',
        classes: 'bg-blue-600/20 border-blue-500/50 text-blue-200',
      };
    case 'in-progress':
    case 'building':
      return {
        label: 'Spec: building',
        classes: 'bg-amber-600/20 border-amber-500/50 text-amber-200',
      };
    case 'complete':
    case 'done':
      return {
        label: 'Spec: done',
        classes: 'bg-emerald-600/20 border-emerald-500/50 text-emerald-200',
      };
    case 'failed':
      return {
        label: 'Spec: failed',
        classes: 'bg-red-600/20 border-red-500/50 text-red-200',
      };
    default:
      return {
        label: `Spec: ${status}`,
        classes: 'bg-slate-700/60 border-slate-500/50 text-slate-200',
      };
  }
}

// Inline pill that appears next to a roadmap bullet once the user has
// promoted it. Shows the current status with a color cue and a "View
// spec" link for deeper inspection.
function SpecStatusChip({
  row,
  onView,
}: {
  row: SpecRowState;
  onView: (promotedPath: string) => void;
}) {
  const { label, classes } = specStatusStyle(row.status);
  return (
    <span
      className="flex-shrink-0 inline-flex items-center gap-2"
      data-testid="roadmap-spec-status-pill"
      data-status={row.status}
    >
      <span
        className={`text-xs px-2 py-0.5 rounded border ${classes}`}
        title={row.title}
      >
        {label}
      </span>
      {row.promotedPath && (
        <button
          type="button"
          onClick={() => onView(row.promotedPath as string)}
          className="text-xs text-blue-300 hover:text-blue-200 underline"
          data-testid="roadmap-spec-view-link"
        >
          View spec
        </button>
      )}
    </span>
  );
}

// Terminal states stop the poller so we never leave an interval running
// for a spec that the user has finished with.
const TERMINAL_SPEC_STATUSES = new Set(['complete', 'done', 'failed']);

interface RoadmapPreviewProps {
  content: string;
  filePath: string;
  onSpecCreated?: (result: { title: string; promotedPath: string | null }) => void;
}

export function RoadmapPreview({ content, filePath, onSpecCreated }: RoadmapPreviewProps) {
  const parsed = useMemo(() => parseRoadmap(content), [content]);
  const [busy, setBusy] = useState<string | null>(null);
  // Per-bullet status: updates on POST, then refreshed by the poller.
  const [statusByBullet, setStatusByBullet] = useState<
    Record<string, SpecRowState>
  >({});
  // One-shot toast kept from the old flow so success is obvious even
  // when the user scrolled past the bullet. The inline pill is the
  // durable signal; the toast is a transient confirmation.
  const [toast, setToast] = useState<
    { title: string; promotedPath: string | null } | null
  >(null);

  // Ref mirror of statusByBullet so the poll tick can decide whether to
  // keep polling without re-registering the interval on every change.
  const statusRef = useRef(statusByBullet);
  statusRef.current = statusByBullet;

  const viewSpec = useCallback((promotedPath: string) => {
    // Full page nav so this works even when the pane is mounted outside
    // a router context. ?expand lets the Specs page auto-open the row.
    const qp = promotedPath
      ? `?expand=${encodeURIComponent(promotedPath)}`
      : '';
    window.location.href = `/specs${qp}`;
  }, []);

  const handleMakeSpec = useCallback(
    async (initiativeText: string) => {
      setBusy(initiativeText);
      try {
        const res = await api.post<{
          title: string;
          promoted_path: string | null;
          status: string;
        }>('/specs/from-roadmap-line', {
          roadmap_path: filePath,
          initiative_text: initiativeText,
        });
        setStatusByBullet((prev) => ({
          ...prev,
          [initiativeText]: {
            status: res.status || 'ready',
            promotedPath: res.promoted_path,
            title: res.title,
          },
        }));
        setToast({ title: res.title, promotedPath: res.promoted_path });
        // Tell any open Specs tab / page to refetch so the new plan
        // appears without waiting on a page refresh.
        bumpSpecs();
        if (onSpecCreated) onSpecCreated({ title: res.title, promotedPath: res.promoted_path });
      } catch {
        setStatusByBullet((prev) => ({
          ...prev,
          [initiativeText]: {
            status: 'failed',
            promotedPath: null,
            title: initiativeText,
          },
        }));
        setToast({ title: 'Could not create plan', promotedPath: null });
      } finally {
        setBusy(null);
      }
    },
    [filePath, onSpecCreated]
  );

  // Poll /specs every 5s while any tracked row is non-terminal. Stops
  // cleanly on unmount or when every tracked spec has reached a
  // terminal state (complete/done/failed). Uses a ref to avoid
  // re-registering the interval on every status update.
  useEffect(() => {
    const anyTracked = Object.keys(statusByBullet).length > 0;
    if (!anyTracked) return;
    const anyNonTerminal = Object.values(statusByBullet).some(
      (row) => !TERMINAL_SPEC_STATUSES.has(row.status)
    );
    if (!anyNonTerminal) return;

    let cancelled = false;
    const tick = async () => {
      try {
        const res = await api.get<{
          docs: Array<{ path?: string; status?: string; title?: string }>;
        }>('/specs');
        if (cancelled) return;
        const docs = res.docs || [];
        setStatusByBullet((prev) => {
          const next = { ...prev };
          let changed = false;
          for (const [bullet, row] of Object.entries(prev)) {
            if (!row.promotedPath) continue;
            const match = docs.find((d) => d.path === row.promotedPath);
            if (!match || !match.status) continue;
            if (match.status !== row.status) {
              next[bullet] = { ...row, status: match.status };
              changed = true;
            }
          }
          return changed ? next : prev;
        });
      } catch {
        // Transient poll failure is not fatal; next tick will retry.
      }
    };

    const id = window.setInterval(tick, 5000);
    // Kick one off immediately so a refresh picks up late state too.
    tick();
    return () => {
      cancelled = true;
      window.clearInterval(id);
    };
    // statusByBullet intentionally in deps: when it changes from
    // "has non-terminal" to "all terminal", we tear down the interval.
  }, [statusByBullet]);

  const goToSpec = useCallback(() => {
    if (!toast) return;
    const qp = toast.promotedPath
      ? `?expand=${encodeURIComponent(toast.promotedPath)}`
      : '';
    window.location.href = `/specs${qp}`;
  }, [toast]);

  const renderInitiativeRow = (text: string, keyPrefix: string) => {
    const row = statusByBullet[text];
    return (
      <li
        key={`${keyPrefix}-${text}`}
        className="flex items-start gap-2 group"
        data-testid="roadmap-initiative"
      >
        <span className="text-slate-300 flex-1">{text}</span>
        {row ? (
          <SpecStatusChip row={row} onView={viewSpec} />
        ) : (
          <button
            type="button"
            onClick={() => handleMakeSpec(text)}
            disabled={busy !== null}
            className="flex-shrink-0 text-xs px-2 py-0.5 bg-blue-600/20 hover:bg-blue-600/40 border border-blue-600/40 rounded text-blue-200 disabled:opacity-50"
            title="Make a spec from this initiative"
            data-testid="roadmap-make-spec-button"
            aria-label={`Make a spec from "${text}"`}
          >
            {busy === text ? '...' : '+ Make spec'}
          </button>
        )}
      </li>
    );
  };

  return (
    <div className="space-y-4" data-testid="roadmap-preview">
      {parsed.quarters.length > 0 &&
        parsed.quarters.map((q) => (
          <div
            key={q.quarter}
            className="bg-slate-900/60 border border-slate-800 rounded-lg p-4"
          >
            <p className="text-xs uppercase tracking-widest text-slate-500 mb-1">
              {q.quarter}
            </p>
            {q.theme && (
              <h3 className="text-base font-semibold text-slate-100 mb-3">
                {q.theme}
              </h3>
            )}
            <ul className="space-y-2 text-sm">
              {q.initiatives.map((init) => renderInitiativeRow(init, q.quarter))}
            </ul>
          </div>
        ))}

      {parsed.quarters.length === 0 && parsed.bullets.length > 0 && (
        <div className="bg-slate-900/60 border border-slate-800 rounded-lg p-4">
          {renderRoadmapStructured(
            content,
            handleMakeSpec,
            busy,
            statusByBullet,
            viewSpec
          )}
        </div>
      )}

      {parsed.quarters.length === 0 && parsed.bullets.length === 0 && (
        <div className="prose-invert">{renderMarkdown(content)}</div>
      )}

      {toast && (
        <div
          className="fixed bottom-6 right-6 z-[60] bg-slate-800 border border-slate-600 rounded-lg shadow-xl px-4 py-3 flex items-center gap-3"
          role="status"
          data-testid="roadmap-spec-toast"
        >
          <div className="text-sm text-slate-100">
            Spec created: <span className="font-medium">{toast.title}</span>
          </div>
          {toast.promotedPath && (
            <button
              type="button"
              onClick={goToSpec}
              className="text-xs px-3 py-1 bg-blue-600 hover:bg-blue-500 rounded text-white"
            >
              Go to spec
            </button>
          )}
          <button
            type="button"
            onClick={() => setToast(null)}
            className="text-xs text-slate-400 hover:text-slate-200"
            aria-label="Dismiss"
          >
            Close
          </button>
        </div>
      )}
    </div>
  );
}

// --- Body dispatcher ---

function PreviewBody({
  category,
  entry,
  readData,
  richData,
  onOpenExternally,
}: {
  category: Category;
  entry: FileEntry;
  readData: ReadResponse | null;
  richData: RichPreview | null;
  onOpenExternally?: (path: string) => void;
}) {
  if (category === 'markdown' && readData && readData.content) {
    if (isRoadmapDoc(readData.content)) {
      return (
        <RoadmapPreview
          content={readData.content}
          filePath={entry.path}
        />
      );
    }
    return <div className="prose-invert">{renderMarkdown(readData.content)}</div>;
  }

  if (category === 'code' && readData && readData.content !== null) {
    return (
      <pre className="bg-slate-950 border border-slate-800 rounded-lg p-4 overflow-auto text-xs font-mono text-slate-200 leading-relaxed">
        <code>{highlightCode(readData.content)}</code>
      </pre>
    );
  }

  if (category === 'text' && readData && readData.content !== null) {
    return (
      <pre className="bg-slate-950 border border-slate-800 rounded-lg p-4 overflow-auto text-xs font-mono text-slate-300 whitespace-pre-wrap break-words leading-relaxed">
        {readData.content}
      </pre>
    );
  }

  if (category === 'image' && readData && readData.type === 'image') {
    if (readData.mime === 'image/svg+xml' && readData.content) {
      return (
        <div
          className="max-w-full overflow-auto bg-white rounded-lg p-4"
          dangerouslySetInnerHTML={{ __html: readData.content }}
          data-testid="svg-image"
        />
      );
    }
    return (
      <div className="flex items-center justify-center py-4">
        <img
          src={readData.content ?? ''}
          alt={entry.name}
          className="max-w-full max-h-[70vh] object-contain rounded-lg border border-slate-800"
        />
      </div>
    );
  }

  if (category === 'spreadsheet' && richData && richData.kind === 'spreadsheet') {
    return <SpreadsheetView preview={richData} />;
  }

  if (category === 'document' && richData && richData.kind === 'docx') {
    return (
      <div
        className="bg-white text-slate-900 rounded-lg p-6 max-w-full overflow-auto docx-body"
        dangerouslySetInnerHTML={{ __html: richData.html }}
        data-testid="docx-html"
      />
    );
  }

  if (category === 'slides' && richData && richData.kind === 'slides') {
    return (
      <div className="space-y-3">
        <SlideBeautifier filePath={entry.path} fileName={entry.name} />
        {richData.slides.map((slide) => (
          <div
            key={slide.index}
            className="bg-slate-900/60 border border-slate-800 rounded-lg p-4"
          >
            <p className="text-[10px] uppercase tracking-widest text-slate-600 mb-1">
              Slide {slide.index}
            </p>
            {slide.title && (
              <h3 className="text-lg font-semibold text-slate-100 mb-2">
                {slide.title}
              </h3>
            )}
            {slide.body && (
              <p className="text-sm text-slate-300 whitespace-pre-wrap">
                {slide.body}
              </p>
            )}
            {!slide.title && !slide.body && (
              <p className="text-xs text-slate-500 italic">No text on this slide.</p>
            )}
          </div>
        ))}
      </div>
    );
  }

  if (category === 'pdf' && richData && richData.kind === 'pdf') {
    return (
      <div className="space-y-3">
        {richData.pages.map((page) => (
          <div
            key={page.index}
            className="bg-slate-900/60 border border-slate-800 rounded-lg p-4"
          >
            <p className="text-[10px] uppercase tracking-widest text-slate-600 mb-2">
              Page {page.index}
            </p>
            {page.text ? (
              <pre className="text-sm text-slate-300 whitespace-pre-wrap font-sans leading-relaxed">
                {page.text}
              </pre>
            ) : (
              <p className="text-xs text-slate-500 italic">No text on this page.</p>
            )}
          </div>
        ))}
        {richData.truncated && (
          <p className="text-xs text-slate-500 text-center">
            Showing the first {richData.pages.length} of {richData.total_pages} pages.
          </p>
        )}
      </div>
    );
  }

  // Fallback: unsupported or binary
  return (
    <div className="flex flex-col items-center justify-center py-16 text-center">
      <Icon name="description" className="text-4xl text-slate-600 mb-3" />
      <p className="text-sm text-slate-300 mb-1">
        Preview not available for this file type.
      </p>
      <p className="text-xs text-slate-500 mb-4">
        {entry.name}
        {entry.size_display ? ` (${entry.size_display})` : ''}
      </p>
      {onOpenExternally && (
        <button
          onClick={() => onOpenExternally(entry.path)}
          className="inline-flex items-center gap-2 px-4 py-2 bg-blue-600 hover:bg-blue-500 rounded-lg text-sm text-white transition-colors"
        >
          <Icon name="open_in_new" size={16} />
          Open in system app
        </button>
      )}
    </div>
  );
}
