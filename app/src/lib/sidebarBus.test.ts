import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'

// These tests cover the cross-tab BroadcastChannel layer on top of the
// existing in-tab pub/sub. The in-tab subscriber path is already covered
// by api.test.ts and Sidebar.test.tsx. Here we assert that:
//
//   1. Local bumps still notify local listeners (unchanged behavior).
//   2. bumpAgents/bumpTasks post a message on the BroadcastChannel so
//      other tabs on the same origin can refetch within a render tick.
//   3. A message arriving on the channel (simulating "the other tab just
//      spawned an agent") fans out to local listeners in this tab.
//   4. The module loads cleanly when BroadcastChannel is missing (old
//      browsers, some jsdom configs) and never throws.

describe('sidebarBus BroadcastChannel', () => {
  let postSpy: ReturnType<typeof vi.fn>
  let channelOnMessage: ((ev: MessageEvent) => void) | null
  let OriginalBroadcastChannel: typeof BroadcastChannel | undefined

  beforeEach(() => {
    vi.resetModules()
    OriginalBroadcastChannel = (globalThis as { BroadcastChannel?: typeof BroadcastChannel }).BroadcastChannel
    postSpy = vi.fn()
    channelOnMessage = null
    class FakeBC {
      name: string
      onmessage: ((ev: MessageEvent) => void) | null = null
      constructor(name: string) {
        this.name = name
        // Capture the latest onmessage assignment so tests can drive it.
        Object.defineProperty(this, 'onmessage', {
          get: () => channelOnMessage,
          set: (fn: ((ev: MessageEvent) => void) | null) => {
            channelOnMessage = fn
          },
        })
      }
      postMessage(data: unknown) {
        postSpy(data)
      }
      close() {}
    }
    ;(globalThis as { BroadcastChannel: unknown }).BroadcastChannel =
      FakeBC as unknown as typeof BroadcastChannel
  })

  afterEach(() => {
    if (OriginalBroadcastChannel) {
      ;(globalThis as { BroadcastChannel: typeof BroadcastChannel }).BroadcastChannel =
        OriginalBroadcastChannel
    } else {
      delete (globalThis as { BroadcastChannel?: typeof BroadcastChannel }).BroadcastChannel
    }
  })

  it('bumpAgents posts an agents payload on the channel', async () => {
    const { bumpAgents } = await import('./sidebarBus')
    bumpAgents()
    expect(postSpy).toHaveBeenCalledWith({ kind: 'agents' })
  })

  it('bumpTasks posts a tasks payload on the channel', async () => {
    const { bumpTasks } = await import('./sidebarBus')
    bumpTasks()
    expect(postSpy).toHaveBeenCalledWith({ kind: 'tasks' })
  })

  it('an incoming agents message fans out to local onAgentsChange listeners', async () => {
    const bus = await import('./sidebarBus')
    const listener = vi.fn()
    const off = bus.onAgentsChange(listener)
    try {
      expect(channelOnMessage).toBeTypeOf('function')
      channelOnMessage!({ data: { kind: 'agents' } } as MessageEvent)
      expect(listener).toHaveBeenCalledTimes(1)
    } finally {
      off()
    }
  })

  it('an incoming tasks message fans out to local onTasksChange listeners', async () => {
    const bus = await import('./sidebarBus')
    const listener = vi.fn()
    const off = bus.onTasksChange(listener)
    try {
      channelOnMessage!({ data: { kind: 'tasks' } } as MessageEvent)
      expect(listener).toHaveBeenCalledTimes(1)
    } finally {
      off()
    }
  })

  it('a tasks message does not trigger agents listeners (and vice versa)', async () => {
    const bus = await import('./sidebarBus')
    const agentsListener = vi.fn()
    const tasksListener = vi.fn()
    const offA = bus.onAgentsChange(agentsListener)
    const offT = bus.onTasksChange(tasksListener)
    try {
      channelOnMessage!({ data: { kind: 'tasks' } } as MessageEvent)
      expect(agentsListener).not.toHaveBeenCalled()
      expect(tasksListener).toHaveBeenCalledTimes(1)

      channelOnMessage!({ data: { kind: 'agents' } } as MessageEvent)
      expect(agentsListener).toHaveBeenCalledTimes(1)
      expect(tasksListener).toHaveBeenCalledTimes(1)
    } finally {
      offA()
      offT()
    }
  })

  it('bumpAgents still fires local listeners exactly once (no echo)', async () => {
    const bus = await import('./sidebarBus')
    const listener = vi.fn()
    const off = bus.onAgentsChange(listener)
    try {
      bus.bumpAgents()
      // Local fan-out happens synchronously; the channel postMessage does
      // NOT echo back to the sender in real browsers, so the listener must
      // fire exactly once.
      expect(listener).toHaveBeenCalledTimes(1)
    } finally {
      off()
    }
  })
})

describe('sidebarBus without BroadcastChannel', () => {
  let OriginalBroadcastChannel: typeof BroadcastChannel | undefined

  beforeEach(() => {
    vi.resetModules()
    OriginalBroadcastChannel = (globalThis as { BroadcastChannel?: typeof BroadcastChannel }).BroadcastChannel
    delete (globalThis as { BroadcastChannel?: typeof BroadcastChannel }).BroadcastChannel
  })

  afterEach(() => {
    if (OriginalBroadcastChannel) {
      ;(globalThis as { BroadcastChannel: typeof BroadcastChannel }).BroadcastChannel =
        OriginalBroadcastChannel
    }
  })

  it('loads and bumps without throwing when BroadcastChannel is undefined', async () => {
    const bus = await import('./sidebarBus')
    const listener = vi.fn()
    const off = bus.onAgentsChange(listener)
    try {
      expect(() => bus.bumpAgents()).not.toThrow()
      expect(listener).toHaveBeenCalledTimes(1)
    } finally {
      off()
    }
  })
})

describe('sidebarBus dismissed set', () => {
  let postSpy: ReturnType<typeof vi.fn>
  let channelOnMessage: ((ev: MessageEvent) => void) | null
  let OriginalBroadcastChannel: typeof BroadcastChannel | undefined

  beforeEach(() => {
    vi.resetModules()
    OriginalBroadcastChannel = (globalThis as { BroadcastChannel?: typeof BroadcastChannel }).BroadcastChannel
    postSpy = vi.fn()
    channelOnMessage = null
    class FakeBC {
      name: string
      onmessage: ((ev: MessageEvent) => void) | null = null
      constructor(name: string) {
        this.name = name
        Object.defineProperty(this, 'onmessage', {
          get: () => channelOnMessage,
          set: (fn: ((ev: MessageEvent) => void) | null) => {
            channelOnMessage = fn
          },
        })
      }
      postMessage(data: unknown) {
        postSpy(data)
      }
      close() {}
    }
    ;(globalThis as { BroadcastChannel: unknown }).BroadcastChannel =
      FakeBC as unknown as typeof BroadcastChannel
  })

  afterEach(() => {
    if (OriginalBroadcastChannel) {
      ;(globalThis as { BroadcastChannel: typeof BroadcastChannel }).BroadcastChannel =
        OriginalBroadcastChannel
    } else {
      delete (globalThis as { BroadcastChannel?: typeof BroadcastChannel }).BroadcastChannel
    }
  })

  it('addDismissed mutates the shared set and isDismissed reflects it', async () => {
    const bus = await import('./sidebarBus')
    expect(bus.isDismissed('a-1')).toBe(false)
    bus.addDismissed('a-1')
    expect(bus.isDismissed('a-1')).toBe(true)
  })

  it('getDismissed returns a view that reflects added names', async () => {
    const bus = await import('./sidebarBus')
    bus.addDismissed('alpha')
    bus.addDismissed('beta')
    const view = bus.getDismissed()
    expect(view.has('alpha')).toBe(true)
    expect(view.has('beta')).toBe(true)
    expect(view.has('gamma')).toBe(false)
  })

  it('an incoming dismiss message updates the local set and does not re-broadcast', async () => {
    const bus = await import('./sidebarBus')
    // Simulate the other tab cancelling an agent. postMessage in real
    // browsers never echoes to the sender, so only the local Set should
    // change and postSpy must stay clean.
    expect(channelOnMessage).toBeTypeOf('function')
    channelOnMessage!({ data: { kind: 'dismiss', name: 'from-tab-b' } } as MessageEvent)
    expect(bus.isDismissed('from-tab-b')).toBe(true)
    expect(postSpy).not.toHaveBeenCalled()
  })
})
