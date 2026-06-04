import { describe, it, expect, vi, beforeAll } from 'vitest'
import { render, screen } from '@testing-library/react'
import { MemoryRouter, Routes, Route } from 'react-router-dom'
import PrivacyPolicy from './PrivacyPolicy'

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
      <PrivacyPolicy />
    </MemoryRouter>
  )
}

describe('PrivacyPolicy page', () => {
  it('renders without crashing', () => {
    renderPage()
    expect(screen.getByTestId('privacy-section-what-we-store')).toBeTruthy()
  })

  it('shows the "what we store" section', () => {
    renderPage()
    expect(screen.getByTestId('privacy-section-what-we-store')).toBeTruthy()
    expect(screen.getByTestId('privacy-heading-what-we-store').textContent).toContain(
      'What we store'
    )
  })

  it('shows the third-party calls section', () => {
    renderPage()
    expect(screen.getByTestId('privacy-section-third-party-calls')).toBeTruthy()
    expect(screen.getByTestId('privacy-heading-third-party-calls').textContent).toContain(
      'leaves your machine'
    )
  })

  it('shows the telemetry section', () => {
    renderPage()
    expect(screen.getByTestId('privacy-section-telemetry')).toBeTruthy()
    expect(screen.getByTestId('privacy-heading-telemetry').textContent).toContain(
      'do not collect'
    )
  })

  it('shows the user control section', () => {
    renderPage()
    expect(screen.getByTestId('privacy-section-your-control')).toBeTruthy()
    expect(screen.getByTestId('privacy-heading-your-control').textContent).toContain(
      'Your control'
    )
  })

  it('shows the contact section', () => {
    renderPage()
    expect(screen.getByTestId('privacy-section-contact')).toBeTruthy()
    expect(screen.getByTestId('privacy-heading-contact').textContent).toContain('Questions')
  })

  it('renders all five sections', () => {
    renderPage()
    const sections = [
      'what-we-store',
      'third-party-calls',
      'telemetry',
      'your-control',
      'contact',
    ]
    for (const id of sections) {
      expect(screen.getByTestId(`privacy-section-${id}`)).toBeTruthy()
    }
  })

  it('route is reachable: MemoryRouter at /privacy renders PrivacyPolicy', () => {
    render(
      <MemoryRouter initialEntries={['/privacy']}>
        <Routes>
          <Route path="privacy" element={<PrivacyPolicy />} />
        </Routes>
      </MemoryRouter>
    )
    expect(screen.getByTestId('privacy-section-what-we-store')).toBeTruthy()
    expect(screen.getByTestId('privacy-section-telemetry')).toBeTruthy()
    expect(screen.getByTestId('privacy-section-your-control')).toBeTruthy()
    expect(screen.getByTestId('privacy-section-contact')).toBeTruthy()
  })
})
