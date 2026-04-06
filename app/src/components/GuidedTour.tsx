import { useState, useCallback, useEffect, useRef } from 'react'
import { useNavigate } from 'react-router-dom'

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
    selector: '[data-tour="sidebar"]',
    title: 'Sidebar',
    description: 'This is your menu. Use it to jump between different parts of your OS.',
    route: '/',
    position: 'right',
  },
  {
    selector: '[data-tour="dashboard"]',
    title: 'Dashboard',
    description: 'Your home screen. It shows what needs your attention right now.',
    route: '/',
    position: 'bottom',
  },
  {
    selector: '[data-tour="tasks"]',
    title: 'Tasks',
    description: 'Keep track of what you need to do. Add tasks, set priorities, and check things off.',
    route: '/tasks',
    position: 'bottom',
  },
  {
    selector: '[data-tour="chat"]',
    title: 'Chat',
    description: 'Talk to your AI assistant from any screen. Ask questions, get help, or just chat.',
    position: 'left',
  },
  {
    selector: '[data-tour="agents"]',
    title: 'Agents',
    description: 'Spawn background AI agents to work on tasks for you while you do other things.',
    route: '/agents',
    position: 'bottom',
  },
  {
    selector: '[data-tour="ideas"]',
    title: 'Ideas',
    description: 'Capture thoughts quickly. You can turn them into tasks later.',
    route: '/ideas',
    position: 'bottom',
  },
]

export default function GuidedTour({ onComplete }: GuidedTourProps) {
  const [step, setStep] = useState(0)
  const [targetRect, setTargetRect] = useState<DOMRect | null>(null)
  const tooltipRef = useRef<HTMLDivElement>(null)
  const navigate = useNavigate()

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
    // Small delay to let the page render after navigation
    const timer = setTimeout(findTarget, 300)
    return () => clearTimeout(timer)
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

  const handleNext = () => {
    if (step < total - 1) setStep(step + 1)
  }

  const handleBack = () => {
    if (step > 0) setStep(step - 1)
  }

  const handleFinish = () => {
    localStorage.setItem('myos-tour-complete', 'true')
    navigate('/')
    onComplete()
  }

  const handleSkip = () => {
    localStorage.setItem('myos-tour-complete', 'true')
    navigate('/')
    onComplete()
  }

  const isLast = step === total - 1
  const pad = 8 // padding around highlighted element

  // Calculate tooltip position relative to the target element
  const getTooltipStyle = (): React.CSSProperties => {
    if (!targetRect) return { top: '50%', left: '50%', transform: 'translate(-50%, -50%)' }

    const gap = 16
    switch (current.position) {
      case 'right':
        return {
          top: targetRect.top + targetRect.height / 2,
          left: targetRect.right + gap + pad,
          transform: 'translateY(-50%)',
        }
      case 'left':
        return {
          top: targetRect.top + targetRect.height / 2,
          right: window.innerWidth - targetRect.left + gap + pad,
          transform: 'translateY(-50%)',
        }
      case 'bottom':
        return {
          top: targetRect.bottom + gap + pad,
          left: targetRect.left + targetRect.width / 2,
          transform: 'translateX(-50%)',
        }
      case 'top':
        return {
          bottom: window.innerHeight - targetRect.top + gap + pad,
          left: targetRect.left + targetRect.width / 2,
          transform: 'translateX(-50%)',
        }
    }
  }

  // Arrow pointing from tooltip to target
  const getArrowStyle = (): React.CSSProperties & { borderClass: string } => {
    switch (current.position) {
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
        className="absolute bg-slate-900 border border-slate-700 rounded-xl shadow-2xl shadow-black/50 w-80 p-5"
        style={getTooltipStyle()}
      >
        {/* Arrow */}
        <div
          className={`absolute w-3 h-3 bg-slate-900 ${borderClass} border-slate-700`}
          style={arrowStyles}
        />

        {/* Step count */}
        <p className="text-[10px] text-slate-500 font-medium mb-3 tracking-wide uppercase">
          {step + 1} of {total}
        </p>

        {/* Title */}
        <h2 className="text-base font-bold text-white mb-1.5">{current.title}</h2>

        {/* Description */}
        <p className="text-sm text-slate-400 leading-relaxed mb-5">{current.description}</p>

        {/* Progress dots */}
        <div className="flex items-center gap-1 mb-4">
          {TOUR_STEPS.map((_, i) => (
            <div
              key={i}
              className={`h-1 rounded-full transition-all duration-300 ${
                i === step ? 'w-5 bg-blue-500' : i < step ? 'w-1.5 bg-blue-500/40' : 'w-1.5 bg-slate-700'
              }`}
            />
          ))}
        </div>

        {/* Actions */}
        <div className="flex items-center justify-between">
          <button
            onClick={step > 0 ? handleBack : handleSkip}
            className="text-xs text-slate-500 hover:text-slate-300 transition-colors"
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
