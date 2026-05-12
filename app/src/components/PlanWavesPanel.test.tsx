import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import PlanWavesPanel from './PlanWavesPanel'
import { api } from '../lib/api'

vi.mock('../lib/api', () => ({
  api: { get: vi.fn() },
}))

const mockedApiGet = vi.mocked(api.get)

const makeWavesResponse = (scopeHint: string) => ({
  waves: [
    {
      wave: 1,
      blocked_by_prior: false,
      needles: [
        {
          id: 'needle-1',
          title: 'Fix the thing',
          priority: 'P1',
          scope_hint: scopeHint,
        },
      ],
    },
  ],
  total_needles: 1,
})

beforeEach(() => {
  vi.clearAllMocks()
})

describe('PlanWavesPanel — needle title rendering', () => {
  it('strips ⊕-appended body from needle title', async () => {
    mockedApiGet.mockResolvedValue({
      waves: [
        {
          wave: 1,
          blocked_by_prior: false,
          needles: [
            {
              id: 'needle-title-clamp',
              title: 'Onboarding: three options ⊕ Open design question: should we do X or Y?',
              priority: 'P2',
              scope_hint: 'general',
            },
          ],
        },
      ],
      total_needles: 1,
    })
    render(<PlanWavesPanel open={true} onClose={() => {}} />)

    await waitFor(() => screen.getByTestId('wave-needle-needle-title-clamp'))

    const needle = screen.getByTestId('wave-needle-needle-title-clamp')
    const titleEl = needle.querySelector('p.text-slate-200')
    expect(titleEl).not.toBeNull()
    expect(titleEl!.textContent).toBe('Onboarding: three options')
    expect(titleEl!.textContent).not.toContain('⊕')
    expect(titleEl!.textContent).not.toContain('Open design question')
    expect(titleEl!.className).toContain('line-clamp-2')
  })
})

describe('PlanWavesPanel — scope_hint rendering', () => {
  it('strips ⊕ separator characters before display', async () => {
    mockedApiGet.mockResolvedValue(
      makeWavesResponse('frontend/src ⊕ backend/api ⊕ tests/unit'),
    )
    render(<PlanWavesPanel open={true} onClose={() => {}} />)

    await waitFor(() => screen.getByTestId('wave-needle-needle-1'))

    const hint = screen.getByTestId('wave-needle-needle-1').querySelector('p.text-slate-500')
    expect(hint).not.toBeNull()
    expect(hint!.textContent).not.toContain('⊕')
    expect(hint!.textContent).toContain('frontend/src')
    expect(hint!.textContent).toContain('backend/api')
  })

  it('applies line-clamp-2 class to scope_hint element', async () => {
    mockedApiGet.mockResolvedValue(
      makeWavesResponse('some long description text that would wrap over multiple lines'),
    )
    render(<PlanWavesPanel open={true} onClose={() => {}} />)

    await waitFor(() => screen.getByTestId('wave-needle-needle-1'))

    const needle = screen.getByTestId('wave-needle-needle-1')
    const hint = needle.querySelector('p.text-slate-500')
    expect(hint).not.toBeNull()
    expect(hint!.className).toContain('line-clamp-2')
  })
})
