/**
 * Integration guard: the sidebar badge, Agents page, and the server-side
 * `/api/agents?user_spawned_only=true` filter must all count the same set
 * of rows. This test asserts the frontend-side filter (`isUserSpawnedAgent`)
 * matches the Python mirror in `api/services/agent_filters.py`. If you
 * change one, update the other.
 */

import { describe, it, expect } from 'vitest'
import { isAgentActive, isUserSpawnedAgent, isMainSession } from './agentUtils'

describe('isUserSpawnedAgent (shared sidebar + Agents page filter)', () => {
  const now = '2026-04-15T10:00:00Z'

  it('includes a named claude-code subagent', () => {
    expect(
      isUserSpawnedAgent({
        name: 'real-agent',
        source: 'claude-code',
        model: 'sonnet',
      }),
    ).toBe(true)
  })

  it('includes an api-sourced agent', () => {
    expect(
      isUserSpawnedAgent({
        name: 'api-agent',
        source: 'api',
        model: 'sonnet',
      }),
    ).toBe(true)
  })

  it('excludes source=chat (torichat session)', () => {
    expect(
      isUserSpawnedAgent({
        name: 'chat-default',
        source: 'chat',
        model: 'sonnet',
      }),
    ).toBe(false)
  })

  it('excludes source=audit', () => {
    expect(
      isUserSpawnedAgent({
        name: 'some-agent',
        source: 'audit',
        model: 'sonnet',
      }),
    ).toBe(false)
  })

  it('excludes source=hook', () => {
    expect(
      isUserSpawnedAgent({
        name: 'hook-agent',
        source: 'hook',
        model: 'sonnet',
      }),
    ).toBe(false)
  })

  it('excludes model=claude-code-subscription', () => {
    expect(
      isUserSpawnedAgent({
        name: 'sub-agent',
        source: 'claude-code',
        model: 'claude-code-subscription',
      }),
    ).toBe(false)
  })

  it('excludes hook_preregister=true placeholder rows (agent not yet booted)', () => {
    // The PreToolUse hook fires before the subagent starts and inserts a row
    // with hook_preregister: true. It must not appear in the sidebar or Agents
    // page until the subagent calls /register and the flag is cleared.
    expect(
      isUserSpawnedAgent({
        name: 'pending-agent',
        source: 'claude-code',
        model: 'sonnet',
        hook_preregister: true,
      }),
    ).toBe(false)
  })

  it('excludes the main Claude Code session (inferred name + desc)', () => {
    const agent = {
      name: 'claude-code-abcdef12',
      source: 'claude-code',
      model: 'sonnet',
      description: 'Claude Code session (cwd: /tmp)',
    }
    expect(isMainSession(agent)).toBe(true)
    expect(isUserSpawnedAgent(agent)).toBe(false)
  })

  it('excludes inferred-name agent with no task and no description (→1674/→1675)', () => {
    // Pre-registration or leaked main-session row: inferred claude-code-XXXX name,
    // no task, no description. The backend is_main_session already excludes these
    // from running_count. The frontend must match or the badge shows 0 while Active
    // Sessions shows 1 with a generic "Claude · Chat · time" label.
    expect(
      isUserSpawnedAgent({
        name: 'claude-code-abcdef12',
        source: 'claude-code',
        model: 'sonnet',
      }),
    ).toBe(false)
  })

  it('includes inferred-name agent once it has a task (registered subagent)', () => {
    // The same inferred-name agent becomes visible as soon as it registers with
    // a human-readable task. This was the intent of d44dba6.
    expect(
      isUserSpawnedAgent({
        name: 'claude-code-abcdef12',
        source: 'claude-code',
        model: 'sonnet',
        task: 'Fix delete-all 403',
      }),
    ).toBe(true)
  })

  it('badge count matches Active Sessions count when inferred-name no-task agent exists (→1675)', () => {
    // Integration guard: badge (running_count from backend WS) must equal
    // Active Sessions visible count. Both now apply the same isMainSession fallback.
    const agents = [
      { name: 'real-1', status: 'running', source: 'claude-code', model: 'sonnet', task: 'Fix bug' },
      // Pre-registration inferred agent — backend excludes it, frontend must too:
      { name: 'claude-code-abcdef12', status: 'running', source: 'claude-code', model: 'sonnet' },
    ]
    const badgeCount = agents.filter(
      (a) =>
        (a.status === 'running' || a.status === 'spawned') &&
        isUserSpawnedAgent(a),
    ).length
    const pageCount = agents
      .filter(isUserSpawnedAgent)
      .filter((a) => a.status === 'running' || a.status === 'spawned').length
    expect(badgeCount).toBe(pageCount)
    expect(badgeCount).toBe(1) // only real-1; claude-code-abcdef12 has no task
  })

  it('integration: badge count equals user-spawned running count', () => {
    // Simulates the exact set the sidebar iterates over in Sidebar.tsx
    // (status === running|spawned && isUserSpawnedAgent). The badge count
    // must match the filtered Agents page list length.
    const agents = [
      { name: 'real-1', status: 'running', source: 'claude-code', model: 'sonnet' },
      { name: 'real-2', status: 'running', source: 'api', model: 'sonnet' },
      { name: 'chat-default', status: 'running', source: 'chat', model: 'sonnet' },
      { name: 'audit-row', status: 'running', source: 'audit', model: 'sonnet' },
      { name: 'hook-row', status: 'running', source: 'hook', model: 'sonnet' },
      { name: 'sub-row', status: 'running', source: 'claude-code', model: 'claude-code-subscription' },
      { name: 'claude-code-12345678', status: 'running', source: 'claude-code', model: 'sonnet', description: 'Claude Code session (cwd: /x)' },
      { name: 'old-completed', status: 'completed', source: 'claude-code', model: 'sonnet' },
      { name: 'spawning', status: 'spawned', source: 'claude-code', model: 'sonnet' },
      { name: 'hook-placeholder', status: 'running', source: 'claude-code', model: 'sonnet', hook_preregister: true },
    ]

    // Sidebar filter: running|spawned + isUserSpawnedAgent
    const badgeCount = agents.filter(
      (a) =>
        (a.status === 'running' || a.status === 'spawned') && isUserSpawnedAgent(a),
    ).length

    // Agents page filter: isUserSpawnedAgent, then count only running|spawned
    const pageCount = agents
      .filter(isUserSpawnedAgent)
      .filter((a) => a.status === 'running' || a.status === 'spawned').length

    expect(badgeCount).toBe(pageCount)
    expect(badgeCount).toBe(3) // real-1, real-2, spawning
    // used to silence unused import warnings
    expect(now).toBeTruthy()
  })
})

describe('isAgentActive (Active tab visibility)', () => {
  const NOW = 1_700_000_000_000

  it('treats running as active', () => {
    expect(isAgentActive({ status: 'running' }, NOW)).toBe(true)
  })

  it('treats spawned as active', () => {
    expect(isAgentActive({ status: 'spawned' }, NOW)).toBe(true)
  })

  it('treats starting as active', () => {
    expect(isAgentActive({ status: 'starting' }, NOW)).toBe(true)
  })

  it('treats completed as inactive', () => {
    expect(isAgentActive({ status: 'completed' }, NOW)).toBe(false)
  })

  it('treats cancelled as inactive even with a fresh heartbeat', () => {
    expect(
      isAgentActive(
        {
          status: 'cancelled',
          last_heartbeat_at: new Date(NOW - 1000).toISOString(),
        },
        NOW,
      ),
    ).toBe(false)
  })

  it('keeps terminated_stale visible when heartbeat is fresher than 2 minutes', () => {
    expect(
      isAgentActive(
        {
          status: 'terminated_stale',
          last_heartbeat_at: new Date(NOW - 30_000).toISOString(),
        },
        NOW,
      ),
    ).toBe(true)
  })

  it('hides terminated_stale when heartbeat is older than 2 minutes', () => {
    expect(
      isAgentActive(
        {
          status: 'terminated_stale',
          last_heartbeat_at: new Date(NOW - 600_000).toISOString(),
        },
        NOW,
      ),
    ).toBe(false)
  })

  it('hides terminated_stale with no heartbeat at all', () => {
    expect(isAgentActive({ status: 'terminated_stale' }, NOW)).toBe(false)
  })
})

describe('isMainSession (frontend/backend parity, →1674/→1675)', () => {
  it('identifies the main session by description prefix', () => {
    expect(
      isMainSession({
        name: 'claude-code-abcdef12',
        description: 'Claude Code session (cwd: /tmp)',
      }),
    ).toBe(true)
  })

  it('identifies Gemini sessions by description prefix', () => {
    expect(
      isMainSession({
        name: 'gemini-cli-mcp-client-abcdef12',
        description: 'Gemini session (cwd: /tmp)',
      }),
    ).toBe(true)
  })

  it('excludes named (non-inferred) agents regardless of description', () => {
    expect(
      isMainSession({
        name: 'my-custom-agent',
        description: 'Claude Code session (cwd: /tmp)',
      }),
    ).toBe(false)
  })

  it('treats inferred-name agent with no task and no description as main session (→1674/→1675)', () => {
    // Backend is_main_session has the same fallback. Keeping them in sync prevents
    // badge=0 while Active Sessions shows 1 with a generic "Claude · Chat · time" label.
    expect(
      isMainSession({
        name: 'claude-code-abcdef12',
      }),
    ).toBe(true)
  })

  it('does NOT treat inferred-name agent with a task as main session', () => {
    // Once the subagent registers a task, it is a real user-spawned agent.
    expect(
      isMainSession({
        name: 'claude-code-abcdef12',
        task: 'Fix delete-all 403',
      }),
    ).toBe(false)
  })

  it('does NOT treat inferred-name agent with a description as main session (unless prefix matches)', () => {
    expect(
      isMainSession({
        name: 'claude-code-abcdef12',
        description: 'Some custom description',
      }),
    ).toBe(false)
  })
})
