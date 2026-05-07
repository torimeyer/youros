/**
 * Tests for OAuth-aware rendering of the Atlassian + GitHub setup cards.
 *
 * When /atlassian/defaults reports oauth_available=true, the expanded
 * card shows the OAuth button as the primary path with a "Use a token
 * instead" link to fall back to PAT. When oauth_available=false (no
 * client_id env var configured), the existing PAT form is the only
 * path. The PAT form's own behavior is covered elsewhere; here we
 * verify the branching only.
 */

import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor, act } from '@testing-library/react'
import { AtlassianSetupCard, GithubSetupCard } from './OnboardingWizard'

type ApiResponse = Record<string, unknown>

let mockGetResponses: Record<string, ApiResponse> = {}

vi.mock('../lib/api', () => ({
  api: {
    get: vi.fn((path: string) => {
      const match = mockGetResponses[path]
      if (match !== undefined) return Promise.resolve(match)
      return Promise.resolve({})
    }),
    post: vi.fn().mockResolvedValue({}),
    put: vi.fn().mockResolvedValue({}),
    patch: vi.fn().mockResolvedValue({}),
    delete: vi.fn().mockResolvedValue({}),
  },
}))

beforeEach(() => {
  mockGetResponses = {}
})

const cardProps = {
  darkMode: true,
  inputCls: 'border-slate-700',
  subtextCls: 'text-slate-400',
}

describe('AtlassianSetupCard OAuth branching', () => {
  it('shows the OAuth button when /atlassian/defaults returns oauth_available=true', async () => {
    mockGetResponses = {
      '/atlassian/status': { connected: false },
      '/atlassian/defaults': { site: '', email: '', oauth_available: true },
    }
    render(<AtlassianSetupCard {...cardProps} />)

    const setupBtn = await screen.findByTestId('onboarding-atlassian-setup')
    await act(async () => { fireEvent.click(setupBtn) })

    await waitFor(() => {
      expect(screen.getByTestId('onboarding-atlassian-oauth')).toBeTruthy()
    })
    expect(screen.queryByTestId('onboarding-atlassian-token')).toBeNull()
    expect(screen.getByTestId('onboarding-atlassian-use-token')).toBeTruthy()
  })

  it('falls through to the PAT form when oauth_available=false', async () => {
    mockGetResponses = {
      '/atlassian/status': { connected: false },
      '/atlassian/defaults': { site: '', email: '', oauth_available: false },
    }
    render(<AtlassianSetupCard {...cardProps} />)

    const setupBtn = await screen.findByTestId('onboarding-atlassian-setup')
    await act(async () => { fireEvent.click(setupBtn) })

    await waitFor(() => {
      expect(screen.getByTestId('onboarding-atlassian-token')).toBeTruthy()
    })
    expect(screen.queryByTestId('onboarding-atlassian-oauth')).toBeNull()
  })

})

describe('GithubSetupCard OAuth branching', () => {
  it('shows the OAuth button when /github/defaults returns oauth_available=true', async () => {
    mockGetResponses = {
      '/github/status': { connected: false },
      '/github/defaults': { oauth_available: true },
    }
    render(<GithubSetupCard {...cardProps} />)

    const setupBtn = await screen.findByTestId('onboarding-github-setup')
    await act(async () => { fireEvent.click(setupBtn) })

    await waitFor(() => {
      expect(screen.getByTestId('onboarding-github-oauth')).toBeTruthy()
    })
    expect(screen.queryByTestId('onboarding-github-token')).toBeNull()
    expect(screen.getByTestId('onboarding-github-use-token')).toBeTruthy()
  })

  it('falls through to the PAT form when oauth_available=false', async () => {
    mockGetResponses = {
      '/github/status': { connected: false },
      '/github/defaults': { oauth_available: false },
    }
    render(<GithubSetupCard {...cardProps} />)

    const setupBtn = await screen.findByTestId('onboarding-github-setup')
    await act(async () => { fireEvent.click(setupBtn) })

    await waitFor(() => {
      expect(screen.getByTestId('onboarding-github-token')).toBeTruthy()
    })
    expect(screen.queryByTestId('onboarding-github-oauth')).toBeNull()
  })
})
