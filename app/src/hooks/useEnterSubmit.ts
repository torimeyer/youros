/**
 * useEnterSubmit
 *
 * Returns an onKeyDown handler that calls `handler` when the user presses
 * Enter without Shift (Shift+Enter always inserts a newline in textareas)
 * and without an active IME composition session.
 *
 * Usage:
 *   const handleKeyDown = useEnterSubmit(handleSubmit)
 *   <input onKeyDown={handleKeyDown} ... />
 *   <textarea onKeyDown={handleKeyDown} ... />  // Shift+Enter inserts newline
 */
import { useCallback, useRef } from 'react'

type Handler = () => void

export function useEnterSubmit(handler: Handler) {
  // Track IME composition so we never fire mid-composition (e.g. Japanese/Chinese input)
  const composingRef = useRef(false)

  const onCompositionStart = useCallback(() => {
    composingRef.current = true
  }, [])

  const onCompositionEnd = useCallback(() => {
    composingRef.current = false
  }, [])

  const onKeyDown = useCallback(
    (e: React.KeyboardEvent<HTMLInputElement | HTMLTextAreaElement>) => {
      if (e.key === 'Enter' && !e.shiftKey && !composingRef.current) {
        e.preventDefault()
        handler()
      }
    },
    [handler],
  )

  return { onKeyDown, onCompositionStart, onCompositionEnd }
}
