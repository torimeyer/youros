import { describe, it, expect, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import { HandoffRuntimeModal } from './HandoffRuntimeModal'

vi.mock('../lib/api', () => ({
  api: {
    post: vi.fn(),
  },
}))

describe('HandoffRuntimeModal theme and accent styling (→3106)', () => {
  it('uses accent-bg and !text-white on Confirm Handoff so light mode does not darken button text or clash with accent color', () => {
    document.documentElement.setAttribute('data-theme', 'light')
    document.documentElement.style.setProperty('--color-accent', '#ec4899')

    try {
      render(
        <HandoffRuntimeModal
          isOpen={true}
          onClose={vi.fn()}
          agentName="builder-claude"
          currentModel="claude-sonnet-5-5"
        />
      )

      const confirmBtn = screen.getByTestId('confirm-handoff-btn')
      expect(confirmBtn.className).toContain('accent-bg')
      expect(confirmBtn.className).toContain('!text-white')
      expect(confirmBtn.className).not.toContain('bg-blue-600')
    } finally {
      document.documentElement.removeAttribute('data-theme')
      document.documentElement.style.removeProperty('--color-accent')
    }
  })
})
