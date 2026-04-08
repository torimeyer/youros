import { useEffect, useMemo, useState, useCallback } from 'react';
import Icon from './Icon';
import { api } from '../lib/api';
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

export default function FilePreviewPane({ entry, onClose, onOpenExternally }: Props) {
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
