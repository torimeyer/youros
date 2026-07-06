import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import TextYourOS from './TextYourOS'

const mockStatus = {
  enabled: false,
  trusted_contacts: [],
  confirm_commands: null,
}

vi.mock('../lib/api', () => ({
  api: {
    get: vi.fn(),
    patch: vi.fn(),
  },
}))

import { api } from '../lib/api'

beforeEach(() => {
  vi.clearAllMocks()
  ;(api.get as ReturnType<typeof vi.fn>).mockResolvedValue({ ...mockStatus })
  ;(api.patch as ReturnType<typeof vi.fn>).mockImplementation((_, body) =>
    Promise.resolve({ ...mockStatus, ...body })
  )
})

describe('TextYourOS', () => {
  it('renders with testid and shows status', async () => {
    render(<TextYourOS />)
    await waitFor(() => expect(screen.getByTestId('text-youros-card')).toBeTruthy())
  })

  it('toggle calls PATCH /text-bridge/config with enabled flipped', async () => {
    render(<TextYourOS />)
    await waitFor(() => screen.getByTestId('text-youros-toggle'))

    fireEvent.click(screen.getByTestId('text-youros-toggle'))

    await waitFor(() => {
      expect(api.patch).toHaveBeenCalledWith('/text-bridge/config', { enabled: true })
    })
  })

  it('shows enabled state after toggle', async () => {
    ;(api.patch as ReturnType<typeof vi.fn>).mockResolvedValue({ ...mockStatus, enabled: true })
    render(<TextYourOS />)
    await waitFor(() => screen.getByTestId('text-youros-toggle'))

    fireEvent.click(screen.getByTestId('text-youros-toggle'))

    await waitFor(() => {
      expect(screen.getByText('Listening for messages from trusted contacts')).toBeTruthy()
    })
  })

  it('adds a trusted contact', async () => {
    render(<TextYourOS />)
    await waitFor(() => screen.getByTestId('text-youros-contact-input'))

    fireEvent.change(screen.getByTestId('text-youros-contact-input'), {
      target: { value: '+15550001234' },
    })
    fireEvent.submit(screen.getByTestId('text-youros-add-contact').closest('form')!)

    await waitFor(() => {
      expect(api.patch).toHaveBeenCalledWith('/text-bridge/config', {
        trusted_contacts: ['+15550001234'],
      })
    })
  })

  it('confirm toggle calls PATCH with confirm_commands always', async () => {
    render(<TextYourOS />)
    await waitFor(() => screen.getByTestId('text-youros-confirm-toggle'))

    fireEvent.click(screen.getByTestId('text-youros-confirm-toggle'))

    await waitFor(() => {
      expect(api.patch).toHaveBeenCalledWith('/text-bridge/config', { confirm_commands: 'always' })
    })
  })
})
