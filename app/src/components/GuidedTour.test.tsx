import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, fireEvent, act } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import GuidedTour from './GuidedTour'

const mockNavigate = vi.fn()

vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual('react-router-dom')
  return { ...actual, useNavigate: () => mockNavigate }
})

vi.mock('../stores/app', () => ({
  useAppStore: (selector: (s: { setTourComplete: (v: boolean) => void }) => unknown) =>
    selector({ setTourComplete: vi.fn() }),
}))

// Helper: place a fake target element in the DOM so the tour can anchor to it
function placeTarget(selector: string, rect: Partial<DOMRect>) {
  // strip brackets and equals to build attributes
  const match = selector.match(/^\[([^=]+)="([^"]+)"\]$/)
  const el = document.createElement('div')
  if (match) {
    el.setAttribute(match[1], match[2])
  } else {
    // anchor-style selector like a[href="/activity"]
    const anchorMatch = selector.match(/^a\[([^=]+)="([^"]+)"\]$/)
    if (anchorMatch) {
      const a = document.createElement('a')
      a.setAttribute(anchorMatch[1], anchorMatch[2])
      document.body.appendChild(a)
      a.getBoundingClientRect = () => ({
        ...defaultRect(rect),
      }) as DOMRect
      return a
    }
  }
  document.body.appendChild(el)
  el.getBoundingClientRect = () => defaultRect(rect) as DOMRect
  // scrollIntoView is not implemented in jsdom
  el.scrollIntoView = vi.fn()
  return el
}

function defaultRect(overrides: Partial<DOMRect>): DOMRect {
  const r = {
    x: 0, y: 0, top: 0, left: 0, right: 100, bottom: 100,
    width: 100, height: 100, toJSON: () => ({}),
    ...overrides,
  }
  return r as DOMRect
}

describe('GuidedTour', () => {
  beforeEach(() => {
    mockNavigate.mockClear()
    // set a viewport
    Object.defineProperty(window, 'innerWidth', { value: 1200, writable: true, configurable: true })
    Object.defineProperty(window, 'innerHeight', { value: 800, writable: true, configurable: true })
  })

  afterEach(() => {
    document.body.innerHTML = ''
    vi.useRealTimers()
  })

  function renderTour() {
    return render(
      <MemoryRouter>
        <GuidedTour onComplete={vi.fn()} />
      </MemoryRouter>
    )
  }

  it('renders the first step with title Dashboard', () => {
    renderTour()
    expect(screen.getByText('Dashboard')).toBeInTheDocument()
    expect(screen.getByText(/1 of/)).toBeInTheDocument()
  })

  it('Chat step uses neutral copy that names both models', () => {
    renderTour()
    // Walk steps to find the Chat one. We assert via the static TOUR text
    // being rendered correctly when we reach it.
    // Step 1 is Dashboard. Click Next through steps until we see Chat.
    for (let i = 0; i < 10; i++) {
      if (screen.queryByText('Chat')) break
      const nextBtn = screen.queryByRole('button', { name: /Next/ })
      if (!nextBtn) break
      fireEvent.click(nextBtn)
    }
    const chatDesc = screen.queryByText(/@claude.*@gemini|@gemini.*@claude/)
    expect(chatDesc).toBeTruthy()
    // And must NOT contain the old confusing copy
    expect(screen.queryByText(/Use @gemini to switch models\.$/)).toBeNull()
  })

  it('clamps tooltip position within the viewport even when target is near left edge', async () => {
    vi.useFakeTimers()
    // Target near left edge, far below top so bottom-positioned tooltip would start near x=0
    placeTarget('[data-tour="dashboard"]', { left: 0, right: 40, top: 100, bottom: 140, width: 40, height: 40 })
    renderTour()
    // Flush the rAF + setTimeout retry
    await act(async () => {
      vi.advanceTimersByTime(100)
    })
    const tooltip = screen.getByTestId('tour-tooltip')
    const leftPx = parseFloat((tooltip as HTMLElement).style.left)
    const topPx = parseFloat((tooltip as HTMLElement).style.top)
    // Tooltip width is 320, viewport is 1200. Left must be >= 16.
    expect(leftPx).toBeGreaterThanOrEqual(16)
    // And within viewport
    expect(leftPx + 320).toBeLessThanOrEqual(1200 - 16)
    expect(topPx).toBeGreaterThanOrEqual(16)
    expect(topPx + 260).toBeLessThanOrEqual(800 - 16)
  })

  it('clamps tooltip when target is near right edge', async () => {
    vi.useFakeTimers()
    // viewport 1200, target at far right
    placeTarget('[data-tour="dashboard"]', { left: 1180, right: 1200, top: 100, bottom: 140, width: 20, height: 40 })
    renderTour()
    await act(async () => {
      vi.advanceTimersByTime(100)
    })
    const tooltip = screen.getByTestId('tour-tooltip')
    const leftPx = parseFloat((tooltip as HTMLElement).style.left)
    expect(leftPx).toBeGreaterThanOrEqual(16)
    expect(leftPx + 320).toBeLessThanOrEqual(1200 - 16)
  })

  it('renders the Specs step as part of the tour', () => {
    renderTour()
    // Click through steps until Specs appears
    let seenSpecs = false
    for (let i = 0; i < 10; i++) {
      if (screen.queryByRole('heading', { name: 'Specs' })) {
        seenSpecs = true
        break
      }
      const nextBtn = screen.queryByRole('button', { name: /Next/ })
      if (!nextBtn) break
      fireEvent.click(nextBtn)
    }
    expect(seenSpecs).toBe(true)
  })

  it('keeps the tooltip below top edge when the anchor fills the page (Specs/Tasks)', async () => {
    vi.useFakeTimers()
    // Simulate a whole-page content container like data-tour="specs" or
    // data-tour="tasks". Its bounding rect covers the entire viewport and
    // extends well below it. Preferred position is 'bottom', which would
    // place the tooltip way off the bottom of the viewport. The fix must
    // flip it so the tooltip stays fully on screen with its top >= 16.
    placeTarget('[data-tour="dashboard"]', {
      left: 0,
      right: 1200,
      top: 0,
      bottom: 2000,
      width: 1200,
      height: 2000,
    })
    renderTour()
    await act(async () => {
      vi.advanceTimersByTime(100)
    })
    const tooltip = screen.getByTestId('tour-tooltip') as HTMLElement
    const topPx = parseFloat(tooltip.style.top)
    // The fix must ensure the top edge is on screen with the 16px margin.
    expect(topPx).toBeGreaterThanOrEqual(16)
    // And the bottom edge must also be on screen (tooltip is <= 260px tall
    // in jsdom because the fallback measurement is what the test sees).
    expect(topPx + 260).toBeLessThanOrEqual(800 - 16)
  })
})
