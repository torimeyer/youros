import { useState, useCallback, useEffect, useLayoutEffect, useRef } from 'react'
import { useNavigate } from 'react-router-dom'
import { useAppStore } from '../stores/app'

interface GuidedTourProps {
  onComplete: () => void
}

interface TourStep {
  selector: string
  title: string
  description: string
  route?: string
  position: 'right' | 'bottom' | 'left' | 'top'
}

const TOUR_STEPS: TourStep[] = [
  {
    selector: '[data-tour="dashboard"]',
    title: 'Dashboard',
    description: 'Your home screen. It shows what needs your attention, a focus card for what to work on first, and a summary of what changed.',
    route: '/',
    position: 'bottom',
  },
  {
    selector: '[data-tour="tasks"]',
    title: 'Needles',
    description: 'Keep track of what you need to do. Add needles, set priorities, link them together, and organize with labels and groups.',
    route: '/tasks',
    position: 'bottom',
  },
  {
    selector: '[data-tour="specs"]',
    title: 'Specs',
    description: 'Turn an idea into a spec, then hit Build-it to have an agent write the code and open a pull request for you.',
    route: '/specs',
    position: 'bottom',
  },
  {
    selector: '[data-tour="chat"]',
    title: 'Chat',
    description: 'Talk to your AI assistant from any screen. Mention a model by name to switch, like @claude or @gemini.',
    position: 'left',
  },
  {
    selector: '[data-tour="agents"]',
    title: 'Agents',
    description: 'Send background AI agents to work on needles for you. See what they can do, hand off work, and track their progress.',
    route: '/agents',
    position: 'bottom',
  },
  {
    selector: '[data-tour="search"]',
    title: 'Search',
    description: 'Press Cmd+K to search across all your needles and jump anywhere fast. That is the tour. Try asking the chat a question next.',
    route: '/',
    position: 'bottom',
  },
]

export default function GuidedTour({ onComplete }: GuidedTourProps) {
  const [step, setStep] = useState(0)
  const [targetRect, setTargetRect] = useState<DOMRect | null>(null)
  // Measured tooltip size. We re-measure after every render so the clamp
  // and flip logic use the true rendered box, not a hardcoded guess.
  const [tooltipSize, setTooltipSize] = useState<{ width: number; height: number }>({
    width: 320,
    height: 260,
  })
  const tooltipRef = useRef<HTMLDivElement>(null)
  const navigate = useNavigate()
  const setTourComplete = useAppStore((s) => s.setTourComplete)

  const total = TOUR_STEPS.length
  const current = TOUR_STEPS[step]

  const findTarget = useCallback(() => {
    const el = document.querySelector(current.selector)
    if (el) {
      const rect = el.getBoundingClientRect()
      setTargetRect(rect)
      el.scrollIntoView({ behavior: 'smooth', block: 'nearest' })
    } else {
      setTargetRect(null)
    }
  }, [current.selector])

  useEffect(() => {
    if (current.route) {
      navigate(current.route)
    }
    // Use rAF for snappy positioning after paint, with a short fallback
    // for elements that render asynchronously after navigation
    let raf: number
    let timer: ReturnType<typeof setTimeout>
    raf = requestAnimationFrame(() => {
      findTarget()
      // Retry once in case the element wasn't in the DOM yet
      timer = setTimeout(findTarget, 80)
    })
    return () => {
      cancelAnimationFrame(raf)
      clearTimeout(timer)
    }
  }, [step, current.route, navigate, findTarget])

  // Reposition on scroll/resize
  useEffect(() => {
    window.addEventListener('resize', findTarget)
    window.addEventListener('scroll', findTarget, true)
    return () => {
      window.removeEventListener('resize', findTarget)
      window.removeEventListener('scroll', findTarget, true)
    }
  }, [findTarget])

  // Measure the tooltip's actual rendered box after every render and when it
  // resizes. The hardcoded 260px fallback used by the first paint can be
  // smaller or larger than reality, so without this the clamp can leave the
  // tooltip clipped above or below the viewport.
  useLayoutEffect(() => {
    const el = tooltipRef.current
    if (!el) return
    const update = () => {
      const rect = el.getBoundingClientRect()
      // If the tooltip has no layout yet (e.g. jsdom test env or first paint
      // before styles apply), keep the fallback size so the clamp still
      // guards against off-screen positioning.
      if (rect.width < 1 || rect.height < 1) return
      setTooltipSize((prev) => {
        if (
          Math.abs(prev.width - rect.width) < 0.5 &&
          Math.abs(prev.height - rect.height) < 0.5
        ) {
          return prev
        }
        return { width: rect.width, height: rect.height }
      })
    }
    update()
    // ResizeObserver catches font loads, wrap changes, and step-to-step copy
    // swaps that change the tooltip's height.
    if (typeof ResizeObserver !== 'undefined') {
      const ro = new ResizeObserver(update)
      ro.observe(el)
      return () => ro.disconnect()
    }
  }, [step, targetRect])

  // Escape skips the whole tour (same as the Skip button). We close over
  // the latest handleSkip via a ref so this effect can run once and
  // never re-subscribe on each render.
  const handleSkipRef = useRef<() => void>(() => {})
  useEffect(() => {
    handleSkipRef.current = () => {
      setTourComplete(true)
      navigate('/')
      onComplete()
    }
  })
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') handleSkipRef.current()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [])

  const handleNext = () => {
    if (step < total - 1) setStep(step + 1)
  }

  const handleBack = () => {
    if (step > 0) setStep(step - 1)
  }

  const handleFinish = () => {
    setTourComplete(true)
    navigate('/')
    onComplete()
  }

  const handleSkip = () => {
    setTourComplete(true)
    navigate('/')
    onComplete()
  }

  const isLast = step === total - 1
  const pad = 8 // padding around highlighted element
  const VIEWPORT_MARGIN = 16

  // Clamp a value so the tooltip stays fully on screen with a 16px margin
  const clamp = (value: number, size: number, viewport: number): number => {
    const min = VIEWPORT_MARGIN
    const max = Math.max(min, viewport - size - VIEWPORT_MARGIN)
    return Math.min(Math.max(value, min), max)
  }

  // Pick the best placement for this step. The step config gives a preferred
  // side, but if putting the tooltip on that side would push it off the top
  // or bottom of the viewport, flip to the opposite side. This keeps the
  // tooltip fully visible even when the anchor is near the edge of the
  // viewport or is taller than the viewport (e.g. a whole-page container).
  const resolvePlacement = (
    rect: DOMRect,
    preferred: TourStep['position'],
    tooltipH: number,
    tooltipW: number,
    vh: number,
    vw: number
  ): TourStep['position'] => {
    const gap = 16
    const spaceAbove = rect.top - gap - pad - VIEWPORT_MARGIN
    const spaceBelow = vh - rect.bottom - gap - pad - VIEWPORT_MARGIN
    const spaceLeft = rect.left - gap - pad - VIEWPORT_MARGIN
    const spaceRight = vw - rect.right - gap - pad - VIEWPORT_MARGIN

    if (preferred === 'top' && spaceAbove < tooltipH && spaceBelow > spaceAbove) {
      return 'bottom'
    }
    if (preferred === 'bottom' && spaceBelow < tooltipH && spaceAbove > spaceBelow) {
      return 'top'
    }
    if (preferred === 'left' && spaceLeft < tooltipW && spaceRight > spaceLeft) {
      return 'right'
    }
    if (preferred === 'right' && spaceRight < tooltipW && spaceLeft > spaceRight) {
      return 'left'
    }
    return preferred
  }

  // Effective placement used by both the tooltip body and the arrow so they
  // stay in sync after a flip.
  const effectivePlacement: TourStep['position'] = (() => {
    if (!targetRect) return current.position
    const vw = typeof window !== 'undefined' ? window.innerWidth : 1024
    const vh = typeof window !== 'undefined' ? window.innerHeight : 768
    return resolvePlacement(targetRect, current.position, tooltipSize.height, tooltipSize.width, vh, vw)
  })()

  // Calculate tooltip position relative to the target element.
  // All positions are expressed as absolute top/left pixel values so we can
  // clamp them into the viewport. We avoid right/bottom anchoring and CSS
  // transforms for positioning so the clamp math is straightforward.
  const getTooltipStyle = (): React.CSSProperties => {
    const vw = typeof window !== 'undefined' ? window.innerWidth : 1024
    const vh = typeof window !== 'undefined' ? window.innerHeight : 768
    const { width: TOOLTIP_W, height: TOOLTIP_H } = tooltipSize

    if (!targetRect) {
      return {
        top: Math.max(VIEWPORT_MARGIN, (vh - TOOLTIP_H) / 2),
        left: Math.max(VIEWPORT_MARGIN, (vw - TOOLTIP_W) / 2),
      }
    }

    const gap = 16
    let top = 0
    let left = 0
    switch (effectivePlacement) {
      case 'right':
        top = targetRect.top + targetRect.height / 2 - TOOLTIP_H / 2
        left = targetRect.right + gap + pad
        break
      case 'left':
        top = targetRect.top + targetRect.height / 2 - TOOLTIP_H / 2
        left = targetRect.left - gap - pad - TOOLTIP_W
        break
      case 'bottom':
        top = targetRect.bottom + gap + pad
        left = targetRect.left + targetRect.width / 2 - TOOLTIP_W / 2
        break
      case 'top':
        top = targetRect.top - gap - pad - TOOLTIP_H
        left = targetRect.left + targetRect.width / 2 - TOOLTIP_W / 2
        break
    }

    return {
      top: clamp(top, TOOLTIP_H, vh),
      left: clamp(left, TOOLTIP_W, vw),
    }
  }

  // Arrow pointing from tooltip to target. Uses the effective (possibly
  // flipped) placement, not the preferred one, so the arrow always points
  // back at the anchor.
  const getArrowStyle = (): React.CSSProperties & { borderClass: string } => {
    switch (effectivePlacement) {
      case 'right':
        return { top: '50%', left: -6, transform: 'translateY(-50%) rotate(45deg)', borderClass: 'border-l border-b' }
      case 'left':
        return { top: '50%', right: -6, transform: 'translateY(-50%) rotate(45deg)', borderClass: 'border-r border-t' }
      case 'bottom':
        return { top: -6, left: '50%', transform: 'translateX(-50%) rotate(45deg)', borderClass: 'border-l border-t' }
      case 'top':
        return { bottom: -6, left: '50%', transform: 'translateX(-50%) rotate(45deg)', borderClass: 'border-r border-b' }
    }
  }

  const arrowInfo = getArrowStyle()
  const { borderClass, ...arrowStyles } = arrowInfo

  return (
    <div className="fixed inset-0 z-[9999]">
      {/* Dark overlay with cutout for the highlighted element */}
      <svg className="absolute inset-0 w-full h-full" style={{ pointerEvents: 'none' }}>
        <defs>
          <mask id="tour-mask">
            <rect x="0" y="0" width="100%" height="100%" fill="white" />
            {targetRect && (
              <rect
                x={targetRect.left - pad}
                y={targetRect.top - pad}
                width={targetRect.width + pad * 2}
                height={targetRect.height + pad * 2}
                rx="12"
                fill="black"
              />
            )}
          </mask>
        </defs>
        <rect
          x="0" y="0" width="100%" height="100%"
          fill="rgba(0,0,0,0.7)"
          mask="url(#tour-mask)"
          style={{ pointerEvents: 'auto' }}
          onClick={handleSkip}
        />
      </svg>

      {/* Highlight border around target */}
      {targetRect && (
        <div
          className="absolute border-2 border-blue-500 rounded-xl pointer-events-none"
          style={{
            top: targetRect.top - pad,
            left: targetRect.left - pad,
            width: targetRect.width + pad * 2,
            height: targetRect.height + pad * 2,
            boxShadow: '0 0 0 4px rgba(59, 130, 246, 0.2)',
          }}
        />
      )}

      {/* Tooltip card */}
      <div
        ref={tooltipRef}
        data-testid="tour-tooltip"
        className="absolute bg-white dark:bg-slate-900 border border-slate-200 dark:border-slate-700 rounded-xl shadow-2xl shadow-black/50 w-80 p-5"
        style={getTooltipStyle()}
      >
        {/* Arrow */}
        <div
          className={`absolute w-3 h-3 bg-white dark:bg-slate-900 ${borderClass} border-slate-200 dark:border-slate-700`}
          style={arrowStyles}
        />

        {/* Step count */}
        <p className="text-[10px] text-slate-500 font-medium mb-3 tracking-wide uppercase">
          {step + 1} of {total}
        </p>

        {/* Title */}
        <h2 className="text-base font-bold text-white mb-1.5">{current.title}</h2>

        {/* Description */}
        <p className="text-sm text-slate-600 dark:text-slate-400 leading-relaxed mb-5">{current.description}</p>

        {/* Progress dots */}
        <div className="flex items-center gap-1 mb-4">
          {TOUR_STEPS.map((_, i) => (
            <div
              key={i}
              className={`h-1 rounded-full transition-all duration-300 ${
                i === step ? 'w-5 bg-blue-500' : i < step ? 'w-1.5 bg-blue-500/40' : 'w-1.5 bg-slate-200 dark:bg-slate-700'
              }`}
            />
          ))}
        </div>

        {/* Actions */}
        <div className="flex items-center justify-between">
          <button
            onClick={step > 0 ? handleBack : handleSkip}
            className="text-xs text-slate-500 hover:text-slate-700 dark:hover:text-slate-300 transition-colors"
          >
            {step > 0 ? 'Back' : 'Skip tour'}
          </button>

          {isLast ? (
            <button
              onClick={handleFinish}
              className="px-4 py-1.5 bg-blue-600 hover:bg-blue-500 rounded-lg text-sm font-medium transition-colors"
            >
              Finish
            </button>
          ) : (
            <button
              onClick={handleNext}
              className="px-4 py-1.5 bg-blue-600 hover:bg-blue-500 rounded-lg text-sm font-medium transition-colors"
            >
              Next
            </button>
          )}
        </div>
      </div>
    </div>
  )
}
