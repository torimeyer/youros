import { useEffect, useState } from 'react'
import {
  DndContext,
  PointerSensor,
  useSensor,
  useSensors,
  closestCenter,
  type DragEndEvent,
} from '@dnd-kit/core'
import {
  SortableContext,
  useSortable,
  verticalListSortingStrategy,
  arrayMove,
} from '@dnd-kit/sortable'
import { CSS } from '@dnd-kit/utilities'
import Icon from './Icon'
import {
  DEFAULT_DASHBOARD_WIDGETS,
  DASHBOARD_WIDGET_LABELS,
} from '../stores/app'

interface Props {
  open: boolean
  onClose: () => void
  widgets: string[]
  onSave: (widgets: string[]) => void
}

interface RowState {
  id: string
  visible: boolean
}

// Build the editable row list from the currently saved widget ids.
// Any known widget not in the saved list still appears, toggled off,
// so the user can turn it back on. The saved widgets keep their order
// first, then any remaining known widgets are appended at the bottom.
function buildRows(saved: string[]): RowState[] {
  const known = Object.keys(DASHBOARD_WIDGET_LABELS)
  const rows: RowState[] = []
  const seen = new Set<string>()
  for (const id of saved) {
    if (DASHBOARD_WIDGET_LABELS[id]) {
      rows.push({ id, visible: true })
      seen.add(id)
    }
  }
  for (const id of known) {
    if (!seen.has(id)) {
      rows.push({ id, visible: false })
    }
  }
  return rows
}

interface SortableRowProps {
  row: RowState
  onToggle: (id: string) => void
}

function SortableRow({ row, onToggle }: SortableRowProps) {
  const {
    attributes,
    listeners,
    setNodeRef,
    transform,
    transition,
    isDragging,
  } = useSortable({ id: row.id })

  const style: React.CSSProperties = {
    transform: CSS.Transform.toString(transform),
    transition,
    opacity: isDragging ? 0.4 : 1,
  }

  const label = DASHBOARD_WIDGET_LABELS[row.id] ?? row.id

  return (
    <div
      ref={setNodeRef}
      style={style}
      data-testid={`widget-row-${row.id}`}
      className="flex items-center gap-3 bg-slate-800 border border-slate-700 rounded-lg px-3 py-2.5"
    >
      <button
        type="button"
        aria-label={`Drag ${label}`}
        {...attributes}
        {...listeners}
        className="text-slate-500 hover:text-slate-300 cursor-grab active:cursor-grabbing touch-none"
      >
        <Icon name="drag_indicator" size={20} />
      </button>
      <span className="flex-1 text-sm text-slate-200">{label}</span>
      <button
        type="button"
        role="switch"
        aria-checked={row.visible}
        aria-label={`Show ${label}`}
        onClick={() => onToggle(row.id)}
        className={`relative inline-flex items-center w-11 h-6 rounded-full transition-colors shrink-0 ${
          row.visible ? 'bg-blue-500' : 'bg-slate-600'
        }`}
      >
        <span
          data-testid={`widget-toggle-thumb-${row.id}`}
          className={`inline-block w-5 h-5 rounded-full bg-white shadow-sm transform transition-transform ${
            row.visible ? 'translate-x-[22px]' : 'translate-x-0.5'
          }`}
        />
      </button>
    </div>
  )
}

export default function DashboardCustomizeModal({
  open,
  onClose,
  widgets,
  onSave,
}: Props) {
  const [rows, setRows] = useState<RowState[]>(() => buildRows(widgets))

  // Reset local state whenever the modal opens with a fresh prop list.
  useEffect(() => {
    if (open) {
      setRows(buildRows(widgets))
    }
  }, [open, widgets])

  // Escape closes the modal.
  useEffect(() => {
    if (!open) return
    const handler = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose()
    }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [open, onClose])

  const sensors = useSensors(useSensor(PointerSensor))

  if (!open) return null

  const handleToggle = (id: string) => {
    setRows((prev) =>
      prev.map((r) => (r.id === id ? { ...r, visible: !r.visible } : r)),
    )
  }

  const handleDragEnd = (event: DragEndEvent) => {
    const { active, over } = event
    if (!over || active.id === over.id) return
    setRows((prev) => {
      const oldIndex = prev.findIndex((r) => r.id === active.id)
      const newIndex = prev.findIndex((r) => r.id === over.id)
      if (oldIndex === -1 || newIndex === -1) return prev
      return arrayMove(prev, oldIndex, newIndex)
    })
  }

  const handleSave = () => {
    const next = rows.filter((r) => r.visible).map((r) => r.id)
    onSave(next)
    onClose()
  }

  const handleReset = () => {
    setRows(buildRows([...DEFAULT_DASHBOARD_WIDGETS]))
  }

  const handleBackdropClick = (e: React.MouseEvent<HTMLDivElement>) => {
    if (e.target === e.currentTarget) onClose()
  }

  return (
    <div
      role="dialog"
      aria-label="Customize dashboard"
      onClick={handleBackdropClick}
      data-testid="customize-dashboard-backdrop"
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4"
    >
      <div className="bg-slate-900 border border-slate-700 rounded-xl w-full max-w-md p-6 shadow-2xl">
        <div className="flex items-start justify-between mb-4">
          <div className="flex items-center gap-2">
            <Icon name="dashboard_customize" className="text-blue-400" size={22} />
            <h2 className="text-white font-semibold text-lg">Customize dashboard</h2>
          </div>
          <button
            onClick={onClose}
            className="text-slate-500 hover:text-white transition-colors"
            aria-label="Close"
          >
            <Icon name="close" size={20} />
          </button>
        </div>

        <p className="text-xs text-slate-400 mb-4">
          Show or hide cards on your home dashboard. Drag to reorder.
        </p>

        <div className="space-y-2 max-h-[50vh] overflow-y-auto pr-1">
          <DndContext
            sensors={sensors}
            collisionDetection={closestCenter}
            onDragEnd={handleDragEnd}
          >
            <SortableContext
              items={rows.map((r) => r.id)}
              strategy={verticalListSortingStrategy}
            >
              {rows.map((row) => (
                <SortableRow key={row.id} row={row} onToggle={handleToggle} />
              ))}
            </SortableContext>
          </DndContext>
        </div>

        <div className="flex items-center justify-between gap-2 mt-6">
          <button
            onClick={handleReset}
            className="px-3 py-2 text-slate-400 hover:text-white text-sm rounded-lg transition-colors"
          >
            Reset to default
          </button>
          <div className="flex items-center gap-2">
            <button
              onClick={onClose}
              className="px-4 py-2 text-slate-400 hover:text-white text-sm rounded-lg transition-colors"
            >
              Cancel
            </button>
            <button
              onClick={handleSave}
              className="px-4 py-2 bg-blue-500 hover:bg-blue-600 text-white text-sm rounded-lg transition-colors"
            >
              Save
            </button>
          </div>
        </div>
      </div>
    </div>
  )
}
