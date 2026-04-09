import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import DashboardCustomizeModal from './DashboardCustomizeModal'
import {
  DEFAULT_DASHBOARD_WIDGETS,
  DASHBOARD_WIDGET_LABELS,
} from '../stores/app'

describe('DashboardCustomizeModal', () => {
  let onClose: ReturnType<typeof vi.fn>
  let onSave: ReturnType<typeof vi.fn>

  beforeEach(() => {
    onClose = vi.fn()
    onSave = vi.fn()
  })

  it('does not render when open is false', () => {
    render(
      <DashboardCustomizeModal
        open={false}
        onClose={onClose}
        widgets={[...DEFAULT_DASHBOARD_WIDGETS]}
        onSave={onSave}
      />,
    )
    expect(screen.queryByRole('dialog', { name: /Customize dashboard/i })).toBeNull()
  })

  it('lists every available widget when open', () => {
    render(
      <DashboardCustomizeModal
        open={true}
        onClose={onClose}
        widgets={[...DEFAULT_DASHBOARD_WIDGETS]}
        onSave={onSave}
      />,
    )
    for (const id of Object.keys(DASHBOARD_WIDGET_LABELS)) {
      expect(screen.getByText(DASHBOARD_WIDGET_LABELS[id])).toBeInTheDocument()
    }
  })

  it('shows hidden widgets with their switch off', () => {
    // Only the first two widgets are visible. Everything else is off
    // but still listed so the user can turn it back on.
    const subset = DEFAULT_DASHBOARD_WIDGETS.slice(0, 2)
    render(
      <DashboardCustomizeModal
        open={true}
        onClose={onClose}
        widgets={subset}
        onSave={onSave}
      />,
    )

    const visibleLabel = DASHBOARD_WIDGET_LABELS[subset[0]]
    const hiddenId = DEFAULT_DASHBOARD_WIDGETS[3]
    const hiddenLabel = DASHBOARD_WIDGET_LABELS[hiddenId]

    const visibleSwitch = screen.getByRole('switch', { name: `Show ${visibleLabel}` })
    const hiddenSwitch = screen.getByRole('switch', { name: `Show ${hiddenLabel}` })

    expect(visibleSwitch).toHaveAttribute('aria-checked', 'true')
    expect(hiddenSwitch).toHaveAttribute('aria-checked', 'false')
  })

  it('toggling a widget flips its switch state', () => {
    render(
      <DashboardCustomizeModal
        open={true}
        onClose={onClose}
        widgets={[...DEFAULT_DASHBOARD_WIDGETS]}
        onSave={onSave}
      />,
    )
    const label = DASHBOARD_WIDGET_LABELS[DEFAULT_DASHBOARD_WIDGETS[0]]
    const sw = screen.getByRole('switch', { name: `Show ${label}` })
    expect(sw).toHaveAttribute('aria-checked', 'true')
    fireEvent.click(sw)
    expect(sw).toHaveAttribute('aria-checked', 'false')
  })

  it('Save calls onSave with only the visible widgets in order', () => {
    render(
      <DashboardCustomizeModal
        open={true}
        onClose={onClose}
        widgets={[...DEFAULT_DASHBOARD_WIDGETS]}
        onSave={onSave}
      />,
    )
    // Turn off the second widget.
    const label = DASHBOARD_WIDGET_LABELS[DEFAULT_DASHBOARD_WIDGETS[1]]
    fireEvent.click(screen.getByRole('switch', { name: `Show ${label}` }))

    fireEvent.click(screen.getByRole('button', { name: /^Save$/ }))

    expect(onSave).toHaveBeenCalledTimes(1)
    const expected = DEFAULT_DASHBOARD_WIDGETS.filter(
      (id) => id !== DEFAULT_DASHBOARD_WIDGETS[1],
    )
    expect(onSave).toHaveBeenCalledWith(expected)
    expect(onClose).toHaveBeenCalled()
  })

  it('Reset restores the full default list when widgets are cleared', () => {
    // Start with an empty widget list so Reset has visible work to do.
    render(
      <DashboardCustomizeModal
        open={true}
        onClose={onClose}
        widgets={[]}
        onSave={onSave}
      />,
    )

    // Every widget switch should be off.
    for (const id of DEFAULT_DASHBOARD_WIDGETS) {
      const sw = screen.getByRole('switch', {
        name: `Show ${DASHBOARD_WIDGET_LABELS[id]}`,
      })
      expect(sw).toHaveAttribute('aria-checked', 'false')
    }

    fireEvent.click(screen.getByRole('button', { name: /Reset to default/i }))

    for (const id of DEFAULT_DASHBOARD_WIDGETS) {
      const sw = screen.getByRole('switch', {
        name: `Show ${DASHBOARD_WIDGET_LABELS[id]}`,
      })
      expect(sw).toHaveAttribute('aria-checked', 'true')
    }

    // Saving now should pass the full default order.
    fireEvent.click(screen.getByRole('button', { name: /^Save$/ }))
    expect(onSave).toHaveBeenCalledWith([...DEFAULT_DASHBOARD_WIDGETS])
  })

  it('Cancel closes without calling onSave', () => {
    render(
      <DashboardCustomizeModal
        open={true}
        onClose={onClose}
        widgets={[...DEFAULT_DASHBOARD_WIDGETS]}
        onSave={onSave}
      />,
    )
    fireEvent.click(screen.getByRole('button', { name: /^Cancel$/ }))
    expect(onClose).toHaveBeenCalled()
    expect(onSave).not.toHaveBeenCalled()
  })
})
