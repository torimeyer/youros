import { describe, it, expect, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import SpecFaq from './SpecFaq'

// jsdom does not provide window.matchMedia. Provide a minimal stub
// so the responsive desktop detection in TopBar does not crash.
Object.defineProperty(window, 'matchMedia', {
  writable: true,
  value: vi.fn().mockImplementation((query: string) => ({
    matches: true,
    media: query,
    onchange: null,
    addListener: vi.fn(),
    removeListener: vi.fn(),
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
    dispatchEvent: vi.fn(),
  })),
})

function renderPage() {
  return render(
    <MemoryRouter>
      <SpecFaq />
    </MemoryRouter>
  )
}

describe('SpecFaq page', () => {
  it('renders all seven FAQ sections with correct testids', () => {
    renderPage()
    const ids = [
      'faq-q-what-is-a-spec',
      'faq-q-what-its-not',
      'faq-q-kinds',
      'faq-q-statuses',
      'faq-q-required-optional',
      'faq-q-when-not',
      'faq-q-how-treated',
    ]
    for (const id of ids) {
      expect(screen.getByTestId(id)).toBeTruthy()
    }
  })

  it('what-is-a-spec section mentions WHAT and WHY', () => {
    renderPage()
    const section = screen.getByTestId('faq-q-what-is-a-spec')
    expect(section.textContent).toMatch(/WHAT/i)
    expect(section.textContent).toMatch(/WHY/i)
  })

  it('what-its-not section heading contains "is NOT"', () => {
    renderPage()
    const section = screen.getByTestId('faq-q-what-its-not')
    expect(section.textContent).toMatch(/is NOT/i)
  })

  it('page has a top-level heading', () => {
    renderPage()
    expect(screen.getByRole('heading', { level: 1 })).toBeTruthy()
  })
})
