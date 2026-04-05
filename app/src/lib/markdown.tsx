import React from 'react'

// Simple markdown renderer for basic formatting in chat messages
export function renderMarkdown(text: string): React.ReactNode[] {
  const parts: React.ReactNode[] = []
  let current = ''
  let i = 0
  
  while (i < text.length) {
    if (text[i] === '*' && text[i + 1] === '*') {
      // Found start of bold
      if (current) {
        parts.push(current)
        current = ''
      }
      
      i += 2 // skip **
      const boldStart = i
      
      // Find end of bold
      while (i < text.length - 1) {
        if (text[i] === '*' && text[i + 1] === '*') {
          const boldText = text.slice(boldStart, i)
          parts.push(
            <strong key={parts.length} className="font-bold text-white">
              {boldText}
            </strong>
          )
          i += 2 // skip closing **
          break
        }
        i++
      }
      
      // If we didn't find closing **, treat it as regular text
      if (i >= text.length - 1 && text[i - 1] !== '*') {
        current += '**' + text.slice(boldStart)
        break
      }
    } else {
      current += text[i]
      i++
    }
  }
  
  if (current) {
    parts.push(current)
  }
  
  return parts
}

// Alternative function that handles model mentions AND markdown
export function renderTextWithMarkdown(text: string, modelColors: Record<string, string> = {}): React.ReactNode[] {
  // First split by model mentions
  const mentionParts = text.split(/(@\w+)/g)
  const allParts: React.ReactNode[] = []
  
  mentionParts.forEach((part, index) => {
    if (part.startsWith('@')) {
      const model = part.slice(1).toLowerCase()
      const color = modelColors[model]
      if (color) {
        allParts.push(
          <span key={`mention-${index}`} className={`${color} font-medium`}>
            {part}
          </span>
        )
      } else {
        allParts.push(part)
      }
    } else {
      // Process markdown in this non-mention part
      const markdownParts = renderMarkdown(part)
      markdownParts.forEach((mdPart, mdIndex) => {
        allParts.push(
          <React.Fragment key={`md-${index}-${mdIndex}`}>
            {mdPart}
          </React.Fragment>
        )
      })
    }
  })
  
  return allParts
}