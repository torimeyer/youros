import { describe, it, expect } from 'vitest'
import { render } from '@testing-library/react'
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
