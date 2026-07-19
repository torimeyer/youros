import { describe, it, expect } from 'vitest'
import { render } from '@testing-library/react'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import Icon from './Icon'

describe('Icon', () => {
  it('renders a span with the material-symbols-outlined class', () => {
    const { container } = render(<Icon name="home" />)
    const span = container.querySelector('span')
    expect(span).toBeTruthy()
    expect(span!.classList.contains('material-symbols-outlined')).toBe(true)
  })

  it('renders the icon name as text content', () => {
    const { container } = render(<Icon name="settings" />)
    const span = container.querySelector('span')
    expect(span!.textContent).toBe('settings')
  })

  it('adds the filled class when filled prop is true', () => {
    const { container } = render(<Icon name="home" filled />)
    const span = container.querySelector('span')
    expect(span!.classList.contains('filled')).toBe(true)
  })

  it('does not add the filled class when filled is false or omitted', () => {
    const { container } = render(<Icon name="home" />)
    const span = container.querySelector('span')
    expect(span!.classList.contains('filled')).toBe(false)
  })

  it('applies the size as inline fontSize style', () => {
    const { container } = render(<Icon name="home" size={32} />)
    const span = container.querySelector('span')
    expect(span!.style.fontSize).toBe('32px')
  })

  it('does not set fontSize when size is not provided', () => {
    const { container } = render(<Icon name="home" />)
    const span = container.querySelector('span')
    expect(span!.style.fontSize).toBe('')
  })

  it('passes through additional className', () => {
    const { container } = render(<Icon name="home" className="text-blue-400" />)
    const span = container.querySelector('span')
    expect(span!.className).toContain('text-blue-400')
  })
})

// Regression for →2968: the @theme block in index.css defined a color token
// named --color-base. Tailwind resolves the `text-*` class against color
// tokens, so `text-base` compiled to `color: var(--color-base)` (a near-white
// surface color) instead of the standard font-size rule. Every icon and label
// written as `text-base` was painted #f4f4f5: invisible on light-mode buttons,
// coincidentally readable on dark ones (the Drive "Sync now" and Settings
// "Sync now" icons). A theme color token must never reuse a font-size scale
// name, or `text-<name>` silently stops meaning "font size" app-wide.
describe('theme color tokens do not hijack text-size classes (→2968)', () => {
  const FONT_SIZE_SCALE = [
    'xs', 'sm', 'base', 'lg', 'xl',
    '2xl', '3xl', '4xl', '5xl', '6xl', '7xl', '8xl', '9xl',
  ]

  it('index.css @theme defines no --color-<size> token shadowing text-<size>', () => {
    // Vitest runs with cwd at the app/ package root (see scripts/run-vitest.sh).
    const cssPath = resolve(process.cwd(), 'src/index.css')
    const css = readFileSync(cssPath, 'utf8')
    const themeBlock = css.match(/@theme\s*\{[\s\S]*?\n\}/)?.[0] ?? ''
    expect(themeBlock).not.toBe('')
    const offenders = FONT_SIZE_SCALE.filter((size) =>
      new RegExp(`--color-${size}\\s*:`).test(themeBlock)
    )
    expect(offenders).toEqual([])
  })
})
