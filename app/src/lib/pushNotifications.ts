/**
 * Push notification helpers.
 *
 * Handles subscribing/unsubscribing to Web Push via the service worker
 * and communicating the subscription to the backend.
 */

import { api } from './api'

/**
 * Check if push notifications are supported in this browser.
 */
export function isPushSupported(): boolean {
  return (
    'serviceWorker' in navigator &&
    'PushManager' in window &&
    'Notification' in window
  )
}

/**
 * Check the current notification permission state.
 */
export function getPermissionState(): NotificationPermission {
  if (typeof Notification === 'undefined') return 'denied'
  return Notification.permission
}

/**
 * Check if the user is currently subscribed to push.
 */
export async function isSubscribed(): Promise<boolean> {
  if (!isPushSupported()) return false
  const reg = await navigator.serviceWorker.ready
  const sub = await reg.pushManager.getSubscription()
  return sub !== null
}

/**
 * Why turning on notifications failed:
 * - 'unsupported': this browser cannot do push notifications at all.
 * - 'blocked': the browser has notifications turned off for this site.
 * - 'dismissed': the permission popup was closed without an answer.
 * - 'error': permission was given but the server part failed.
 */
export type SubscribeFailureReason = 'unsupported' | 'blocked' | 'dismissed' | 'error'

export type SubscribeResult = { ok: true } | { ok: false; reason: SubscribeFailureReason }

/**
 * Subscribe to push notifications.
 * Requests permission if not yet granted, subscribes via the Push API,
 * and sends the subscription to the backend. Returns a result that says
 * WHY it failed, so the UI can explain instead of silently reverting.
 */
export async function subscribe(): Promise<SubscribeResult> {
  if (!isPushSupported()) return { ok: false, reason: 'unsupported' }

  // Ask permission
  const permission = await Notification.requestPermission()
  if (permission === 'denied') return { ok: false, reason: 'blocked' }
  if (permission !== 'granted') return { ok: false, reason: 'dismissed' }

  try {
    // Get the VAPID public key from the backend
    const { public_key } = await api.get<{ public_key: string }>('/push/vapid-public-key')

    // Convert URL-safe base64 to Uint8Array
    const applicationServerKey = urlBase64ToUint8Array(public_key)

    // Subscribe via the Push API
    const reg = await navigator.serviceWorker.ready
    const subscription = await reg.pushManager.subscribe({
      userVisibleOnly: true,
      applicationServerKey: applicationServerKey as BufferSource,
    })

    // Send the subscription to our backend
    await api.post('/push/subscribe', subscription.toJSON())

    return { ok: true }
  } catch {
    return { ok: false, reason: 'error' }
  }
}

/**
 * Unsubscribe from push notifications.
 */
export async function unsubscribe(): Promise<boolean> {
  if (!isPushSupported()) return false

  const reg = await navigator.serviceWorker.ready
  const subscription = await reg.pushManager.getSubscription()
  if (!subscription) return false

  // Tell the backend to remove this subscription
  try {
    await api.post('/push/unsubscribe', subscription.toJSON())
  } catch {
    // Best effort. Even if the backend call fails, still unsubscribe locally.
  }

  await subscription.unsubscribe()
  return true
}

/**
 * Convert a URL-safe base64 string to a Uint8Array.
 * Needed for the applicationServerKey in pushManager.subscribe().
 */
function urlBase64ToUint8Array(base64String: string): Uint8Array {
  const padding = '='.repeat((4 - (base64String.length % 4)) % 4)
  const base64 = (base64String + padding).replace(/-/g, '+').replace(/_/g, '/')
  const rawData = window.atob(base64)
  const outputArray = new Uint8Array(rawData.length)
  for (let i = 0; i < rawData.length; i++) {
    outputArray[i] = rawData.charCodeAt(i)
  }
  return outputArray
}
