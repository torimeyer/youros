import { api, ApiError } from './api'

export interface ConflictItem {
  requested: string
  held_by_spawn: string
  held_path: string
}

export type BuildResult =
  | { status: 'ok'; agents: string[]; message: string; has_unchecked_acs?: boolean }
  | { status: 'conflict'; conflicts: ConflictItem[] }

interface BuildResponse {
  agents: string[]
  message: string
  has_unchecked_acs?: boolean
}

export interface BuildCallbacks {
  onOptimisticStart?: () => void
  onSuccess?: (result: BuildResult) => void
  onError?: (err: Error) => void
}

export async function buildSpec(
  path: string,
  callbacks?: BuildCallbacks,
  model?: 'claude' | 'gemini'
): Promise<BuildResult> {
  callbacks?.onOptimisticStart?.()
  try {
    const encodedPath = encodeURIComponent(path)
    const modelParam = model ? `?model=${model}` : ''
    const res = await api.post<BuildResponse>(`/specs/${encodedPath}/build${modelParam}`)
    const result: BuildResult = {
      status: 'ok',
      agents: res.agents || [],
      message: res.message || '',
      has_unchecked_acs: res.has_unchecked_acs,
    }
    callbacks?.onSuccess?.(result)
    return result
  } catch (err) {
    if (err instanceof ApiError && err.status === 409) {
      const data = err.response.data as Record<string, unknown>
      if (data.error === 'lock_conflict') {
        const conflicts = (data.conflicts || []) as ConflictItem[]
        return { status: 'conflict', conflicts }
      }
    }
    if (err instanceof Error) {
      callbacks?.onError?.(err)
    }
    throw err
  }
}
