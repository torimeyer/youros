import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { SpawnAgentModal } from './SpawnAgentModal'
import * as spawn from '../lib/spawn'

vi.mock('../lib/spawn', () => ({
  buildSpec: vi.fn(),
}))

const mockedBuildSpec = vi.mocked(spawn.buildSpec)

function renderModal(overrides: Partial<Parameters<typeof SpawnAgentModal>[0]> = {}) {
  const props = {
    path: 'docs/spec/my-feature.md',
    title: 'My Feature',
    onClose: vi.fn(),
    onSpawned: vi.fn(),
    ...overrides,
  }
  return { ...render(<SpawnAgentModal {...props} />), props }
}

describe('SpawnAgentModal', () => {
  beforeEach(() => {
    mockedBuildSpec.mockReset()
  })

  it('renders with neutral title "Spawn agent"', () => {
    renderModal()
    expect(screen.getByText('Spawn agent')).toBeInTheDocument()
  })

  it('shows Claude and Gemini toggle options', () => {
    renderModal()
    expect(screen.getByLabelText(/Claude/i)).toBeInTheDocument()
    expect(screen.getByLabelText(/Gemini/i)).toBeInTheDocument()
  })

  it('defaults to Claude selected', () => {
    renderModal()
    const claudeInput = screen.getByLabelText(/Claude/i) as HTMLInputElement
    const geminiInput = screen.getByLabelText(/Gemini/i) as HTMLInputElement
    expect(claudeInput.checked).toBe(true)
    expect(geminiInput.checked).toBe(false)
  })

  it('confirm button label says "Spawn Claude agent" by default', () => {
    renderModal()
    expect(screen.getByTestId('spawn-agent-btn')).toHaveTextContent('Spawn Claude agent')
  })

  it('switching to Gemini updates confirm button label', () => {
    renderModal()
    fireEvent.click(screen.getByLabelText(/Gemini/i))
    expect(screen.getByTestId('spawn-agent-btn')).toHaveTextContent('Spawn Gemini agent')
  })

  it('calls buildSpec with model=claude when Claude is selected and confirmed', async () => {
    mockedBuildSpec.mockResolvedValue({ status: 'ok', agents: ['agent-1'], message: 'done' })
    const { props } = renderModal()
    fireEvent.click(screen.getByTestId('spawn-agent-btn'))
    await waitFor(() => expect(mockedBuildSpec).toHaveBeenCalledWith(
      'docs/spec/my-feature.md',
      undefined,
      'claude'
    ))
    await waitFor(() => expect(props.onSpawned).toHaveBeenCalledWith(['agent-1']))
  })

  it('calls buildSpec with model=gemini when Gemini is selected and confirmed', async () => {
    mockedBuildSpec.mockResolvedValue({ status: 'ok', agents: ['agent-2'], message: 'done' })
    const { props } = renderModal()
    fireEvent.click(screen.getByLabelText(/Gemini/i))
    fireEvent.click(screen.getByTestId('spawn-agent-btn'))
    await waitFor(() => expect(mockedBuildSpec).toHaveBeenCalledWith(
      'docs/spec/my-feature.md',
      undefined,
      'gemini'
    ))
    await waitFor(() => expect(props.onSpawned).toHaveBeenCalledWith(['agent-2']))
  })

  it('shows readiness checks when provided', () => {
    renderModal({ checks: [{ name: 'ac', detail: 'All ACs checked', passed: true }] })
    expect(screen.getByText('All ACs checked')).toBeInTheDocument()
  })

  it('shows conflict error message if buildSpec returns conflict', async () => {
    mockedBuildSpec.mockResolvedValue({
      status: 'conflict',
      conflicts: [{ requested: 'file.md', held_by_spawn: 'other-agent', held_path: 'file.md' }],
    })
    renderModal()
    fireEvent.click(screen.getByTestId('spawn-agent-btn'))
    await waitFor(() => expect(screen.getByText(/Lock conflict/i)).toBeInTheDocument())
  })

  it('cancel button calls onClose', () => {
    const { props } = renderModal()
    fireEvent.click(screen.getByTestId('cancel-spawn-btn'))
    expect(props.onClose).toHaveBeenCalled()
  })
})
