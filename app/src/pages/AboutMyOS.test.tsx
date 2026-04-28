import { describe, it, expect, vi, beforeAll } from 'vitest'
import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import AboutMyOS from './AboutMyOS'

vi.mock('../lib/api', () => ({ api: { get: vi.fn(), post: vi.fn() } }))

beforeAll(() => {
  Object.defineProperty(window, 'matchMedia', {
    writable: true,
    value: vi.fn().mockImplementation((query: string) => ({
      matches: false,
      media: query,
      onchange: null,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      dispatchEvent: vi.fn(),
    })),
  })
})

function renderPage() {
  return render(
    <MemoryRouter>
      <AboutMyOS />
    </MemoryRouter>
  )
}

describe('AboutMyOS page', () => {
  it('renders all six sections', () => {
    renderPage()
    const sectionIds = [
      'what-myos-is',
      'kernel-underneath',
      'how-agents-coordinate',
      'work-that-compounds',
      'local-and-model-agnostic',
      'where-to-look-next',
    ]
    for (const id of sectionIds) {
      expect(screen.getByTestId(`about-section-${id}`)).toBeTruthy()
      expect(screen.getByTestId(`about-heading-${id}`)).toBeTruthy()
    }
  })

  it('renders section headings with correct text', () => {
    renderPage()
    expect(screen.getByTestId('about-heading-what-myos-is').textContent).toContain('What myOS is')
    expect(screen.getByTestId('about-heading-kernel-underneath').textContent).toContain(
      'kernel underneath'
    )
    expect(screen.getByTestId('about-heading-how-agents-coordinate').textContent).toContain(
      'How agents coordinate'
    )
    expect(screen.getByTestId('about-heading-work-that-compounds').textContent).toContain(
      'Work that compounds'
    )
    expect(screen.getByTestId('about-heading-local-and-model-agnostic').textContent).toContain(
      'Local first'
    )
    expect(screen.getByTestId('about-heading-where-to-look-next').textContent).toContain(
      'Where to look next'
    )
  })
})
