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
    expect(onSave).toHaveBeenCalledWith(expected, expect.any(Object))
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
    expect(onSave).toHaveBeenCalledWith([...DEFAULT_DASHBOARD_WIDGETS], expect.any(Object))
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

  // Regression: the toggle thumb used to render invisible because the
  // <span> thumb was absolutely positioned with no left anchor, which
  // made the pill look like a solid blue shape with no on/off marker.
  // The thumb is now an inline flow element inside an inline-flex track.
  it('renders thumb at the on position when the toggle is on', () => {
    // Start with a single visible widget so we know which switch to find.
    const id = DEFAULT_DASHBOARD_WIDGETS[0]
    render(
      <DashboardCustomizeModal
        open={true}
        onClose={onClose}
        widgets={[id]}
        onSave={onSave}
      />,
    )
    const thumb = screen.getByTestId(`widget-toggle-thumb-${id}`)
    expect(thumb.className).toContain('bg-white')
    expect(thumb.className).toContain('translate-x-[22px]')
    expect(thumb.className).not.toContain('translate-x-0.5')
  })

  it('renders thumb at the off position when the toggle is off', () => {
    // Pick a widget that is NOT in the saved list so it renders off.
    const hiddenId = DEFAULT_DASHBOARD_WIDGETS[2]
    render(
      <DashboardCustomizeModal
        open={true}
        onClose={onClose}
        widgets={[DEFAULT_DASHBOARD_WIDGETS[0]]}
        onSave={onSave}
      />,
    )
    const thumb = screen.getByTestId(`widget-toggle-thumb-${hiddenId}`)
    expect(thumb.className).toContain('bg-white')
    expect(thumb.className).toContain('translate-x-0.5')
    expect(thumb.className).not.toContain('translate-x-[22px]')
  })

  // Regression: clicking a hidden toggle must result in onSave being
  // called with a list that INCLUDES the newly toggled id. Previously
  // users reported the new card never showed up on the dashboard.
  it('clicking a hidden toggle then Save emits the new id in the saved list', () => {
    // Start with only quick_launch visible. next_meeting is hidden.
    render(
      <DashboardCustomizeModal
        open={true}
        onClose={onClose}
        widgets={['quick_launch']}
        onSave={onSave}
      />,
    )
    const label = DASHBOARD_WIDGET_LABELS['next_meeting']
    const sw = screen.getByRole('switch', { name: `Show ${label}` })
    expect(sw).toHaveAttribute('aria-checked', 'false')
    fireEvent.click(sw)
    expect(sw).toHaveAttribute('aria-checked', 'true')

    fireEvent.click(screen.getByRole('button', { name: /^Save$/ }))
    expect(onSave).toHaveBeenCalledTimes(1)
    const emitted = onSave.mock.calls[0][0] as string[]
    expect(emitted).toContain('quick_launch')
    expect(emitted).toContain('next_meeting')
  })

  it('Reset to default then Save emits the full default widget list', () => {
    render(
      <DashboardCustomizeModal
        open={true}
        onClose={onClose}
        widgets={['todays_focus']}
        onSave={onSave}
      />,
    )
    fireEvent.click(screen.getByRole('button', { name: /Reset to default/i }))
    fireEvent.click(screen.getByRole('button', { name: /^Save$/ }))
    expect(onSave).toHaveBeenCalledWith([...DEFAULT_DASHBOARD_WIDGETS], expect.any(Object))
  })
})

// ---------------------------------------------------------------------------
// →2921: per-widget width (half or full) editable from the modal rows
// ---------------------------------------------------------------------------

describe('DashboardCustomizeModal widget widths (→2921)', () => {
  let onClose: ReturnType<typeof vi.fn>
  let onSave: ReturnType<typeof vi.fn>

  beforeEach(() => {
    onClose = vi.fn()
    onSave = vi.fn()
  })

  it('every row shows a width control reflecting the default widths', () => {
    render(
      <DashboardCustomizeModal
        open={true}
        onClose={onClose}
        widgets={[...DEFAULT_DASHBOARD_WIDGETS]}
        onSave={onSave}
      />,
    )
    // Default full-width widgets offer to shrink; everything else offers to grow.
    expect(screen.getByTestId('widget-row-size-next_meeting')).toHaveAttribute(
      'aria-label',
      'Make Next Event half width',
    )
    expect(screen.getByTestId('widget-row-size-adventure')).toHaveAttribute(
      'aria-label',
      'Make Try an Adventure half width',
    )
    expect(screen.getByTestId('widget-row-size-todays_focus')).toHaveAttribute(
      'aria-label',
      "Make Today's Focus full width",
    )
  })

  it('a saved width overrides the default in the row control', () => {
    render(
      <DashboardCustomizeModal
        open={true}
        onClose={onClose}
        widgets={[...DEFAULT_DASHBOARD_WIDGETS]}
        sizes={{ todays_focus: 'full', next_meeting: 'half' }}
        onSave={onSave}
      />,
    )
    expect(screen.getByTestId('widget-row-size-todays_focus')).toHaveAttribute(
      'aria-label',
      "Make Today's Focus half width",
    )
    expect(screen.getByTestId('widget-row-size-next_meeting')).toHaveAttribute(
      'aria-label',
      'Make Next Event full width',
    )
  })

  it('toggling a width then Save emits the widths map alongside the ids', () => {
    render(
      <DashboardCustomizeModal
        open={true}
        onClose={onClose}
        widgets={[...DEFAULT_DASHBOARD_WIDGETS]}
        onSave={onSave}
      />,
    )
    fireEvent.click(screen.getByTestId('widget-row-size-todays_focus'))
    expect(screen.getByTestId('widget-row-size-todays_focus')).toHaveAttribute(
      'aria-label',
      "Make Today's Focus half width",
    )
    fireEvent.click(screen.getByRole('button', { name: /^Save$/ }))
    expect(onSave).toHaveBeenCalledTimes(1)
    const [ids, sizes] = onSave.mock.calls[0] as [string[], Record<string, string>]
    expect(ids).toEqual([...DEFAULT_DASHBOARD_WIDGETS])
    expect(sizes).toEqual(
      expect.objectContaining({
        todays_focus: 'full',
        next_meeting: 'full',
        adventure: 'full',
        quick_launch: 'half',
      }),
    )
  })

  it('Reset to default returns widths to their defaults', () => {
    render(
      <DashboardCustomizeModal
        open={true}
        onClose={onClose}
        widgets={[...DEFAULT_DASHBOARD_WIDGETS]}
        sizes={{ todays_focus: 'full' }}
        onSave={onSave}
      />,
    )
    expect(screen.getByTestId('widget-row-size-todays_focus')).toHaveAttribute(
      'aria-label',
      "Make Today's Focus half width",
    )
    fireEvent.click(screen.getByRole('button', { name: /Reset to default/i }))
    expect(screen.getByTestId('widget-row-size-todays_focus')).toHaveAttribute(
      'aria-label',
      "Make Today's Focus full width",
    )
  })

  it('a hidden widget keeps its chosen width through Save', () => {
    render(
      <DashboardCustomizeModal
        open={true}
        onClose={onClose}
        widgets={['quick_launch']}
        sizes={{ todays_focus: 'full' }}
        onSave={onSave}
      />,
    )
    fireEvent.click(screen.getByRole('button', { name: /^Save$/ }))
    const [ids, sizes] = onSave.mock.calls[0] as [string[], Record<string, string>]
    expect(ids).toEqual(['quick_launch'])
    expect(sizes).toEqual(expect.objectContaining({ todays_focus: 'full' }))
  })
})
