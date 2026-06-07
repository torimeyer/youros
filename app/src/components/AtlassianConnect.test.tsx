import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor, fireEvent } from '@testing-library/react'
import AtlassianConnect from './AtlassianConnect'

vi.mock('../lib/api', async () => {
  const actual = await vi.importActual<typeof import('../lib/api')>('../lib/api')
  return {
    ...actual,
    api: {
      get: vi.fn(),
      post: vi.fn(),
      delete: vi.fn(),
    },
  }
})

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

import { api } from '../lib/api'

const mockedApiGet = vi.mocked(api.get)

describe('AtlassianConnect', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockedApiGet.mockImplementation((path: string) => {
      if (path.includes('/atlassian/defaults')) {
        return Promise.resolve({ site: '', oauth_available: true })
      }
      return Promise.resolve({})
    })
  })

  it('shows Reconnect button when connected and expired', async () => {
    mockedApiGet.mockImplementation((path: string) => {
      if (path.includes('/atlassian/status')) {
        return Promise.resolve({ connected: true, expired: true, email: 'user@example.com', site: 'example.atlassian.net' })
      }
      if (path.includes('/atlassian/defaults')) {
        return Promise.resolve({ site: '', oauth_available: true })
      }
      return Promise.resolve({})
    })

    render(<AtlassianConnect />)

    await waitFor(() => {
      expect(screen.getByTestId('atlassian-reconnect-btn')).toBeInTheDocument()
    })
    expect(screen.getByText('Reconnect')).toBeInTheDocument()
  })

  it('does not show Reconnect button when connected and not expired', async () => {
    mockedApiGet.mockImplementation((path: string) => {
      if (path.includes('/atlassian/status')) {
        return Promise.resolve({ connected: true, expired: false, email: 'user@example.com', site: 'example.atlassian.net' })
      }
      if (path.includes('/atlassian/defaults')) {
        return Promise.resolve({ site: '', oauth_available: true })
      }
      return Promise.resolve({})
    })

    render(<AtlassianConnect />)

    await waitFor(() => {
      expect(screen.getByTestId('atlassian-connect-connected')).toBeInTheDocument()
    })
    expect(screen.queryByTestId('atlassian-reconnect-btn')).not.toBeInTheDocument()
  })

  it('Reconnect button onClick navigates to /api/atlassian/auth with return_to=/settings', async () => {
    let assignedHref = ''
    Object.defineProperty(window, 'location', {
      configurable: true,
      writable: true,
      value: {
        ...window.location,
        get href() { return assignedHref },
        set href(v: string) { assignedHref = v },
      },
    })

    mockedApiGet.mockImplementation((path: string) => {
      if (path.includes('/atlassian/status')) {
        return Promise.resolve({ connected: true, expired: true, email: 'user@example.com', site: 'example.atlassian.net' })
      }
      if (path.includes('/atlassian/defaults')) {
        return Promise.resolve({ site: '', oauth_available: true })
      }
      return Promise.resolve({})
    })

    render(<AtlassianConnect />)

    await waitFor(() => {
      expect(screen.getByTestId('atlassian-reconnect-btn')).toBeInTheDocument()
    })

    fireEvent.click(screen.getByTestId('atlassian-reconnect-btn'))
    expect(assignedHref).toBe('/api/atlassian/auth?return_to=%2Fsettings')
  })

  it('shows "Connect Jira" on OAuth button when product=jira', async () => {
    mockedApiGet.mockImplementation((path: string) => {
      if (path.includes('/atlassian/status')) {
        return Promise.resolve({ connected: false })
      }
      if (path.includes('/atlassian/defaults')) {
        return Promise.resolve({ site: '', oauth_available: true })
      }
      return Promise.resolve({})
    })

    render(<AtlassianConnect product="jira" />)

    await waitFor(() => {
      expect(screen.getByTestId('atlassian-oauth-btn')).toBeInTheDocument()
    })
    expect(screen.getByTestId('atlassian-oauth-btn')).toHaveTextContent('Connect Jira')
  })

  it('shows "Connect Confluence" on OAuth button when product=confluence', async () => {
    mockedApiGet.mockImplementation((path: string) => {
      if (path.includes('/atlassian/status')) {
        return Promise.resolve({ connected: false })
      }
      if (path.includes('/atlassian/defaults')) {
        return Promise.resolve({ site: '', oauth_available: true })
      }
      return Promise.resolve({})
    })

    render(<AtlassianConnect product="confluence" />)

    await waitFor(() => {
      expect(screen.getByTestId('atlassian-oauth-btn')).toBeInTheDocument()
    })
    expect(screen.getByTestId('atlassian-oauth-btn')).toHaveTextContent('Connect Confluence')
  })

  it('Connect Atlassian button includes return_to=/settings', async () => {
    let assignedHref = ''
    Object.defineProperty(window, 'location', {
      configurable: true,
      writable: true,
      value: {
        ...window.location,
        get href() { return assignedHref },
        set href(v: string) { assignedHref = v },
      },
    })

    mockedApiGet.mockImplementation((path: string) => {
      if (path.includes('/atlassian/status')) {
        return Promise.resolve({ connected: false })
      }
      if (path.includes('/atlassian/defaults')) {
        return Promise.resolve({ site: '', oauth_available: true })
      }
      return Promise.resolve({})
    })

    render(<AtlassianConnect />)

    await waitFor(() => {
      expect(screen.getByTestId('atlassian-oauth-btn')).toBeInTheDocument()
    })

    fireEvent.click(screen.getByTestId('atlassian-oauth-btn'))
    expect(assignedHref).toBe('/api/atlassian/auth?return_to=%2Fsettings')
  })

  it('shows Jira & Confluence product labels when both sites are in status', async () => {
    mockedApiGet.mockImplementation((path: string) => {
      if (path.includes('/atlassian/status')) {
        return Promise.resolve({
          connected: true,
          email: 'user@example.com',
          site: 'example.atlassian.net',
          jira_site: 'example.atlassian.net',
          confluence_site: 'example.atlassian.net',
        })
      }
      if (path.includes('/atlassian/defaults')) return Promise.resolve({ site: '', oauth_available: true })
      return Promise.resolve({})
    })
    render(<AtlassianConnect />)
    await waitFor(() => {
      const products = screen.getByTestId('atlassian-products')
      expect(products).toHaveTextContent('Jira')
      expect(products).toHaveTextContent('Confluence')
    })
  })
})
