import { describe, it, expect, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { RecentAgentActions } from '../RecentAgentActions'
import type { AgentInfo } from '../../lib/agentUtils'

function makeAgent(template: string): AgentInfo {
  return {
    name: 'test-agent',
    status: 'completed',
    source: 'user',
    template,
  }
}

describe('RecentAgentActions — template button keys', () => {
  it('shows Replan for "Meal Planner" template (lowercased contains "planner")', () => {
    render(<RecentAgentActions agent={makeAgent('Meal Planner')} />)
    expect(screen.getByTestId('action-replan')).toBeInTheDocument()
    expect(screen.getByTestId('action-run-again')).toBeInTheDocument()
    expect(screen.queryByTestId('action-save-to-gems')).not.toBeInTheDocument()
  })

  it('shows Replan for "Trip Planner" template', () => {
    render(<RecentAgentActions agent={makeAgent('Trip Planner')} />)
    expect(screen.getByTestId('action-replan')).toBeInTheDocument()
  })

  it('shows Replan for a template containing "planning"', () => {
    render(<RecentAgentActions agent={makeAgent('Sprint Planning')} />)
    expect(screen.getByTestId('action-replan')).toBeInTheDocument()
  })

  it('shows Save to gems for "gem-builder" template', () => {
    render(<RecentAgentActions agent={makeAgent('gem-builder')} />)
    expect(screen.getByTestId('action-save-to-gems')).toBeInTheDocument()
    expect(screen.getByTestId('action-run-again')).toBeInTheDocument()
    expect(screen.queryByTestId('action-replan')).not.toBeInTheDocument()
  })

  it('shows only Run again for a non-template agent', () => {
    render(<RecentAgentActions agent={makeAgent('Builder')} />)
    expect(screen.getByTestId('action-run-again')).toBeInTheDocument()
    expect(screen.queryByTestId('action-replan')).not.toBeInTheDocument()
    expect(screen.queryByTestId('action-save-to-gems')).not.toBeInTheDocument()
  })

  it('renders nothing when template is empty', () => {
    const { container } = render(<RecentAgentActions agent={makeAgent('')} />)
    expect(container.firstChild).toBeNull()
  })

  it('calls onRunAgain when Replan is clicked', async () => {
    const onRunAgain = vi.fn()
    render(<RecentAgentActions agent={makeAgent('Meal Planner')} onRunAgain={onRunAgain} />)
    await userEvent.click(screen.getByTestId('action-replan'))
    expect(onRunAgain).toHaveBeenCalledOnce()
    const [, template] = onRunAgain.mock.calls[0]
    expect(template).toBe('Meal Planner')
  })

  it('calls onSaveGem when Save to gems is clicked', async () => {
    const onSaveGem = vi.fn()
    render(<RecentAgentActions agent={makeAgent('gem-builder')} onSaveGem={onSaveGem} />)
    await userEvent.click(screen.getByTestId('action-save-to-gems'))
    expect(onSaveGem).toHaveBeenCalledWith('test-agent')
  })
})
