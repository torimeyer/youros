import { describe, it, expect, vi, beforeEach } from 'vitest'

vi.mock('./api', () => {
  class ApiError extends Error {
    status: number
    response: { status: number; data: Record<string, unknown> }
    constructor(status: number, data: Record<string, unknown>) {
      super('API Error')
      this.name = 'ApiError'
      this.status = status
      this.response = { status, data }
    }
  }
  return {
    api: { get: vi.fn(), post: vi.fn() },
    ApiError,
  }
})

import { buildSpec, type ConflictItem } from './spawn'
import { api, ApiError } from './api'

const mockedGet = vi.mocked(api.get)
const mockedPost = vi.mocked(api.post)

beforeEach(() => {
  vi.clearAllMocks()
  mockedGet.mockResolvedValue({ conflicts: [] })
})

describe('buildSpec — success path', () => {
  it('returns status:ok with agents and message on 200', async () => {
    mockedPost.mockResolvedValue({
      agents: ['builder-1', 'builder-2'],
      message: 'Started 2 agents.',
      has_unchecked_acs: true,
    })
    const result = await buildSpec('docs/spec/my-feature.md')
    expect(result).toEqual({
      status: 'ok',
      agents: ['builder-1', 'builder-2'],
      message: 'Started 2 agents.',
      has_unchecked_acs: true,
    })
  })

  it('encodes the path in the URL', async () => {
    mockedPost.mockResolvedValue({ agents: [], message: '' })
    await buildSpec('docs/spec/my feature.md')
    expect(mockedPost).toHaveBeenCalledWith('/specs/docs%2Fspec%2Fmy%20feature.md/build')
  })

  it('calls onOptimisticStart before the request resolves', async () => {
    const onOptimisticStart = vi.fn()
    let resolvePost!: (v: unknown) => void
    mockedPost.mockImplementation(
      () => new Promise((r) => { resolvePost = r })
    )
    const promise = buildSpec('docs/spec/my-feature.md', { onOptimisticStart })
    expect(onOptimisticStart).toHaveBeenCalledTimes(1)
    await Promise.resolve() // flush preflight api.get microtask so api.post is called
    resolvePost({ agents: [], message: '' })
    await promise
  })

  it('calls onSuccess with the result', async () => {
    const onSuccess = vi.fn()
    mockedPost.mockResolvedValue({ agents: ['a'], message: 'ok' })
    const result = await buildSpec('docs/spec/foo.md', { onSuccess })
    expect(onSuccess).toHaveBeenCalledWith(result)
  })
})

describe('buildSpec — 409 conflict path', () => {
  it('returns status:conflict instead of throwing on 409 lock_conflict', async () => {
    const conflicts: ConflictItem[] = [
      { requested: 'docs/spec/foo.md', held_by_spawn: 'agent-abc', held_path: 'docs/spec/foo.md' },
    ]
    mockedPost.mockRejectedValue(
      new ApiError(409, { error: 'lock_conflict', conflicts })
    )
    const result = await buildSpec('docs/spec/foo.md')
    expect(result).toEqual({ status: 'conflict', conflicts })
  })

  it('does NOT call onError on 409 lock_conflict', async () => {
    const onError = vi.fn()
    mockedPost.mockRejectedValue(
      new ApiError(409, { error: 'lock_conflict', conflicts: [] })
    )
    await buildSpec('docs/spec/foo.md', { onError })
    expect(onError).not.toHaveBeenCalled()
  })

  it('includes an empty conflicts array if backend omits it', async () => {
    mockedPost.mockRejectedValue(
      new ApiError(409, { error: 'lock_conflict' })
    )
    const result = await buildSpec('docs/spec/foo.md')
    expect(result).toEqual({ status: 'conflict', conflicts: [] })
  })
})

describe('buildSpec — error path', () => {
  it('calls onError and re-throws on non-409 errors', async () => {
    const onError = vi.fn()
    const err = new ApiError(500, { detail: 'Server exploded' })
    mockedPost.mockRejectedValue(err)
    await expect(buildSpec('docs/spec/foo.md', { onError })).rejects.toThrow()
    expect(onError).toHaveBeenCalledWith(err)
  })

  it('re-throws on 409 with a different error key (not lock_conflict)', async () => {
    mockedPost.mockRejectedValue(
      new ApiError(409, { error: 'other_conflict' })
    )
    await expect(buildSpec('docs/spec/foo.md')).rejects.toThrow()
  })
})
