import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import InlineDiff from './InlineDiff'

const SAMPLE_DIFF = `--- a/src/foo.ts
+++ b/src/foo.ts
@@ -1,3 +1,3 @@
 const x = 1
-const y = 2
+const y = 99`

describe('InlineDiff', () => {
  it('renders the diff container', () => {
    render(<InlineDiff diff={SAMPLE_DIFF} />)
    expect(screen.getByTestId('inline-diff')).toBeInTheDocument()
  })

  it('renders added lines with green styling', () => {
    render(<InlineDiff diff={SAMPLE_DIFF} />)
    const lines = screen.getByTestId('inline-diff').querySelectorAll('div')
    const addedLine = Array.from(lines).find(el => el.textContent?.startsWith('+const y = 99'))
    expect(addedLine).toBeTruthy()
    expect(addedLine?.className).toContain('green')
  })

  it('renders removed lines with red styling', () => {
    render(<InlineDiff diff={SAMPLE_DIFF} />)
    const lines = screen.getByTestId('inline-diff').querySelectorAll('div')
    const removedLine = Array.from(lines).find(el => el.textContent?.startsWith('-const y = 2'))
    expect(removedLine).toBeTruthy()
    expect(removedLine?.className).toContain('red')
  })

  it('renders hunk headers with blue styling', () => {
    render(<InlineDiff diff={SAMPLE_DIFF} />)
    const lines = screen.getByTestId('inline-diff').querySelectorAll('div')
    const hunkLine = Array.from(lines).find(el => el.textContent?.startsWith('@@'))
    expect(hunkLine).toBeTruthy()
    expect(hunkLine?.className).toContain('blue')
  })

  it('handles empty diff gracefully', () => {
    render(<InlineDiff diff="" />)
    expect(screen.getByTestId('inline-diff')).toBeInTheDocument()
  })
})
