import { describe, it, expect, beforeEach } from 'vitest'
import { useWorkflowsStore } from './workflowsStore'

const WF = {
  id: 'wf-1',
  name: 'demo',
  steps: [],
  status: 'running',
  created_at: '2026-05-10T00:00:00Z',
}

describe('useWorkflowsStore', () => {
  beforeEach(() => {
    useWorkflowsStore.setState({ workflows: [] })
  })

  it('setWorkflows replaces the list', () => {
    useWorkflowsStore.getState().setWorkflows([WF])
    expect(useWorkflowsStore.getState().workflows).toHaveLength(1)
    expect(useWorkflowsStore.getState().workflows[0].id).toBe('wf-1')
  })

  it('content-equal setWorkflows keeps the same reference (→1730)', () => {
    const stable = [WF]
    useWorkflowsStore.getState().setWorkflows(stable)
    const first = useWorkflowsStore.getState().workflows
    useWorkflowsStore.getState().setWorkflows([...stable])
    expect(useWorkflowsStore.getState().workflows).toBe(first)
  })

  it('a real change swaps the reference', () => {
    useWorkflowsStore.getState().setWorkflows([WF])
    const first = useWorkflowsStore.getState().workflows
    useWorkflowsStore.getState().setWorkflows([{ ...WF, status: 'completed' }])
    expect(useWorkflowsStore.getState().workflows).not.toBe(first)
  })
})
