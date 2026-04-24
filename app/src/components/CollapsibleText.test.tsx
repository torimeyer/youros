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
