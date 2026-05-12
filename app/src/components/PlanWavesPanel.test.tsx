import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import PlanWavesPanel from './PlanWavesPanel'

vi.mock('../lib/api', async () => {
  const actual = await vi.importActual<typeof import('../lib/api')>('../lib/api')
  return {
    ...actual,
    api: {
      get: vi.fn(),
      post: vi.fn(),
    },
  }
})

import { api } from '../lib/api'
const mockedApiGet = vi.mocked(api.get)

const LONG_DESC =
  'This is a very long description that goes on and on and on. ' +
  'It has multiple sentences. It describes a complex feature with many moving parts. ' +
  'This is more text to ensure it exceeds two lines in any reasonable viewport. ' +
  'Even more text to be sure.'

const SEPARATOR_DESC = 'First section⊕Second section⊕Third section with more detail here.'

function makeWavesData(description: string = LONG_DESC) {
  return {
    waves: [
      {
        wave: 1,
        needles: [
          {
            id: '42',
            title: 'Build the widget',
            priority: 'P1',
            scope_hint: 'frontend',
            description,
          },
        ],
        blocked_by_prior: false,
      },
    ],
    total_needles: 1,
  }
}

function renderPanel(open = true) {
  return render(<PlanWavesPanel open={open} onClose={vi.fn()} />)
}

beforeEach(() => {
  vi.resetAllMocks()
})

describe('PlanWavesPanel', () => {
  it('renders title and priority badge for a needle', async () => {
    mockedApiGet.mockResolvedValueOnce(makeWavesData())
    renderPanel()
    await waitFor(() => screen.getByTestId('wave-needle-42'))
    expect(screen.getByText('Build the widget')).toBeInTheDocument()
    expect(screen.getByText('P1')).toBeInTheDocument()
  })

  it('description container has line-clamp-2 class by default', async () => {
    mockedApiGet.mockResolvedValueOnce(makeWavesData())
    renderPanel()
    await waitFor(() => screen.getByTestId('needle-description-42'))
    const desc = screen.getByTestId('needle-description-42')
    expect(desc.className).toContain('line-clamp-2')
  })

  it('clicking Show more removes line-clamp-2 and shows full text', async () => {
    mockedApiGet.mockResolvedValueOnce(makeWavesData())
    renderPanel()
    await waitFor(() => screen.getByTestId('needle-description-42'))
    const showMoreBtn = screen.getByRole('button', { name: 'Show more' })
    fireEvent.click(showMoreBtn)
    const desc = screen.getByTestId('needle-description-42')
    expect(desc.className).not.toContain('line-clamp-2')
    expect(screen.getByRole('button', { name: 'Show less' })).toBeInTheDocument()
  })

  it('clicking Show less collapses description back to clamped', async () => {
    mockedApiGet.mockResolvedValueOnce(makeWavesData())
    renderPanel()
    await waitFor(() => screen.getByTestId('needle-description-42'))
    fireEvent.click(screen.getByRole('button', { name: 'Show more' }))
    fireEvent.click(screen.getByRole('button', { name: 'Show less' }))
    expect(screen.getByTestId('needle-description-42').className).toContain('line-clamp-2')
  })

  it('strips ⊕ separator characters from the description', async () => {
    mockedApiGet.mockResolvedValueOnce(makeWavesData(SEPARATOR_DESC))
    renderPanel()
    await waitFor(() => screen.getByTestId('needle-description-42'))
    const desc = screen.getByTestId('needle-description-42')
    expect(desc.textContent).not.toContain('⊕')
    expect(desc.textContent).toContain('First section')
    expect(desc.textContent).toContain('Second section')
  })

  it('does not render description element when description is empty', async () => {
    mockedApiGet.mockResolvedValueOnce(makeWavesData(''))
    renderPanel()
    await waitFor(() => screen.getByTestId('wave-needle-42'))
    expect(screen.queryByTestId('needle-description-42')).not.toBeInTheDocument()
  })

  it('shows loading state while fetching', () => {
    mockedApiGet.mockReturnValueOnce(new Promise(() => {}))
    renderPanel()
    expect(screen.getByTestId('plan-waves-loading')).toBeInTheDocument()
  })

  it('shows error when fetch fails', async () => {
    mockedApiGet.mockRejectedValueOnce(new Error('Network error'))
    renderPanel()
    await waitFor(() => screen.getByTestId('plan-waves-error'))
    expect(screen.getByTestId('plan-waves-error').textContent).toContain('Network error')
  })

  it('renders nothing when open is false', () => {
    renderPanel(false)
    expect(screen.queryByTestId('plan-waves-panel')).not.toBeInTheDocument()
    expect(mockedApiGet).not.toHaveBeenCalled()
  })
})
