import Icon from '../Icon'
import type { GameDef } from './registry'

export default function GameTile({
  game,
  onPlay,
}: {
  game: GameDef
  onPlay: (id: string) => void
}) {
  return (
    <button
      type="button"
      data-testid={`game-tile-${game.id}`}
      onClick={() => onPlay(game.id)}
      className="group flex flex-col items-start gap-3 rounded-xl border border-slate-200 bg-white p-5 text-left shadow-sm transition hover:-translate-y-0.5 hover:shadow-md dark:border-slate-800 dark:bg-slate-900"
    >
      <span className="flex h-12 w-12 items-center justify-center rounded-lg bg-slate-100 accent-text dark:bg-slate-800">
        <Icon name={game.icon} className="text-2xl" />
      </span>
      <span className="text-base font-semibold text-slate-900 dark:text-white">{game.name}</span>
      <span className="text-sm text-slate-600 dark:text-slate-400">{game.blurb}</span>
      <span className="mt-auto text-xs font-medium uppercase tracking-wide text-slate-500 dark:text-slate-500">
        {game.lane === 'logic' ? 'Logic' : 'Arcade'}
      </span>
    </button>
  )
}
