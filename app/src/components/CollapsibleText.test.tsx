import { describe, it, expect } from 'vitest'
import { render } from '@testing-library/react'
import { CollapsibleText } from './ChatPanel'

// Regression guard for the markdown-in-chat bug. The bubble body used to
// render raw text (**→849** visible as literal asterisks) whenever the
// parent passed streaming=true, even for bubbles that were already settled.
// The fix routes per-bubble streaming, and these tests lock the contract:
// when streaming=false the body MUST be parsed markdown with real tags.
describe('CollapsibleText markdown rendering', () => {
  it('renders **bold** as <strong> when not streaming', () => {
    const { container } = render(
      <CollapsibleText
        text="Three agents: **→849** and **→853**"
        isLast={true}
        streaming={false}
      />,
    )
    const strongs = container.querySelectorAll('strong')
    expect(strongs.length).toBe(2)
    expect(strongs[0].textContent).toBe('→849')
    expect(strongs[1].textContent).toBe('→853')
    expect(container.textContent).not.toContain('**')
  })

  it('renders a bulleted list as <ul><li>', () => {
    const { container } = render(
      <CollapsibleText
        text={'- one\n- two\n- three'}
        isLast={false}
        streaming={false}
      />,
    )
    expect(container.querySelector('ul')).not.toBeNull()
    expect(container.querySelectorAll('li').length).toBe(3)
  })

  it('renders inline `code` as <code>', () => {
    const { container } = render(
      <CollapsibleText
        text="run `npm test` now"
        isLast={false}
        streaming={false}
      />,
    )
    expect(container.querySelector('code')?.textContent).toBe('npm test')
  })

  it('renders links as <a> with safe href', () => {
    const { container } = render(
      <CollapsibleText
        text="See [the docs](https://example.com) for more."
        isLast={false}
        streaming={false}
      />,
    )
    const a = container.querySelector('a')
    expect(a).not.toBeNull()
    expect(a?.getAttribute('href')).toBe('https://example.com')
    expect(a?.textContent).toBe('the docs')
  })

  it('shows raw text (whitespace-pre-wrap) only while actively streaming the last bubble', () => {
    const { container } = render(
      <CollapsibleText
        text="**bold**"
        isLast={true}
        streaming={true}
      />,
    )
    // During an active stream we deliberately keep the raw text to avoid
    // markdown flicker. Once streaming ends, bold must become <strong>.
    expect(container.querySelector('strong')).toBeNull()
    expect(container.textContent).toContain('**bold**')
  })

  it('parses markdown when streaming is true but this bubble is not the last', () => {
    // Parallel fan-out case: another bubble is streaming, this one is done.
    // Must show rendered markdown, not literal asterisks.
    const { container } = render(
      <CollapsibleText
        text="Status: **ready**"
        isLast={false}
        streaming={true}
      />,
    )
    expect(container.querySelector('strong')?.textContent).toBe('ready')
  })
})

// Regression guard for →1121: streaming bubbles must preserve paragraph breaks.
// The streaming path previously rendered raw text in a whitespace-pre-wrap div.
// \n\n produced a visual blank line via CSS but no <p> elements, so the visual
// separation was inconsistent between streaming and finalized states and
// depended entirely on CSS white-space behaviour that jsdom cannot verify.
describe('CollapsibleText streaming paragraph breaks (→1121)', () => {
  it('renders \\n\\n as two separate <p> elements during active streaming', () => {
    // Use {expression} syntax so \n is an actual newline, not literal backslash-n.
    const { container } = render(
      <CollapsibleText
        text={"Para one.\n\nPara two."}
        isLast={true}
        streaming={true}
      />,
    )
    const pElements = container.querySelectorAll('p')
    expect(pElements.length).toBeGreaterThanOrEqual(2)
    expect(pElements[0].textContent).toContain('Para one.')
    expect(pElements[1].textContent).toContain('Para two.')
  })

  it('does not merge the two paragraphs into one block', () => {
    const { container } = render(
      <CollapsibleText
        text={"First paragraph.\n\nSecond paragraph."}
        isLast={true}
        streaming={true}
      />,
    )
    const pElements = container.querySelectorAll('p')
    expect(pElements.length).toBeGreaterThanOrEqual(2)
    expect(pElements[0].textContent).not.toContain('Second paragraph.')
    expect(pElements[1].textContent).not.toContain('First paragraph.')
  })

  it('renders a single-newline within a paragraph as a <br> during streaming', () => {
    const { container } = render(
      <CollapsibleText
        text={"Line one.\nLine two."}
        isLast={true}
        streaming={true}
      />,
    )
    // Single newlines stay within one paragraph — no extra <p> appears.
    const pElements = container.querySelectorAll('p')
    expect(pElements.length).toBe(1)
    // The line break must be visible (br), not collapsed.
    const br = pElements[0].querySelector('br')
    expect(br).not.toBeNull()
    expect(pElements[0].textContent).toContain('Line one.')
    expect(pElements[0].textContent).toContain('Line two.')
  })
})
