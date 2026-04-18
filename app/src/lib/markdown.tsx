import React from 'react'

// Only allow links we know are safe to click. Prevents javascript: and data:
// URLs from slipping into the rendered DOM when a model produces suspicious
// markdown. Relative paths (e.g. "/tasks/123") and plain fragments are allowed.
function isSafeLinkHref(href: string): boolean {
  const trimmed = href.trim()
  if (!trimmed) return false
  if (/^(https?:|mailto:|tel:)/i.test(trimmed)) return true
  if (trimmed.startsWith('/') || trimmed.startsWith('#')) return true
  return false
}

// Apply inline markdown: [text](url), **bold**, *italic*, `code`
// Links are matched first so their inner text and the URL are never re-split
// by the bold/italic/code regex. This stops a link like `[View in Calendar](...)`
// from leaking through as raw text, which was the original bug.
function renderInline(text: string, keyPrefix: string): React.ReactNode[] {
  // Links: [label](url). Label cannot contain [ or ]. URL cannot contain whitespace or ).
  const parts = text.split(/(\[[^\[\]]+\]\([^\s)]+\)|\*\*[^*]+\*\*|\*[^*]+\*|`[^`]+`)/g)
  return parts
    .map((part, i) => {
      const linkMatch = part.match(/^\[([^\[\]]+)\]\(([^\s)]+)\)$/)
      if (linkMatch) {
        const [, label, href] = linkMatch
        if (isSafeLinkHref(href)) {
          return (
            <a
              key={`${keyPrefix}-a${i}`}
              href={href}
              target="_blank"
              rel="noopener noreferrer"
              className="text-blue-400 hover:text-blue-300 underline"
            >
              {label}
            </a>
          )
        }
        // Unsafe scheme: fall back to the label so nothing clickable renders.
        return label
      }
      if (part.startsWith('**') && part.endsWith('**') && part.length > 4) {
        return (
          <strong key={`${keyPrefix}-b${i}`} className="font-bold text-white">
            {part.slice(2, -2)}
          </strong>
        )
      }
      if (part.startsWith('*') && part.endsWith('*') && part.length > 2) {
        return (
          <em key={`${keyPrefix}-i${i}`} className="italic">
            {part.slice(1, -1)}
          </em>
        )
      }
      if (part.startsWith('`') && part.endsWith('`') && part.length > 2) {
        return (
          <code key={`${keyPrefix}-c${i}`} className="bg-slate-800 px-1 py-0.5 rounded text-[11px] font-mono text-amber-300">
            {part.slice(1, -1)}
          </code>
        )
      }
      return part || null
    })
    .filter((p) => p !== null && p !== '') as React.ReactNode[]
}

// Render full markdown: headings, fenced code blocks, bullet lists, inline styles
export function renderMarkdown(text: string): React.ReactNode[] {
  // Guard: if the input is empty or pure whitespace return nothing so callers
  // can safely treat an empty result as "no visible content". Without this a
  // single '\n' token would produce a lone <br> that makes the bubble look
  // blank even though msg.content is technically truthy.
  if (!text.trim()) return []
  const nodes: React.ReactNode[] = []
  // Trim leading and trailing whitespace so that model responses that start
  // or end with blank lines do not render as empty <br> elements at the top
  // or bottom of a chat bubble.
  const lines = text.trim().split('\n')
  let i = 0

  while (i < lines.length) {
    const line = lines[i]

    // Fenced code block
    if (line.trimStart().startsWith('```')) {
      const lang = line.trimStart().slice(3).trim()
      const codeLines: string[] = []
      i++
      while (i < lines.length && !lines[i].trimStart().startsWith('```')) {
        codeLines.push(lines[i])
        i++
      }
      i++ // skip closing ```
      nodes.push(
        <pre
          key={`block-${i}`}
          className="bg-slate-800 rounded-lg p-3 my-2 overflow-x-auto text-[12px] font-mono text-amber-200 whitespace-pre"
          data-lang={lang || undefined}
        >
          <code>{codeLines.join('\n')}</code>
        </pre>
      )
      continue
    }

    // Bullet list: collect consecutive bullet lines
    if (/^(\s*[-*+])\s/.test(line)) {
      const items: string[] = []
      while (i < lines.length && /^(\s*[-*+])\s/.test(lines[i])) {
        items.push(lines[i].replace(/^\s*[-*+]\s/, ''))
        i++
      }
      nodes.push(
        <ul key={`ul-${i}`} className="list-disc list-inside my-1 space-y-0.5 text-slate-300">
          {items.map((item, idx) => (
            <li key={idx}>{renderInline(item, `li-${i}-${idx}`)}</li>
          ))}
        </ul>
      )
      continue
    }

    // Numbered list: collect consecutive numbered lines
    if (/^\s*\d+\.\s/.test(line)) {
      const items: string[] = []
      while (i < lines.length && /^\s*\d+\.\s/.test(lines[i])) {
        items.push(lines[i].replace(/^\s*\d+\.\s/, ''))
        i++
      }
      nodes.push(
        <ol key={`ol-${i}`} className="list-decimal list-inside my-1 space-y-0.5 text-slate-300">
          {items.map((item, idx) => (
            <li key={idx}>{renderInline(item, `oli-${i}-${idx}`)}</li>
          ))}
        </ol>
      )
      continue
    }

    // Headings
    const h3 = line.match(/^###\s+(.+)/)
    if (h3) {
      nodes.push(
        <h3 key={`h3-${i}`} className="text-base font-bold text-white mt-3 mb-1">
          {renderInline(h3[1], `h3-${i}`)}
        </h3>
      )
      i++
      continue
    }

    const h2 = line.match(/^##\s+(.+)/)
    if (h2) {
      nodes.push(
        <h2 key={`h2-${i}`} className="text-lg font-bold text-white mt-3 mb-1">
          {renderInline(h2[1], `h2-${i}`)}
        </h2>
      )
      i++
      continue
    }

    const h1 = line.match(/^#\s+(.+)/)
    if (h1) {
      nodes.push(
        <h1 key={`h1-${i}`} className="text-xl font-bold text-white mt-3 mb-1">
          {renderInline(h1[1], `h1-${i}`)}
        </h1>
      )
      i++
      continue
    }

    // Horizontal rule
    if (/^---+$/.test(line.trim()) || /^\*\*\*+$/.test(line.trim())) {
      nodes.push(<hr key={`hr-${i}`} className="border-slate-700 my-2" />)
      i++
      continue
    }

    // Empty line: paragraph boundary. Skip any run of blank lines so we
    // do not emit a stray <br> between paragraphs (the paragraph spacing
    // comes from the <p> margins, not from a stacked break).
    if (line.trim() === '') {
      while (i < lines.length && lines[i].trim() === '') {
        i++
      }
      continue
    }

    // Paragraph: collect consecutive non-blank lines that are not the start
    // of a block (heading, list, fenced code, horizontal rule). Within the
    // paragraph each source newline becomes a soft break (<br>) so that
    // "sentence one.\nsentence two." renders on two visible lines, matching
    // GitHub and Slack behavior. A blank line or a block-starting line ends
    // the paragraph.
    const paragraphStart = i
    const paragraphChildren: React.ReactNode[] = []
    while (i < lines.length) {
      const current = lines[i]
      if (current.trim() === '') break
      if (current.trimStart().startsWith('```')) break
      if (/^(\s*[-*+])\s/.test(current)) break
      if (/^\s*\d+\.\s/.test(current)) break
      if (/^#{1,3}\s+/.test(current)) break
      if (/^---+$/.test(current.trim()) || /^\*\*\*+$/.test(current.trim())) break

      if (paragraphChildren.length > 0) {
        paragraphChildren.push(<br key={`br-${i}`} />)
      }
      const inlineParts = renderInline(current, `p-${i}`)
      inlineParts.forEach((node, idx) => {
        paragraphChildren.push(
          <React.Fragment key={`pf-${i}-${idx}`}>{node}</React.Fragment>,
        )
      })
      i++
    }

    if (paragraphChildren.length > 0) {
      nodes.push(
        <p key={`p-${paragraphStart}`} className="my-1">
          {paragraphChildren}
        </p>,
      )
    }
  }

  return nodes.filter((n) => n !== null) as React.ReactNode[]
}

// Render text with both @model mentions and markdown
export function renderTextWithMarkdown(
  text: string,
  modelColors: Record<string, string> = {},
): React.ReactNode[] {
  const mentionParts = text.split(/(@\w+)/g)
  const allParts: React.ReactNode[] = []

  mentionParts.forEach((part, index) => {
    if (part.startsWith('@')) {
      const model = part.slice(1).toLowerCase()
      const color = modelColors[model]
      allParts.push(
        color ? (
          <span key={`mention-${index}`} className={`${color} font-medium`}>
            {part}
          </span>
        ) : (
          part
        ),
      )
    } else {
      renderMarkdown(part).forEach((mdPart, mdIndex) => {
        allParts.push(
          <React.Fragment key={`md-${index}-${mdIndex}`}>{mdPart}</React.Fragment>,
        )
      })
    }
  })

  return allParts
}
