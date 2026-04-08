import { useState } from 'react'
import Icon from './Icon'
import { api } from '../lib/api'

/*
 * SlideBeautifier: "Make it pretty" button and before and after viewer.
 * Mounted in FilePreviewPane.tsx inside the slides branch.
 */

// Types that mirror the shape returned by POST /api/files/beautify-deck.

interface OriginalSlide {
  index: number
  title: string
  bullets: string[]
  body: string
  image_count: number
  notes: string
}

type LayoutKind =
  | 'title'
  | 'title_and_content'
  | 'two_column'
  | 'image_left'
  | 'image_right'
  | 'quote'
  | 'section_header'
  | 'comparison'

interface FontPairing {
  heading: string
  body: string
}

interface BeautifiedSlide {
  index: number
  layout: LayoutKind
  title: string
  body: string[] | { left: string[]; right: string[] }
  accent_color: string
  font_pairing: FontPairing
  notes: string
  rationale: string
}

interface BeautifyResponse {
  name: string
  slide_count: number
  originals: OriginalSlide[]
  beautified: BeautifiedSlide[]
  cached: boolean
  elapsed_seconds?: number
}

interface SlideBeautifierProps {
  filePath: string
  fileName?: string
}

// Map an AI font name to a safe CSS stack. Keeps the frontend from loading
// fonts it does not have, while still passing the character of the name.
function fontStack(name: string): string {
  switch (name) {
    case 'Inter':
      return 'Inter, system-ui, sans-serif'
    case 'Source Sans':
      return '"Source Sans 3", "Source Sans Pro", system-ui, sans-serif'
    case 'Playfair':
      return '"Playfair Display", Georgia, serif'
    case 'Merriweather':
      return 'Merriweather, Georgia, serif'
    case 'JetBrains Mono':
      return '"JetBrains Mono", ui-monospace, monospace'
    case 'Space Grotesk':
      return '"Space Grotesk", system-ui, sans-serif'
    default:
      return 'system-ui, sans-serif'
  }
}

function isTwoColumn(
  body: BeautifiedSlide['body'],
): body is { left: string[]; right: string[] } {
  return (
    !Array.isArray(body) &&
    typeof body === 'object' &&
    body !== null &&
    'left' in body
  )
}

// A small renderer for the AI's layout types. Plain HTML and Tailwind, kept
// deliberately light so the output reads clean instead of cluttered.
function BeautifiedSlideView({ slide }: { slide: BeautifiedSlide }) {
  const accent = slide.accent_color
  const headingStyle = { fontFamily: fontStack(slide.font_pairing.heading) }
  const bodyStyle = { fontFamily: fontStack(slide.font_pairing.body) }

  const card = 'relative w-full aspect-[16/9] rounded-xl overflow-hidden bg-white text-slate-900 shadow-sm'

  if (slide.layout === 'title') {
    return (
      <div className={card} data-testid="beautified-title">
        <div className="absolute inset-0 flex items-center justify-center p-10">
          <h2
            className="text-4xl font-bold text-center leading-tight"
            style={{ ...headingStyle, color: accent }}
          >
            {slide.title}
          </h2>
        </div>
      </div>
    )
  }

  if (slide.layout === 'section_header') {
    return (
      <div
        className={card}
        data-testid="beautified-section"
        style={{ background: accent }}
      >
        <div className="absolute inset-0 flex items-center justify-start p-10">
          <h2 className="text-4xl font-bold text-white leading-tight" style={headingStyle}>
            {slide.title}
          </h2>
        </div>
      </div>
    )
  }

  if (slide.layout === 'quote') {
    const quoteLines = Array.isArray(slide.body) ? slide.body : []
    return (
      <div className={card} data-testid="beautified-quote">
        <div className="absolute inset-0 flex flex-col items-center justify-center p-10 text-center">
          <p className="text-2xl italic leading-snug" style={bodyStyle}>
            {quoteLines[0] || slide.title}
          </p>
          {quoteLines[1] && (
            <p className="mt-4 text-sm" style={{ ...bodyStyle, color: accent }}>
              {quoteLines[1]}
            </p>
          )}
        </div>
      </div>
    )
  }

  if (slide.layout === 'two_column' || slide.layout === 'comparison') {
    const cols = isTwoColumn(slide.body)
      ? slide.body
      : { left: Array.isArray(slide.body) ? slide.body.slice(0, Math.ceil(slide.body.length / 2)) : [], right: Array.isArray(slide.body) ? slide.body.slice(Math.ceil(slide.body.length / 2)) : [] }
    return (
      <div className={card} data-testid="beautified-twocol">
        <div className="absolute inset-0 p-8 flex flex-col">
          <h3 className="text-xl font-semibold mb-4" style={{ ...headingStyle, color: accent }}>
            {slide.title}
          </h3>
          <div className="grid grid-cols-2 gap-4 flex-1">
            <ul className="space-y-2" style={bodyStyle}>
              {cols.left.map((item, i) => (
                <li key={i} className="text-sm leading-snug">{item}</li>
              ))}
            </ul>
            <ul className="space-y-2" style={bodyStyle}>
              {cols.right.map((item, i) => (
                <li key={i} className="text-sm leading-snug">{item}</li>
              ))}
            </ul>
          </div>
        </div>
      </div>
    )
  }

  if (slide.layout === 'image_left' || slide.layout === 'image_right') {
    const bullets = Array.isArray(slide.body) ? slide.body : []
    const imageBlock = (
      <div
        className="w-full h-full flex items-center justify-center text-slate-400"
        style={{ background: `${accent}22` }}
      >
        <Icon name="image" className="text-4xl" />
      </div>
    )
    const contentBlock = (
      <div className="p-6 flex flex-col justify-center">
        <h3 className="text-xl font-semibold mb-3" style={{ ...headingStyle, color: accent }}>
          {slide.title}
        </h3>
        <ul className="space-y-2" style={bodyStyle}>
          {bullets.map((item, i) => (
            <li key={i} className="text-sm leading-snug">{item}</li>
          ))}
        </ul>
      </div>
    )
    return (
      <div className={card} data-testid="beautified-image">
        <div className="absolute inset-0 grid grid-cols-2">
          {slide.layout === 'image_left' ? imageBlock : contentBlock}
          {slide.layout === 'image_left' ? contentBlock : imageBlock}
        </div>
      </div>
    )
  }

  // Default: title_and_content
  const bullets = Array.isArray(slide.body) ? slide.body : []
  return (
    <div className={card} data-testid="beautified-content">
      <div className="absolute inset-0 p-8 flex flex-col">
        <h3 className="text-2xl font-bold mb-5" style={{ ...headingStyle, color: accent }}>
          {slide.title}
        </h3>
        <ul className="space-y-3" style={bodyStyle}>
          {bullets.map((item, i) => (
            <li key={i} className="text-base leading-snug flex gap-3">
              <span
                className="inline-block w-2 h-2 rounded-full mt-2 flex-shrink-0"
                style={{ background: accent }}
              />
              <span>{item}</span>
            </li>
          ))}
        </ul>
      </div>
    </div>
  )
}

// Render an original slide as a simple panel. This is intentionally plain
// so the comparison with the polished version is clear.
function OriginalSlideView({ slide }: { slide: OriginalSlide }) {
  return (
    <div className="relative w-full aspect-[16/9] rounded-xl overflow-hidden bg-slate-50 text-slate-800 shadow-sm border border-slate-200">
      <div className="absolute inset-0 p-8 flex flex-col">
        {slide.title && (
          <h3 className="text-xl font-semibold mb-3 text-slate-900">{slide.title}</h3>
        )}
        {slide.bullets.length > 0 && (
          <ul className="space-y-2">
            {slide.bullets.map((b, i) => (
              <li key={i} className="text-sm text-slate-700 leading-snug">
                {b}
              </li>
            ))}
          </ul>
        )}
        {slide.body && (
          <p className="mt-3 text-sm text-slate-700 whitespace-pre-wrap">{slide.body}</p>
        )}
        {!slide.title && slide.bullets.length === 0 && !slide.body && (
          <p className="text-xs text-slate-400 italic">Empty slide</p>
        )}
      </div>
    </div>
  )
}

export default function SlideBeautifier({ filePath, fileName }: SlideBeautifierProps) {
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [data, setData] = useState<BeautifyResponse | null>(null)
  // Track which version is showing per slide. "before" or "after".
  const [view, setView] = useState<Record<number, 'before' | 'after'>>({})

  const run = async () => {
    setLoading(true)
    setError(null)
    setData(null)
    try {
      const res = await api.post<BeautifyResponse>('/files/beautify-deck', {
        path: filePath,
      })
      setData(res)
      // Default every slide to the polished version so the before and after
      // shows the improvement up front.
      const initial: Record<number, 'before' | 'after'> = {}
      res.beautified.forEach((s) => {
        initial[s.index] = 'after'
      })
      setView(initial)
    } catch (err) {
      let msg = 'Something went wrong while polishing your slides.'
      if (err instanceof Error && err.message) {
        try {
          const parsed = JSON.parse(err.message)
          if (parsed && typeof parsed.detail === 'string') msg = parsed.detail
        } catch {
          msg = err.message
        }
      }
      setError(msg)
    } finally {
      setLoading(false)
    }
  }

  const useAllPolished = () => {
    if (!data) return
    const next: Record<number, 'before' | 'after'> = {}
    data.beautified.forEach((s) => {
      next[s.index] = 'after'
    })
    setView(next)
  }

  const useAllOriginal = () => {
    if (!data) return
    const next: Record<number, 'before' | 'after'> = {}
    data.beautified.forEach((s) => {
      next[s.index] = 'before'
    })
    setView(next)
  }

  return (
    <div className="w-full">
      {/* Button row */}
      <div className="flex items-center justify-between gap-3 mb-4">
        <div className="min-w-0">
          {fileName && (
            <p className="text-sm text-slate-400 truncate">{fileName}</p>
          )}
        </div>
        <button
          onClick={run}
          disabled={loading}
          className="flex items-center gap-2 px-4 py-2 bg-blue-600 hover:bg-blue-500 disabled:bg-slate-700 disabled:text-slate-400 rounded-lg text-sm text-white font-medium transition-colors"
        >
          <Icon name="auto_awesome" size={16} />
          {loading ? 'Polishing your slides...' : 'Make it pretty'}
        </button>
      </div>

      {loading && (
        <div className="flex items-center gap-3 p-5 bg-slate-900/40 border border-slate-800 rounded-xl text-sm text-slate-300">
          <Icon name="auto_awesome" className="text-blue-400 animate-pulse" />
          <span>Polishing your slides. This takes a few seconds per slide.</span>
        </div>
      )}

      {error && (
        <div
          role="alert"
          className="flex items-center gap-3 p-4 bg-red-500/10 border border-red-500/30 rounded-lg text-red-400 text-sm mb-4"
        >
          <Icon name="error" className="text-lg" />
          <span>{error}</span>
        </div>
      )}

      {data && data.beautified.length > 0 && (
        <div className="space-y-6">
          {/* Master action row */}
          <div className="flex flex-wrap items-center justify-between gap-3 bg-slate-900/40 border border-slate-800 rounded-xl p-4">
            <div>
              <p className="text-sm text-slate-200 font-medium">
                Polished {data.beautified.length} slide
                {data.beautified.length === 1 ? '' : 's'}
              </p>
              {data.cached && (
                <p className="text-xs text-slate-500 mt-0.5">
                  Shown from cache. Your file has not changed since the last run.
                </p>
              )}
            </div>
            <div className="flex gap-2">
              <button
                onClick={useAllPolished}
                className="px-3 py-1.5 bg-blue-600 hover:bg-blue-500 rounded-lg text-xs text-white font-medium transition-colors"
              >
                Use these for the whole deck
              </button>
              <button
                onClick={useAllOriginal}
                className="px-3 py-1.5 bg-slate-800 hover:bg-slate-700 rounded-lg text-xs text-slate-300 transition-colors border border-slate-700"
              >
                Keep my originals
              </button>
            </div>
          </div>

          {/* Per slide comparison */}
          {data.beautified.map((slide) => {
            const original = data.originals.find((o) => o.index === slide.index)
            const currentView = view[slide.index] ?? 'after'
            return (
              <div
                key={slide.index}
                className="bg-slate-900/40 border border-slate-800 p-5 rounded-xl"
              >
                <div className="flex items-center justify-between mb-4">
                  <p className="text-xs uppercase tracking-widest text-slate-500 font-bold">
                    Slide {slide.index}
                  </p>
                  <div className="flex gap-1">
                    <button
                      onClick={() =>
                        setView((v) => ({ ...v, [slide.index]: 'before' }))
                      }
                      className={`px-3 py-1 rounded-lg text-xs transition-colors ${
                        currentView === 'before'
                          ? 'bg-slate-700 text-white'
                          : 'bg-slate-800 text-slate-400 hover:text-white'
                      }`}
                    >
                      Keep original
                    </button>
                    <button
                      onClick={() =>
                        setView((v) => ({ ...v, [slide.index]: 'after' }))
                      }
                      className={`px-3 py-1 rounded-lg text-xs transition-colors ${
                        currentView === 'after'
                          ? 'bg-blue-600 text-white'
                          : 'bg-slate-800 text-slate-400 hover:text-white'
                      }`}
                    >
                      Use polished
                    </button>
                  </div>
                </div>

                <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                  <div>
                    <p className="text-[11px] uppercase tracking-wider text-slate-500 mb-2">
                      Before
                    </p>
                    {original ? (
                      <OriginalSlideView slide={original} />
                    ) : (
                      <div className="text-xs text-slate-500">
                        Original not available.
                      </div>
                    )}
                  </div>
                  <div>
                    <p className="text-[11px] uppercase tracking-wider text-slate-500 mb-2">
                      After
                    </p>
                    <BeautifiedSlideView slide={slide} />
                  </div>
                </div>

                <p className="mt-4 text-sm text-slate-400">
                  <span className="text-slate-500">What changed. </span>
                  {slide.rationale}
                </p>
              </div>
            )
          })}
        </div>
      )}
    </div>
  )
}
