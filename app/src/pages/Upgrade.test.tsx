import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor, fireEvent } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import Upgrade from './Upgrade'

// Mock the api module so no test ever hits the network. Each test routes
// '/upgrade/status' explicitly; anything else (TopBar polls) resolves to
// an empty object.
vi.mock('../lib/api', () => ({
  api: {
    get: vi.fn(),
    post: vi.fn(),
    put: vi.fn(),
    patch: vi.fn(),
    delete: vi.fn(),
  },
}))

import { api } from '../lib/api'

const mockedGet = vi.mocked(api.get)
const mockedPost = vi.mocked(api.post)

// jsdom does not provide window.matchMedia. Provide a minimal stub so
// components that use responsive breakpoints do not crash.
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

const upToDate = {
  youros: { current: '5.14.0', latest: '5.14.0', behind: false },
  ostk: { current: '0.9.2', latest: '0.9.2', behind: false },
}

const myosBehind = {
  youros: { current: '5.14.0', latest: '5.15.0', behind: true, commits_behind: 3 },
  ostk: { current: '0.9.2', latest: '0.9.2', behind: false },
}

const bothBehind = {
  youros: { current: '5.14.0', latest: '5.15.0', behind: true, commits_behind: 3 },
  ostk: { current: '0.9.2', latest: '0.9.4', behind: true, commits_behind: 1 },
}

function mockStatus(status: object) {
  mockedGet.mockImplementation((path: string) => {
    if (path === '/upgrade/status') return Promise.resolve(status)
    return Promise.resolve({})
  })
}

function statusCalls() {
  return mockedGet.mock.calls.filter((c) => c[0] === '/upgrade/status').length
}

function renderUpgrade() {
  return render(
    <MemoryRouter>
      <Upgrade />
    </MemoryRouter>
  )
}

describe('Upgrade page', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockedPost.mockResolvedValue({ success: true, message: 'ok' })
  })

  it('shows the checking state while the status fetch is in flight', async () => {
    let resolveStatus: (v: unknown) => void = () => {}
    mockedGet.mockImplementation((path: string) => {
      if (path === '/upgrade/status') {
        return new Promise((res) => {
          resolveStatus = res
        })
      }
      return Promise.resolve({})
    })
    renderUpgrade()
    expect(screen.getByText('Checking for updates...')).toBeInTheDocument()
    resolveStatus(upToDate)
    await waitFor(() => {
      expect(screen.queryByText('Checking for updates...')).not.toBeInTheDocument()
    })
    expect(screen.getByText('System Engine')).toBeInTheDocument()
  })

  it('shows the plain-language error when the status fetch fails', async () => {
    mockedGet.mockImplementation((path: string) => {
      if (path === '/upgrade/status') return Promise.reject(new Error('offline'))
      return Promise.resolve({})
    })
    renderUpgrade()
    await waitFor(() => {
      expect(
        screen.getByText(
          'Could not check for updates. Make sure you are connected to the internet.'
        )
      ).toBeInTheDocument()
    })
    // No component cards render in the error state.
    expect(screen.queryByText('System Engine')).not.toBeInTheDocument()
    expect(screen.queryByText(/Installed:/)).not.toBeInTheDocument()
  })

  it('renders both cards with "Up to date" badges and no update controls when current', async () => {
    mockStatus(upToDate)
    renderUpgrade()
    await waitFor(() => {
      expect(screen.getByText('System Engine')).toBeInTheDocument()
    })
    expect(screen.getAllByText('Up to date')).toHaveLength(2)
    expect(screen.queryByText('Update available')).not.toBeInTheDocument()
    expect(screen.queryByText('Update')).not.toBeInTheDocument()
    expect(screen.queryByText('Update both')).not.toBeInTheDocument()
    // Installed versions are shown
    expect(screen.getByText('5.14.0')).toBeInTheDocument()
    expect(screen.getByText('0.9.2')).toBeInTheDocument()
  })

  it('shows the update path when one component is behind', async () => {
    mockStatus(myosBehind)
    renderUpgrade()
    await waitFor(() => {
      expect(screen.getByText('Update available')).toBeInTheDocument()
    })
    // The other card stays up to date
    expect(screen.getByText('Up to date')).toBeInTheDocument()
    // The newer version and the pending-work note are shown
    expect(screen.getByText('5.15.0')).toBeInTheDocument()
    expect(screen.getByText('3 new commits available')).toBeInTheDocument()
    expect(screen.getByText('Update')).toBeInTheDocument()
    // "Update both" only appears when BOTH components are behind
    expect(screen.queryByText('Update both')).not.toBeInTheDocument()
  })

  it('uses the singular form for one pending commit', async () => {
    mockStatus({
      ...myosBehind,
      youros: { ...myosBehind.youros, commits_behind: 1 },
    })
    renderUpgrade()
    await waitFor(() => {
      expect(screen.getByText('1 new commit available')).toBeInTheDocument()
    })
  })

  it('clicking Update posts the target, disables the button while running, then shows the result', async () => {
    mockStatus(myosBehind)
    let resolveRun: (v: unknown) => void = () => {}
    mockedPost.mockImplementation(
      () =>
        new Promise((res) => {
          resolveRun = res
        })
    )
    renderUpgrade()
    await waitFor(() => expect(screen.getByText('Update')).toBeInTheDocument())

    fireEvent.click(screen.getByText('Update'))
    expect(mockedPost).toHaveBeenCalledWith('/upgrade/run', { target: 'myos' })

    // While running: the button flips to a disabled "Updating..." state.
    const updatingBtn = screen.getByText('Updating...').closest('button')
    expect(updatingBtn).toBeDisabled()

    resolveRun({ success: true, message: 'Updated yourOS to 5.15.0.' })
    await waitFor(() => {
      expect(screen.getByText('Updated yourOS to 5.15.0.')).toBeInTheDocument()
    })
    // The page refreshes the status after the run finishes.
    expect(statusCalls()).toBeGreaterThanOrEqual(2)
  })

  it('shows the plain-language failure message when the upgrade call fails', async () => {
    mockStatus(myosBehind)
    mockedPost.mockRejectedValue(new Error('boom'))
    renderUpgrade()
    await waitFor(() => expect(screen.getByText('Update')).toBeInTheDocument())
    fireEvent.click(screen.getByText('Update'))
    await waitFor(() => {
      expect(screen.getByText('Something went wrong. Please try again.')).toBeInTheDocument()
    })
  })

  it('offers "Update both" only when both are behind, and it updates both cards at once', async () => {
    mockStatus(bothBehind)
    // Never resolves: freezes the page in the running state for assertion.
    mockedPost.mockImplementation(() => new Promise(() => {}))
    renderUpgrade()
    await waitFor(() => expect(screen.getByText('Update both')).toBeInTheDocument())

    fireEvent.click(screen.getByText('Update both'))
    expect(mockedPost).toHaveBeenCalledWith('/upgrade/run', { target: 'both' })
    // Both card buttons show the running state; the combined button hides.
    expect(screen.getAllByText('Updating...')).toHaveLength(2)
    expect(screen.queryByText('Update both')).not.toBeInTheDocument()
  })

  it('the "Refresh status" control refetches the status', async () => {
    mockStatus(upToDate)
    renderUpgrade()
    await waitFor(() => expect(screen.getByText('Refresh status')).toBeInTheDocument())
    const before = statusCalls()
    fireEvent.click(screen.getByText('Refresh status'))
    await waitFor(() => {
      expect(statusCalls()).toBe(before + 1)
    })
  })
})
