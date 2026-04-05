import React from 'react'

// Render inline markdown: **bold**, *italic*, `code`
export function renderMarkdown(text: string): React.ReactNode[] {
  const parts = text.split(/(\*\*[^*\n]+\*\*|\*[^*\n]+\*|`[^`\n]+`)/g)
  return parts
    .map((part, i) => {
      if (part.startsWith('**') && part.endsWith('**') && part.length > 4) {
        return (
          <strong key={i} className="font-bold text-white">
            {part.slice(2, -2)}
          </strong>
        )
      }
      if (part.startsWith('*') && part.endsWith('*') && part.length > 2) {
        return (
          <em key={i} className="italic">
            {part.slice(1, -1)}
          </em>
        )
      }
      if (part.startsWith('`') && part.endsWith('`') && part.length > 2) {
        return (
          <code key={i} className="bg-slate-800 px-1 py-0.5 rounded text-[11px] font-mono text-amber-300">
            {part.slice(1, -1)}
          </code>
        )
      }
      return part || null
    })
    .filter((p) => p !== null && p !== '') as React.ReactNode[]
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
