import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import AdminStarterPack from './StarterPack'

vi.mock('../../lib/api', () => ({
  api: {
    get: vi.fn(),
    put: vi.fn(),
  },
}))

vi.mock('../../stores/app', () => ({
  useAppStore: (selector: (s: { darkMode: boolean }) => unknown) => selector({ darkMode: false }),
}))

vi.mock('../../components/Icon', () => ({
  default: ({ name }: { name: string }) => <span data-testid={`icon-${name}`} />,
}))

import { api } from '../../lib/api'
const mockedApi = vi.mocked(api)

const SAMPLE_PACK = {
  agentfiles: ['builtin-builder', 'builtin-research'],
  skills: ['review'],
  suggested_first_runs: [],
}

describe('AdminStarterPack', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockedApi.get.mockResolvedValue(SAMPLE_PACK)
    mockedApi.put.mockResolvedValue(SAMPLE_PACK)
  })

  it('renders agents from the loaded pack', async () => {
    render(<AdminStarterPack />)
    await waitFor(() => {
      expect(screen.getByText('builtin-builder')).toBeTruthy()
      expect(screen.getByText('builtin-research')).toBeTruthy()
    })
  })

  it('renders skills from the loaded pack', async () => {
    render(<AdminStarterPack />)
    await waitFor(() => {
      expect(screen.getByText('review')).toBeTruthy()
    })
  })

  it('calls PUT with pack data when Save changes is clicked', async () => {
    const user = userEvent.setup()
    render(<AdminStarterPack />)

    await waitFor(() => expect(screen.getByText('builtin-builder')).toBeTruthy())

    await user.click(screen.getByText('Save changes'))

    await waitFor(() => {
      expect(mockedApi.put).toHaveBeenCalledWith(
        '/onboarding/org-pack',
        expect.objectContaining({ agentfiles: expect.arrayContaining(['builtin-builder']) }),
      )
    })
  })

  it('adds an agent when Add is clicked', async () => {
    const user = userEvent.setup()
    render(<AdminStarterPack />)

    await waitFor(() => expect(screen.getByText('builtin-builder')).toBeTruthy())

    await user.type(screen.getByLabelText('Add agent'), 'builtin-diagnose')
    await user.click(screen.getAllByText('Add')[0])

    await waitFor(() => {
      expect(screen.getByText('builtin-diagnose')).toBeTruthy()
    })
  })

  it('removes an agent when its remove button is clicked', async () => {
    const user = userEvent.setup()
    render(<AdminStarterPack />)

    await waitFor(() => expect(screen.getByText('builtin-research')).toBeTruthy())

    await user.click(screen.getByLabelText('Remove builtin-research'))

    await waitFor(() => {
      expect(screen.queryByText('builtin-research')).toBeNull()
    })
  })
})
