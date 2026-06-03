import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { SpawnGeminiModal } from '../SpawnGeminiModal'

vi.mock('../../lib/spawn', () => ({
  buildSpec: vi.fn().mockResolvedValue({ status: 'ok', agents: ['gemini-agent-1'], message: 'Spawned' }),
}))

import { buildSpec } from '../../lib/spawn'

describe('SpawnGeminiModal', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('renders the spec title', () => {
    render(
      <SpawnGeminiModal
        path="/path/to/spec.md"
        title="My Feature Spec"
        onClose={vi.fn()}
      />
    )
    expect(screen.getByText('My Feature Spec')).toBeInTheDocument()
  })

  it('shows spawn and cancel buttons', () => {
    render(
      <SpawnGeminiModal
        path="/path/to/spec.md"
        title="My Feature Spec"
        onClose={vi.fn()}
      />
    )
    expect(screen.getByTestId('spawn-agent-btn')).toBeInTheDocument()
    expect(screen.getByTestId('cancel-spawn-btn')).toBeInTheDocument()
  })

  it('calls buildSpec with model=claude by default on spawn', async () => {
    const onClose = vi.fn()
    render(
      <SpawnGeminiModal
        path="/path/to/spec.md"
        title="My Feature Spec"
        onClose={onClose}
      />
    )
    await userEvent.click(screen.getByTestId('spawn-agent-btn'))
    expect(buildSpec).toHaveBeenCalledWith(
      '/path/to/spec.md',
      undefined,
      'claude'
    )
  })

  it('calls onClose when cancel is clicked', async () => {
    const onClose = vi.fn()
    render(
      <SpawnGeminiModal
        path="/path/to/spec.md"
        title="My Feature Spec"
        onClose={onClose}
      />
    )
    await userEvent.click(screen.getByTestId('cancel-spawn-btn'))
    expect(onClose).toHaveBeenCalledOnce()
  })

  it('calls onSpawned with agent list after successful spawn', async () => {
    const onSpawned = vi.fn()
    const onClose = vi.fn()
    render(
      <SpawnGeminiModal
        path="/path/to/spec.md"
        title="My Feature Spec"
        onClose={onClose}
        onSpawned={onSpawned}
      />
    )
    await userEvent.click(screen.getByTestId('spawn-agent-btn'))
    expect(onSpawned).toHaveBeenCalledWith(['gemini-agent-1'])
  })
})
