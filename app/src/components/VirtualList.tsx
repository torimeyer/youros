import { useRef, useState } from 'react'
import { useWindowVirtualizer } from '@tanstack/react-virtual'

interface VirtualListProps<T> {
  items: T[]
  estimateSize?: (index: number) => number
  overscan?: number
  renderItem: (item: T, index: number) => React.ReactNode
  itemGap?: number
  className?: string
  'data-testid'?: string
}

function VirtualListCore<T>({
  items,
  estimateSize = () => 80,
  overscan = 10,
  renderItem,
  itemGap = 8,
  className,
  'data-testid': testId,
}: VirtualListProps<T>) {
  const containerRef = useRef<HTMLDivElement>(null)
  const [scrollMargin, setScrollMargin] = useState(0)
  const virtualizer = useWindowVirtualizer({
    count: items.length,
    estimateSize: (i) => estimateSize(i) + itemGap,
    overscan,
    scrollMargin,
  })
  return (
    <div
      ref={(el) => { containerRef.current = el; setScrollMargin(el?.offsetTop ?? 0) }}
      className={className}
      data-testid={testId}
      style={{ height: virtualizer.getTotalSize(), position: 'relative' }}
    >
      {virtualizer.getVirtualItems().map((vItem) => (
        <div
          key={vItem.index}
          data-index={vItem.index}
          ref={virtualizer.measureElement}
          style={{
            position: 'absolute',
            top: 0,
            left: 0,
            width: '100%',
            transform: `translateY(${vItem.start - scrollMargin}px)`,
            paddingBottom: itemGap,
          }}
        >
          {renderItem(items[vItem.index], vItem.index)}
        </div>
      ))}
    </div>
  )
}

// In environments without ResizeObserver (jsdom, SSR) the window virtualizer
// cannot measure the viewport and renders 0 items. Fall back to a plain list.
function VirtualListFallback<T>({
  items,
  renderItem,
  itemGap = 8,
  className,
  'data-testid': testId,
}: VirtualListProps<T>) {
  return (
    <div className={className} data-testid={testId}>
      {items.map((item, index) => (
        <div key={index} style={{ marginBottom: itemGap }}>
          {renderItem(item, index)}
        </div>
      ))}
    </div>
  )
}

const hasResizeObserver = typeof ResizeObserver !== 'undefined'

export function VirtualList<T>(props: VirtualListProps<T>) {
  if (hasResizeObserver) {
    return <VirtualListCore<T> {...props} />
  }
  return <VirtualListFallback<T> {...props} />
}
