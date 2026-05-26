import { useCallback, useState } from 'react'
import { scorePegs, isWin, randomCode, type PegScore } from './mastermindLogic'
import { useConfirm } from '../../../hooks/useConfirm'
import ConfirmModal from '../../ConfirmModal'

const COLORS = [
  { key: 'red', cls: 'bg-red-500' },
  { key: 'orange', cls: 'bg-orange-500' },
  { key: 'yellow', cls: 'bg-yellow-400' },
  { key: 'green', cls: 'bg-green-500' },
  { key: 'blue', cls: 'bg-blue-500' },
  { key: 'purple', cls: 'bg-purple-500' },
]
const KEYS = COLORS.map((c) => c.key)
const LEN = 4
const MAX = 10
const clsOf = (k: string) => COLORS.find((c) => c.key === k)?.cls ?? 'bg-slate-300'

function Dot({ k }: { k: string }) {
  return <span className={`inline-block h-6 w-6 rounded-full ${clsOf(k)}`} />
}

function Pegs({ score }: { score: PegScore }) {
  const marks = [
    ...Array(score.exact).fill('exact'),
    ...Array(score.partial).fill('partial'),
    ...Array(LEN - score.exact - score.partial).fill('none'),
  ]
  return (
    <span className="grid grid-cols-2 gap-0.5">
      {marks.map((m, i) => (
        <span
          key={i}
          className={`h-2 w-2 rounded-full ${
            m === 'exact'
              ? 'bg-slate-900 dark:bg-white'
              : m === 'partial'
                ? 'border border-slate-500'
                : 'bg-slate-200 dark:bg-slate-700'
          }`}
        />
      ))}
    </span>
  )
}

export default function Mastermind() {
  const { confirm, confirmProps } = useConfirm()
  const [secret, setSecret] = useState<string[]>(() => randomCode(KEYS, LEN))
  const [guesses, setGuesses] = useState<{ pegs: string[]; score: PegScore }[]>([])
  const [current, setCurrent] = useState<string[]>([])
  const [status, setStatus] = useState<'playing' | 'won' | 'lost'>('playing')

  const reset = useCallback(() => {
    setSecret(randomCode(KEYS, LEN))
    setGuesses([])
    setCurrent([])
    setStatus('playing')
  }, [])

  function pick(k: string) {
    if (status !== 'playing' || current.length >= LEN) return
    setCurrent([...current, k])
  }

  function submit() {
    if (current.length !== LEN || status !== 'playing') return
    const score = scorePegs(secret, current)
    const ng = [...guesses, { pegs: current, score }]
    setGuesses(ng)
    setCurrent([])
    if (isWin(score, LEN)) {
      setStatus('won')
      void confirm({
        title: 'Cracked it',
        message: `Solved in ${ng.length} guess${ng.length === 1 ? '' : 'es'}. Play again?`,
        confirmLabel: 'Play again',
        cancelLabel: 'Done',
      }).then((a) => {
        if (a) reset()
      })
    } else if (ng.length >= MAX) {
      setStatus('lost')
      void confirm({
        title: 'Out of guesses',
        message: 'The code is revealed below. Play again?',
        confirmLabel: 'Play again',
        cancelLabel: 'Done',
      }).then((a) => {
        if (a) reset()
      })
    }
  }

  return (
    <div className="flex flex-col gap-4">
      <p className="text-sm text-slate-600 dark:text-slate-400">
        Guess the 4-color code. Filled peg = right color and spot; outline peg = right color, wrong spot.
      </p>
      <div className="flex flex-col gap-1">
        {guesses.map((g, i) => (
          <div key={i} className="flex items-center gap-3">
            <span className="flex gap-1">
              {g.pegs.map((k, j) => (
                <Dot key={j} k={k} />
              ))}
            </span>
            <Pegs score={g.score} />
          </div>
        ))}
      </div>
      {status === 'playing' ? (
        <div className="flex flex-wrap items-center gap-3">
          <span className="flex gap-1">
            {Array.from({ length: LEN }).map((_, i) =>
              current[i] ? (
                <Dot key={i} k={current[i]} />
              ) : (
                <span key={i} className="inline-block h-6 w-6 rounded-full border border-dashed border-slate-400" />
              ),
            )}
          </span>
          <button
            type="button"
            onClick={() => setCurrent(current.slice(0, -1))}
            disabled={!current.length}
            className="rounded px-2 py-1 text-sm text-slate-600 disabled:opacity-40 dark:text-slate-300"
          >
            Undo
          </button>
          <button
            type="button"
            data-testid="mm-submit"
            onClick={submit}
            disabled={current.length !== LEN}
            className="rounded-lg accent-highlight px-3 py-1 text-sm font-medium disabled:opacity-40"
          >
            Guess
          </button>
        </div>
      ) : (
        <div className="flex items-center gap-2 text-sm">
          <span className="text-slate-600 dark:text-slate-400">Code:</span>
          <span className="flex gap-1">
            {secret.map((k, i) => (
              <Dot key={i} k={k} />
            ))}
          </span>
        </div>
      )}
      <div className="flex flex-wrap gap-2 pt-2">
        {COLORS.map((c) => (
          <button
            key={c.key}
            type="button"
            data-testid={`mm-color-${c.key}`}
            onClick={() => pick(c.key)}
            aria-label={c.key}
            className={`h-8 w-8 rounded-full ${c.cls} transition hover:ring-2`}
          />
        ))}
      </div>
      <span className="text-xs text-slate-500">
        Guesses: {guesses.length} / {MAX}
      </span>
      <ConfirmModal {...confirmProps} />
    </div>
  )
}
