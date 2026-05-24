/**
 * Tests for OAuth-aware rendering of the Atlassian + GitHub setup cards,
 * and for the OnboardingWizard restore-step behavior on OAuth return.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, fireEvent, waitFor, act } from '@testing-library/react'
import { AtlassianSetupCard, GithubSetupCard } from './OnboardingWizard'
import OnboardingWizard from './OnboardingWizard'
import { useAppStore } from '../stores/app'
import { api } from '../lib/api'

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
  localStorage.clear()
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

// Connect step index in PERSONAL_STEPS_NO_FORK (TEAM_MODE_VISIBLE=false):
// ['Welcome', 'You', 'Name', 'FilesLocation', 'Profile', 'Customize', 'Theme', 'Tracking', 'Connect', 'Ready']
const CONNECT_STEP_IDX = 8

function setupWizardStore() {
  useAppStore.setState({
    onboarded: false,
    osName: 'myOS',
    darkMode: false,
    defaultChatModel: 'claude',
    instanceMode: 'personal',
    orgName: '',
    teamAccentColor: '#6366f1',
    displayOsName: () => 'myOS',
    setInstanceMode: vi.fn() as unknown as (mode: 'personal' | 'team') => void,
    setOrgName: vi.fn(),
    setAgentsLastViewed: vi.fn() as unknown as (v: string) => void,
  })
}

describe('OnboardingWizard restore-step after OAuth', () => {
  beforeEach(() => {
    window.history.pushState({}, '', '/')
    vi.clearAllMocks()
    mockGetResponses = {}
    setupWizardStore()
  })

  afterEach(() => {
    window.history.pushState({}, '', '/')
  })

  it.each([
    ['?connected=true'],
    ['?auth_success=google'],
    ['?atlassian_connected=true'],
    ['?oauth_connected=true'],
  ])('restores to saved Connect step on return param %s', async (paramStr) => {
    mockGetResponses['/settings'] = { onboarding_step: CONNECT_STEP_IDX }
    window.history.pushState({}, '', `/${paramStr}`)

    render(<OnboardingWizard />)

    await waitFor(() => {
      expect(screen.getByTestId('step-connect')).toBeTruthy()
    })
    expect(screen.queryByTestId('step-welcome')).toBeNull()
    expect(vi.mocked(api.patch)).toHaveBeenCalledWith('/settings', { onboarding_step: null })
  })

  it('shows error banner on ?error=token_exchange_failed and clears saved step', async () => {
    window.history.pushState({}, '', '/?error=token_exchange_failed')

    render(<OnboardingWizard />)

    await waitFor(() => {
      expect(screen.getByTestId('oauth-error-banner')).toBeTruthy()
    })
    const banner = screen.getByTestId('oauth-error-banner')
    expect(banner.textContent).toContain('token_exchange_failed')
    expect(vi.mocked(api.patch)).toHaveBeenCalledWith('/settings', { onboarding_step: null })
  })

  it('restores to step 9 from backend settings on OAuth return', async () => {
    mockGetResponses['/settings'] = { onboarding_step: 9 }
    window.history.pushState({}, '', '/?connected=true')

    render(<OnboardingWizard />)

    await waitFor(() => {
      // step 9 = 'Ready' in PERSONAL_STEPS_NO_FORK (0-indexed)
      expect(screen.getByTestId('step-ready')).toBeTruthy()
    })
    expect(screen.queryByTestId('step-welcome')).toBeNull()
    expect(vi.mocked(api.patch)).toHaveBeenCalledWith('/settings', { onboarding_step: null })
  })

  it('does not advance step when settings has no onboarding_step', async () => {
    mockGetResponses['/settings'] = {}
    window.history.pushState({}, '', '/?connected=true')

    render(<OnboardingWizard />)

    await waitFor(() => {
      expect(screen.getByTestId('step-welcome')).toBeTruthy()
    })
    expect(screen.queryByTestId('step-connect')).toBeNull()
    expect(vi.mocked(api.patch)).not.toHaveBeenCalledWith('/settings', { onboarding_step: null })
  })
})
