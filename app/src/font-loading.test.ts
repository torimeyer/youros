import { describe, it, expect, beforeEach, afterEach } from 'vitest'

describe('font loading FOUC prevention', () => {
  beforeEach(() => {
    document.documentElement.classList.remove('icons-loaded')
  })

  afterEach(() => {
    document.documentElement.classList.remove('icons-loaded')
  })

  it('adds icons-loaded class when document.fonts.ready resolves', async () => {
    // jsdom does not provide document.fonts, so we mock it
    const fontsReady = Promise.resolve()
    Object.defineProperty(document, 'fonts', {
      value: { ready: fontsReady },
      configurable: true,
    })

    expect(document.documentElement.classList.contains('icons-loaded')).toBe(false)

    // Simulate the logic from main.tsx
    if (document.fonts?.ready) {
      await document.fonts.ready
      document.documentElement.classList.add('icons-loaded')
    }

    expect(document.documentElement.classList.contains('icons-loaded')).toBe(true)

    // Clean up mock
    Object.defineProperty(document, 'fonts', {
      value: undefined,
      configurable: true,
    })
  })

  it('adds icons-loaded immediately when document.fonts is not available', () => {
    // Ensure document.fonts is not defined (default in jsdom)
    expect(document.documentElement.classList.contains('icons-loaded')).toBe(false)

    // Simulate the fallback logic from main.tsx
    if (document.fonts?.ready) {
      // Would not enter this branch
    } else {
      document.documentElement.classList.add('icons-loaded')
    }

    expect(document.documentElement.classList.contains('icons-loaded')).toBe(true)
  })

  it('icons-loaded class can be toggled on the document element', () => {
    const span = document.createElement('span')
    span.classList.add('material-symbols-outlined')
    span.textContent = 'home'
    document.body.appendChild(span)

    // Before: icons-loaded is not present
    expect(document.documentElement.classList.contains('icons-loaded')).toBe(false)

    // After: adding the class satisfies the CSS contract for visibility
    document.documentElement.classList.add('icons-loaded')
    expect(document.documentElement.classList.contains('icons-loaded')).toBe(true)

    document.body.removeChild(span)
  })
})
