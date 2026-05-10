import { create } from 'zustand'

export interface WorkflowStepSnapshot {
  id: string
  agent_name: string
  prompt: string
  model: string
  budget: number
  depends_on: string[]
  status: string
  started_at?: string
  finished_at?: string
  error?: string
}

export interface WorkflowSnapshot {
  id: string
  name: string
  steps: WorkflowStepSnapshot[]
  status: string
  created_at: string
  completed_at?: string | null
}

interface WorkflowsState {
  workflows: WorkflowSnapshot[]
  setWorkflows: (workflows: WorkflowSnapshot[]) => void
}

export const useWorkflowsStore = create<WorkflowsState>((set) => ({
  workflows: [],
  setWorkflows: (workflows) => set({ workflows }),
}))
