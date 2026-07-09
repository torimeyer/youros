import ReactMarkdown, { type Components } from 'react-markdown'
import PageShell from '../components/PageShell'
// Root PRIVACY.md is the single source of truth. It is inlined at build time,
// so this page can never drift from the document (PrivacyPolicy.test.tsx
// verifies every heading in PRIVACY.md renders here).
import privacyMd from '../../../PRIVACY.md?raw'

const CODE_INLINE =
  'text-slate-700 dark:text-slate-300 bg-slate-100 dark:bg-slate-800 px-1 rounded'

// Shared element styles for the rendered document. react-markdown outputs
// bare HTML elements, and Tailwind's reset strips their default look, so each
// element gets the same classes the old hand-written page used.
const markdownComponents: Components = {
  h1: ({ children }) => (
    <h1 className="text-2xl font-semibold text-slate-900 dark:text-slate-100 mb-3">
      {children}
    </h1>
  ),
  h2: ({ children }) => (
    <h2 className="text-lg font-semibold text-slate-900 dark:text-slate-100 mt-8 mb-3">
      {children}
    </h2>
  ),
  h3: ({ children }) => (
    <h3 className="text-base font-semibold text-slate-900 dark:text-slate-100 mt-6 mb-2">
      {children}
    </h3>
  ),
  p: ({ children }) => (
    <p className="text-sm text-slate-600 dark:text-slate-400 leading-relaxed mb-3">
      {children}
    </p>
  ),
  ul: ({ children }) => (
    <ul className="text-sm text-slate-600 dark:text-slate-400 space-y-2 list-disc list-inside mb-3">
      {children}
    </ul>
  ),
  strong: ({ children }) => (
    <strong className="font-semibold text-slate-700 dark:text-slate-300">{children}</strong>
  ),
  em: ({ children }) => <em className="text-slate-500 dark:text-slate-500">{children}</em>,
  a: ({ children, href }) => (
    <a href={href} className="underline text-slate-700 dark:text-slate-300">
      {children}
    </a>
  ),
  code: ({ children }) => <code className={CODE_INLINE}>{children}</code>,
  hr: () => <hr className="border-slate-200 dark:border-slate-800 my-8" />,
}

// react-markdown (without plugins) does not understand pipe tables or render
// fenced code blocks the way this page needs, so the document is split into
// segments first: plain markdown chunks, pipe tables, and fenced code blocks.
// Tables and code blocks get dedicated rendering below; everything else goes
// through react-markdown.
type Segment =
  | { kind: 'markdown'; text: string }
  | { kind: 'table'; header: string[]; rows: string[][] }
  | { kind: 'code'; text: string }

function splitSegments(md: string): Segment[] {
  const segments: Segment[] = []
  const lines = md.split('\n')
  let buffer: string[] = []

  const flush = () => {
    const text = buffer.join('\n').trim()
    if (text) segments.push({ kind: 'markdown', text })
    buffer = []
  }

  let i = 0
  while (i < lines.length) {
    const line = lines[i]

    if (line.trimStart().startsWith('```')) {
      flush()
      const code: string[] = []
      i += 1
      while (i < lines.length && !lines[i].trimStart().startsWith('```')) {
        code.push(lines[i])
        i += 1
      }
      i += 1 // skip the closing fence
      segments.push({ kind: 'code', text: code.join('\n') })
      continue
    }

    if (line.startsWith('|')) {
      flush()
      const tableRows: string[][] = []
      while (i < lines.length && lines[i].startsWith('|')) {
        const cells = lines[i]
          .split('|')
          .slice(1, -1)
          .map((cell) => cell.trim())
        // Skip the |---|---| separator row.
        if (!cells.every((cell) => /^:?-{3,}:?$/.test(cell))) {
          tableRows.push(cells)
        }
        i += 1
      }
      const [header = [], ...rows] = tableRows
      segments.push({ kind: 'table', header, rows })
      continue
    }

    buffer.push(line)
    i += 1
  }
  flush()
  return segments
}

// Renders one table cell's text with inline markdown (backtick code spans,
// bold) by reusing react-markdown and unwrapping the paragraph it adds.
function InlineMarkdown({ text }: { text: string }) {
  return (
    <ReactMarkdown components={{ ...markdownComponents, p: ({ children }) => <>{children}</> }}>
      {text}
    </ReactMarkdown>
  )
}

function MarkdownTable({ header, rows }: { header: string[]; rows: string[][] }) {
  return (
    <div className="overflow-x-auto mb-3">
      <table className="w-full text-sm text-left border border-slate-200 dark:border-slate-800 rounded-lg">
        <thead>
          <tr className="border-b border-slate-200 dark:border-slate-800">
            {header.map((cell, i) => (
              <th key={i} className="px-3 py-2 font-semibold text-slate-900 dark:text-slate-100">
                <InlineMarkdown text={cell} />
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, r) => (
            <tr
              key={r}
              className="border-b border-slate-100 dark:border-slate-800/60 last:border-0"
            >
              {row.map((cell, c) => (
                <td key={c} className="px-3 py-2 text-slate-600 dark:text-slate-400 align-top">
                  <InlineMarkdown text={cell} />
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

export default function PrivacyPolicy() {
  const segments = splitSegments(privacyMd)

  return (
    <PageShell title="Privacy">
      <main className="pt-24 pb-16 px-8" data-testid="privacy-content">
        {segments.map((segment, i) => {
          if (segment.kind === 'code') {
            return (
              <pre
                key={i}
                className="text-xs font-mono bg-slate-100 dark:bg-slate-800 text-slate-700 dark:text-slate-300 rounded-lg p-4 overflow-x-auto mb-3"
              >
                {segment.text}
              </pre>
            )
          }
          if (segment.kind === 'table') {
            return <MarkdownTable key={i} header={segment.header} rows={segment.rows} />
          }
          return (
            <ReactMarkdown key={i} components={markdownComponents}>
              {segment.text}
            </ReactMarkdown>
          )
        })}
      </main>
    </PageShell>
  )
}
