/**
 * TemplateCard: single shared card used in both the installed Templates grid
 * and the marketplace browser. Keeps card shape identical across both contexts.
 *
 * Props:
 *   name: display name (Title Case)
 *   description: one-line summary
 *   icon: Material Symbols name
 *   aliases: alternate names shown as chips (e.g. ["saa"])
 *   source: "builtin" | "marketplace" | "custom"
 *   installed: true when the template is already in the user's list
 *   onAction: called when the action button is clicked
 *   actionLabel: label for the action button (default: "Use")
 *   onUnfavorite: optional, shows an unfavorite (star) icon to remove from favorites
 *   capabilities: optional agentfile capability summary shown on hover
 *   parseError: when set, shows an error panel instead of capabilities
 *   testId: data-testid prefix
 */

import { useState } from 'react'
import Icon from './Icon'

export interface TemplateCapabilities {
  writes_to: string
  cannot_touch: string
  budget: string
  time_limit: string
  sandbox: string
}

export interface TemplateCardProps {
  name: string
  description: string
  icon: string
  aliases?: string[]
  source?: 'builtin' | 'marketplace' | 'custom' | string
  installed?: boolean
  favorited?: boolean
  onAction?: () => void
  actionLabel?: string
  onUnfavorite?: () => void
  capabilities?: TemplateCapabilities | null
  parseError?: string | null
  testId?: string
}

export default function TemplateCard({
  name,
  description,
  icon,
  aliases = [],
  source = 'builtin',
  installed = true,
  favorited = false,
  onAction,
  actionLabel = 'Use',
  onUnfavorite,
  capabilities,
  parseError,
  testId,
}: TemplateCardProps) {
  const [capHover, setCapHover] = useState(false)

  const accentColor =
    source === 'builtin'
      ? 'text-blue-400'
      : source === 'custom'
        ? 'text-purple-400'
        : 'text-slate-400'

  const hasParseError = parseError != null
  const showBadge = source === 'marketplace' || source === 'custom'

  return (
    <div
      className={`bg-slate-900/40 border rounded-xl p-5 flex flex-col gap-2 hover:border-slate-700 transition-colors ${favorited ? 'border-amber-500/40' : 'border-slate-800'}`}
      data-testid={testId ?? `template-card-${name}`}
    >
      {/* Top row: icon, body, action */}
      <div className="flex items-start gap-3">
        {/* Icon */}
        <div className="shrink-0 w-12 h-12 rounded-lg bg-slate-800 border border-slate-700 flex items-center justify-center">
          <Icon name={icon} className={accentColor} size={24} />
        </div>

        {/* Body */}
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-1.5 flex-wrap">
            <p className="text-white font-semibold text-lg truncate" title={name}>{name}</p>
            {favorited && (
              <span className="text-amber-400" title="Favorited" data-testid={`${testId ?? `template-card-${name}`}-favorited`}>
                <Icon name="star" size={14} />
              </span>
            )}
            {aliases.map((a) => (
              <span
                key={a}
                className="text-[10px] px-1.5 py-0.5 rounded bg-slate-700 text-slate-400 font-mono"
                data-testid={`alias-chip-${a}`}
              >
                alias: {a}
              </span>
            ))}
            {showBadge && (
              <span className={`text-[10px] px-1.5 py-0.5 rounded ${
                source === 'marketplace'
                  ? 'bg-slate-200 text-slate-700 dark:bg-slate-700/60 dark:text-slate-400'
                  : 'bg-purple-100 text-purple-700 dark:bg-purple-500/20 dark:text-purple-400'
              }`}>
                {source === 'marketplace' ? 'catalog' : 'custom'}
              </span>
            )}
          </div>
          <div className="flex items-center gap-1 mt-0.5">
            <p
              className="text-slate-400 text-xs line-clamp-2 flex-1 min-w-0"
              title={description}
            >
              {description}
            </p>
            {/* Capabilities info icon: only shown when capabilities are present and no parse error */}
            {!hasParseError && capabilities && (
              <div className="relative shrink-0">
                <button
                  type="button"
                  className="text-slate-600 hover:text-slate-400 transition-colors focus:outline-none"
                  aria-label={`Capabilities for ${name}`}
                  data-testid={`template-caps-info-${name}`}
                  onMouseEnter={() => setCapHover(true)}
                  onMouseLeave={() => setCapHover(false)}
                  onFocus={() => setCapHover(true)}
                  onBlur={() => setCapHover(false)}
                >
                  <Icon name="info" size={13} />
                </button>
                {capHover && (
                  <div
                    className="absolute right-0 bottom-full mb-1 z-10 w-52 bg-slate-800 border border-slate-700 rounded-lg p-2 shadow-lg pointer-events-none"
                    data-testid={`template-capabilities-${name}`}
                  >
                    <p className="text-[10px] text-slate-400 space-y-0.5">
                      <span className="block" title={`Writes to: ${capabilities.writes_to}`}>✎ {capabilities.writes_to}</span>
                      <span className="block" title={`Budget: ${capabilities.budget}`}>⌛ {capabilities.budget}</span>
                      <span className="block" title={`Time limit: ${capabilities.time_limit}`}>🕒 {capabilities.time_limit}</span>
                      <span className="block" title={`Sandbox: ${capabilities.sandbox}`}>🔒 {capabilities.sandbox}</span>
                    </p>
                  </div>
                )}
              </div>
            )}
          </div>
        </div>

        {/* Action */}
        <div className="shrink-0 flex flex-col gap-1 items-end">
          {onAction && !installed && (
            <button
              onClick={onAction}
              className="text-blue-400 hover:text-blue-300 transition-colors"
              title={`Add ${name} to your templates`}
              data-testid={testId ? `${testId}-action` : `template-card-${name}-action`}
            >
              <Icon name="add_circle" size={22} />
            </button>
          )}
          {onAction && installed && !hasParseError && (
            <button
              onClick={onAction}
              className="bg-pink-500 hover:bg-pink-600 text-white rounded-lg px-4 py-2 text-sm transition-colors"
              data-testid={testId ? `${testId}-action` : `template-card-${name}-action`}
            >
              {actionLabel}
            </button>
          )}
          {!onAction && installed && (
            <span className="text-green-400" title="Already added">
              <Icon name="check_circle" size={18} />
            </span>
          )}
          {onUnfavorite && (
            <button
              onClick={onUnfavorite}
              className="text-amber-400 hover:text-amber-300 transition-colors mt-1"
              title="Remove from favorites"
              data-testid={testId ? `${testId}-unfavorite` : `template-card-${name}-unfavorite`}
            >
              <Icon name="star" size={16} />
            </button>
          )}
        </div>
      </div>

      {/* Parse error panel (replaces capabilities) */}
      {hasParseError && (
        <div
          className="pt-2 border-t border-slate-700/50"
          data-testid={`template-capabilities-error-${name}`}
        >
          <p className="text-xs text-amber-400">
            Could not read capabilities for this template. Fix the .agent file.
          </p>
          <button
            type="button"
            disabled
            data-testid={`template-spawn-${name}`}
            className="w-full mt-2 py-1.5 bg-slate-800 text-slate-500 rounded-lg text-xs cursor-not-allowed"
          >
            Spawn
          </button>
        </div>
      )}
    </div>
  )
}
