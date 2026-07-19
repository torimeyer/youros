import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'

// Mock the api module
vi.mock('./api', () => ({
  api: {
    get: vi.fn(),
    post: vi.fn(),
  },
}))

import { api } from './api'
import { isPushSupported, getPermissionState, isSubscribed, subscribe, unsubscribe } from './pushNotifications'

const mockedApiGet = vi.mocked(api.get)
const mockedApiPost = vi.mocked(api.post)

// Save originals so we can restore them
const originalPushManager = globalThis.PushManager
const originalNotification = globalThis.Notification

function setupPushEnv(opts?: {
  hasPushManager?: boolean
  permission?: string
  requestPermission?: () => Promise<string>
  swReady?: unknown
}) {
  if (opts?.hasPushManager !== false) {
    vi.stubGlobal('PushManager', class {})
  } else {
    // Delete PushManager entirely so `'PushManager' in window` is false
    delete (globalThis as Record<string, unknown>).PushManager
  }

  const notifObj: Record<string, unknown> = {
    permission: opts?.permission ?? 'default',
  }
  if (opts?.requestPermission) {
    notifObj.requestPermission = opts.requestPermission
  }
  vi.stubGlobal('Notification', notifObj)

  // Always set up a serviceWorker mock so isPushSupported() passes.
  // Individual tests can override swReady to provide specific mocks.
  const swReadyTarget = opts?.swReady ?? {}
  Object.defineProperty(navigator, 'serviceWorker', {
    value: { ready: Promise.resolve(swReadyTarget) },
    configurable: true,
    writable: true,
  })
}

describe('pushNotifications', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  afterEach(() => {
    // Restore globals to avoid leaking between tests
    vi.stubGlobal('PushManager', originalPushManager)
    vi.stubGlobal('Notification', originalNotification)
  })

  describe('isPushSupported', () => {
    it('returns true when all APIs are available', () => {
      setupPushEnv({ hasPushManager: true })
      expect(isPushSupported()).toBe(true)
    })

    it('returns false when PushManager is missing', () => {
      setupPushEnv({ hasPushManager: false })
      expect(isPushSupported()).toBe(false)
    })
  })

  describe('getPermissionState', () => {
    it('returns the current Notification permission', () => {
      setupPushEnv({ permission: 'granted' })
      expect(getPermissionState()).toBe('granted')
    })

    it('returns denied when Notification is not available', () => {
      // Delete Notification entirely so typeof returns 'undefined'
      delete (globalThis as Record<string, unknown>).Notification
      expect(getPermissionState()).toBe('denied')
    })
  })

  describe('isSubscribed', () => {
    it('returns false when push is not supported', async () => {
      setupPushEnv({ hasPushManager: false })
      const result = await isSubscribed()
      expect(result).toBe(false)
    })

    it('returns true when there is an active subscription', async () => {
      const mockReg = {
        pushManager: {
          getSubscription: vi.fn().mockResolvedValue({ endpoint: 'https://push.example.com/1' }),
        },
      }
      setupPushEnv({ hasPushManager: true, permission: 'granted', swReady: mockReg })
      const result = await isSubscribed()
      expect(result).toBe(true)
    })

    it('returns false when there is no subscription', async () => {
      const mockReg = {
        pushManager: {
          getSubscription: vi.fn().mockResolvedValue(null),
        },
      }
      setupPushEnv({ hasPushManager: true, permission: 'granted', swReady: mockReg })
      const result = await isSubscribed()
      expect(result).toBe(false)
    })
  })

  describe('subscribe', () => {
    it('reports unsupported when push is not available in this browser', async () => {
      setupPushEnv({ hasPushManager: false })
      const result = await subscribe()
      expect(result).toEqual({ ok: false, reason: 'unsupported' })
    })

    it('reports blocked when the browser has notifications turned off for the site', async () => {
      const mockReg = { pushManager: { subscribe: vi.fn() } }
      setupPushEnv({
        hasPushManager: true,
        requestPermission: vi.fn().mockResolvedValue('denied') as unknown as () => Promise<string>,
        swReady: mockReg,
      })
      const result = await subscribe()
      expect(result).toEqual({ ok: false, reason: 'blocked' })
    })

    it('reports dismissed when the permission popup is closed without an answer', async () => {
      const mockReg = { pushManager: { subscribe: vi.fn() } }
      setupPushEnv({
        hasPushManager: true,
        requestPermission: vi.fn().mockResolvedValue('default') as unknown as () => Promise<string>,
        swReady: mockReg,
      })
      const result = await subscribe()
      expect(result).toEqual({ ok: false, reason: 'dismissed' })
    })

    it('reports an error when the server part fails after permission is granted', async () => {
      const mockReg = { pushManager: { subscribe: vi.fn() } }
      setupPushEnv({
        hasPushManager: true,
        requestPermission: vi.fn().mockResolvedValue('granted') as unknown as () => Promise<string>,
        swReady: mockReg,
      })
      mockedApiGet.mockRejectedValue(new Error('backend down'))
      const result = await subscribe()
      expect(result).toEqual({ ok: false, reason: 'error' })
    })

    it('subscribes and sends to backend when permission is granted', async () => {
      const mockSub = {
        toJSON: () => ({ endpoint: 'https://push.example.com/1', keys: {} }),
      }
      const mockReg = {
        pushManager: {
          subscribe: vi.fn().mockResolvedValue(mockSub),
        },
      }
      setupPushEnv({
        hasPushManager: true,
        requestPermission: vi.fn().mockResolvedValue('granted') as unknown as () => Promise<string>,
        swReady: mockReg,
      })

      mockedApiGet.mockResolvedValue({ public_key: 'dGVzdC1wdWJsaWMta2V5' })
      mockedApiPost.mockResolvedValue({ result: 'ok' })

      const result = await subscribe()
      expect(result).toEqual({ ok: true })
      expect(mockedApiGet).toHaveBeenCalledWith('/push/vapid-public-key')
      expect(mockedApiPost).toHaveBeenCalledWith('/push/subscribe', { endpoint: 'https://push.example.com/1', keys: {} })
    })
  })

  describe('unsubscribe', () => {
    it('returns false when push is not supported', async () => {
      setupPushEnv({ hasPushManager: false })
      const result = await unsubscribe()
      expect(result).toBe(false)
    })

    it('unsubscribes and notifies backend', async () => {
      const mockSub = {
        unsubscribe: vi.fn().mockResolvedValue(true),
        toJSON: () => ({ endpoint: 'https://push.example.com/1', keys: {} }),
      }
      const mockReg = {
        pushManager: {
          getSubscription: vi.fn().mockResolvedValue(mockSub),
        },
      }
      setupPushEnv({ hasPushManager: true, permission: 'granted', swReady: mockReg })

      mockedApiPost.mockResolvedValue({ result: 'ok' })

      const result = await unsubscribe()
      expect(result).toBe(true)
      expect(mockSub.unsubscribe).toHaveBeenCalled()
      expect(mockedApiPost).toHaveBeenCalledWith('/push/unsubscribe', { endpoint: 'https://push.example.com/1', keys: {} })
    })
  })
})
