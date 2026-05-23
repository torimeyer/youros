import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { reportError } from './reportError'

describe('reportError', () => {
  let errorSpy: ReturnType<typeof vi.spyOn>

  beforeEach(() => {
    errorSpy = vi.spyOn(console, 'error').mockImplementation(() => {})
  })

  afterEach(() => {
    errorSpy.mockRestore()
  })

  it('calls console.error exactly once', () => {
    reportError('test label', new Error('oops'))
    expect(errorSpy).toHaveBeenCalledTimes(1)
  })

  it('prefixes the label in square brackets', () => {
    reportError('fetch failed', new Error('oops'))
    expect(errorSpy).toHaveBeenCalledWith('[fetch failed]', expect.any(Error))
  })

  it('passes the error object through unchanged', () => {
    const err = new Error('the real error')
    reportError('something broke', err)
    const [, passedErr] = errorSpy.mock.calls[0]
    expect(passedErr).toBe(err)
  })
})
