import { existsSync, readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, it, expect, vi, beforeAll } from 'vitest'
import { render, screen } from '@testing-library/react'
import { MemoryRouter, Routes, Route } from 'react-router-dom'
import PrivacyPolicy from './PrivacyPolicy'

vi.mock('../lib/api', () => ({ api: { get: vi.fn(), post: vi.fn() } }))

// Root PRIVACY.md is the single source of truth for this page. Read it from
// disk so any future edit to PRIVACY.md that the page does not reflect makes
// this suite fail (anti-drift guarantee). Vitest runs with cwd = app/, but
// also handle a repo-root cwd for direct invocations.
const privacyMdPath = [
  resolve(process.cwd(), '..', 'PRIVACY.md'),
  resolve(process.cwd(), 'PRIVACY.md'),
].find((candidate) => existsSync(candidate))
if (!privacyMdPath) throw new Error('PRIVACY.md not found next to app/')
const privacyMd = readFileSync(privacyMdPath, 'utf8')

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
    expect(screen.getByTestId('privacy-content')).toBeTruthy()
  })

  it('shows what stays on the laptop, including the storage table', () => {
    const { container } = renderPage()
    const text = container.textContent ?? ''
    expect(text).toContain('What lives on your laptop')
    // Table content from PRIVACY.md section 1.
    expect(text).toContain('~/.youros/settings.json')
    expect(text).toContain('Theme, model preference, API keys')
  })

  it('shows what leaves the laptop', () => {
    const { container } = renderPage()
    const text = container.textContent ?? ''
    expect(text).toContain('What leaves your laptop')
    expect(text).toContain('api.anthropic.com')
  })

  it('states there is no telemetry', () => {
    const { container } = renderPage()
    expect(container.textContent).toContain('There is no telemetry.')
  })

  it('explains disconnecting a tool', () => {
    const { container } = renderPage()
    expect(container.textContent).toContain('Disconnecting a tool')
  })

  it('shows the data-flow diagram', () => {
    const { container } = renderPage()
    expect(container.textContent).toContain('Your keyboard')
    expect(container.querySelector('pre')).toBeTruthy()
  })

  it('route is reachable: MemoryRouter at /privacy renders PrivacyPolicy', () => {
    render(
      <MemoryRouter initialEntries={['/privacy']}>
        <Routes>
          <Route path="privacy" element={<PrivacyPolicy />} />
        </Routes>
      </MemoryRouter>
    )
    expect(screen.getByTestId('privacy-content')).toBeTruthy()
  })
})

describe('PrivacyPolicy stays aligned with root PRIVACY.md', () => {
  const headings = privacyMd
    .split('\n')
    .filter((line) => /^#{1,3} /.test(line))
    .map((line) => line.replace(/^#+ /, '').trim())

  it('sanity: PRIVACY.md still has its headings', () => {
    // 1 title + 4 numbered sections + 5 subsections under "What leaves"
    expect(headings.length).toBeGreaterThanOrEqual(10)
  })

  it('renders the text of every PRIVACY.md heading', () => {
    const { container } = renderPage()
    const text = container.textContent ?? ''
    for (const heading of headings) {
      expect(text, `page is missing PRIVACY.md heading: "${heading}"`).toContain(heading)
    }
  })
})
