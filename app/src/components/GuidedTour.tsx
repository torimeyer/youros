import { useState, useCallback } from 'react'
import Icon from './Icon'

interface GuidedTourProps {
  onComplete: () => void
}

interface TourStep {
  icon: string
  title: string
  description: string
}

const TOUR_STEPS: TourStep[] = [
  {
    icon: 'menu',
    title: 'Sidebar',
    description: 'This is your menu. Use it to jump between different parts of your OS.',
  },
  {
    icon: 'home',
    title: 'Dashboard',
    description: 'This is your home screen. It shows what needs your attention right now.',
  },
  {
    icon: 'checklist',
    title: 'Tasks',
    description: 'Keep track of what you need to do. Add tasks, set priorities, and check things off.',
  },
  {
    icon: 'chat',
    title: 'Chat',
    description: 'Talk to your AI assistant from any screen. Ask questions, get help, or just chat.',
  },
  {
    icon: 'lightbulb',
    title: 'Ideas',
    description: 'Capture thoughts and ideas quickly. You can turn them into tasks later.',
  },
  {
    icon: 'search',
    title: 'Command Palette',
    description: 'Press Cmd+K anytime to search and jump to anything quickly.',
  },
]

export default function GuidedTour({ onComplete }: GuidedTourProps) {
  const [step, setStep] = useState(0)
  const [animating, setAnimating] = useState(false)

  const total = TOUR_STEPS.length
  const current = TOUR_STEPS[step]

  const goTo = useCallback(
    (next: number) => {
      setAnimating(true)
      setTimeout(() => {
        setStep(next)
        setAnimating(false)
      }, 200)
    },
    [],
  )

  const handleNext = () => {
    if (step < total - 1) {
      goTo(step + 1)
    }
  }

  const handleBack = () => {
    if (step > 0) {
      goTo(step - 1)
    }
  }

  const handleFinish = () => {
    localStorage.setItem('youros-tour-complete', 'true')
    onComplete()
  }

  const handleSkip = () => {
    localStorage.setItem('youros-tour-complete', 'true')
    onComplete()
  }

  const isLast = step === total - 1

  return (
    <div className="fixed inset-0 z-[9999] flex items-center justify-center">
      {/* Backdrop */}
      <div className="absolute inset-0 bg-black/70 backdrop-blur-sm" />

      {/* Card */}
      <div
        className={`relative bg-slate-900 border border-slate-700 rounded-2xl shadow-2xl shadow-black/50 w-full max-w-md mx-4 p-8 transition-all duration-200 ${
          animating ? 'opacity-0 scale-95' : 'opacity-100 scale-100'
        }`}
      >
        {/* Step indicator */}
        <p className="text-xs text-slate-500 font-medium mb-6 tracking-wide uppercase">
          {step + 1} of {total}
        </p>

        {/* Icon */}
        <div className="w-16 h-16 rounded-2xl bg-blue-500/15 border border-blue-500/20 flex items-center justify-center mb-5">
          <Icon name={current.icon} className="text-blue-400" size={32} />
        </div>

        {/* Title */}
        <h2 className="text-xl font-bold text-white mb-2">{current.title}</h2>

        {/* Description */}
        <p className="text-sm text-slate-400 leading-relaxed mb-8">{current.description}</p>

        {/* Progress dots */}
        <div className="flex items-center gap-1.5 mb-6">
          {TOUR_STEPS.map((_, i) => (
            <div
              key={i}
              className={`h-1.5 rounded-full transition-all duration-300 ${
                i === step ? 'w-6 bg-blue-500' : i < step ? 'w-1.5 bg-blue-500/40' : 'w-1.5 bg-slate-700'
              }`}
            />
          ))}
        </div>

        {/* Actions */}
        <div className="flex items-center justify-between">
          <div>
            {step > 0 ? (
              <button
                onClick={handleBack}
                className="text-sm text-slate-400 hover:text-white transition-colors px-3 py-1.5 rounded-lg hover:bg-slate-800"
              >
                Back
              </button>
            ) : (
              <button
                onClick={handleSkip}
                className="text-sm text-slate-500 hover:text-slate-300 transition-colors"
              >
                Skip tour
              </button>
            )}
          </div>

          <div className="flex items-center gap-3">
            {step > 0 && (
              <button
                onClick={handleSkip}
                className="text-sm text-slate-500 hover:text-slate-300 transition-colors"
              >
                Skip tour
              </button>
            )}
            {isLast ? (
              <button
                onClick={handleFinish}
                className="px-5 py-2 bg-blue-600 hover:bg-blue-500 rounded-lg text-sm font-medium transition-colors"
              >
                Finish
              </button>
            ) : (
              <button
                onClick={handleNext}
                className="px-5 py-2 bg-blue-600 hover:bg-blue-500 rounded-lg text-sm font-medium transition-colors"
              >
                Next
              </button>
            )}
          </div>
        </div>
      </div>
    </div>
  )
}
