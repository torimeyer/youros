import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import ConflictDialog from './ConflictDialog'
import type { ConflictItem } from '../lib/spawn'

const sampleConflicts: ConflictItem[] = [
  {
    requested: 'api/routers/agents.py',
    held_by_spawn: 'build-1458-ai-backend',
    held_path: 'api/routers/agents.py',
  },
  {
    requested: 'app/src/pages/Settings.tsx',
    held_by_spawn: 'build-1458-ai-backend',
    held_path: 'app/src/pages/Settings.tsx',
  },
]

describe('ConflictDialog', () => {
  it('renders nothing when open is false', () => {
    const { container } = render(
      <ConflictDialog
        open={false}
        conflicts={sampleConflicts}
        onWait={() => {}}
        onProceed={() => {}}
      />,
    )
    expect(container.firstChild).toBeNull()
  })

  it('renders one row per conflict, naming the holding agent and file', () => {
    render(
      <ConflictDialog
        open={true}
        conflicts={sampleConflicts}
        onWait={() => {}}
        onProceed={() => {}}
      />,
    )
    const rows = screen.getAllByTestId('conflict-row')
    expect(rows).toHaveLength(2)
    expect(rows[0]).toHaveTextContent('build-1458-ai-backend')
    expect(rows[0]).toHaveTextContent('api/routers/agents.py')
    expect(rows[1]).toHaveTextContent('app/src/pages/Settings.tsx')
  })

  it('fires onWait when the Wait button is clicked', () => {
    const onWait = vi.fn()
    render(
      <ConflictDialog
        open={true}
        conflicts={sampleConflicts}
        onWait={onWait}
        onProceed={() => {}}
      />,
    )
    fireEvent.click(screen.getByTestId('conflict-dialog-wait'))
    expect(onWait).toHaveBeenCalledTimes(1)
  })

  it('fires onProceed when the Build anyway button is clicked', () => {
    const onProceed = vi.fn()
    render(
      <ConflictDialog
        open={true}
        conflicts={sampleConflicts}
        onWait={() => {}}
        onProceed={onProceed}
      />,
    )
    fireEvent.click(screen.getByTestId('conflict-dialog-proceed'))
    expect(onProceed).toHaveBeenCalledTimes(1)
  })

  it('shows the conflict count in the header', () => {
    render(
      <ConflictDialog
        open={true}
        conflicts={sampleConflicts}
        onWait={() => {}}
        onProceed={() => {}}
      />,
    )
    expect(screen.getByTestId('conflict-dialog-header')).toHaveTextContent('2')
  })
})
