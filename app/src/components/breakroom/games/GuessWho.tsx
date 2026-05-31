import { useCallback, useMemo, useState } from 'react'
import { EDITIONS, type GwItem } from './guessWhoEditions'
import { bestQuestion, filterByAnswer, pickSecret, type AiQuestion } from './guessWhoLogic'
import { api } from '../../../lib/api'
import { useConfirm } from '../../../hooks/useConfirm'
import ConfirmModal from '../../ConfirmModal'

type Phase = 'pickSecret' | 'play'
type Turn = 'you' | 'ai'
interface LogLine {
  who: 'you' | 'ai'
  text: string
}
interface AnswerResp {
  answer: 'yes' | 'no' | 'invalid'
}

export default function GuessWho() {
  const { confirm, confirmProps } = useConfirm()
  // Single edition ships today (Food). When more exist, add a picker here.
  const edition = EDITIONS[0]
  const items = edition.items

  const [aiSecret, setAiSecret] = useState<GwItem>(() => pickSecret(items))
  const [userSecret, setUserSecret] = useState<GwItem | null>(null)
  const [phase, setPhase] = useState<Phase>('pickSecret')
  const [turn, setTurn] = useState<Turn>('you')
  const [aiCandidates, setAiCandidates] = useState<GwItem[]>(items)
  const [flipped, setFlipped] = useState<Set<string>>(new Set())
  const [guessMode, setGuessMode] = useState(false)
  const [aiQuestion, setAiQuestion] = useState<AiQuestion | null>(null)
  const [aiGuess, setAiGuess] = useState<GwItem | null>(null)
  const [log, setLog] = useState<LogLine[]>([])
  const [input, setInput] = useState('')
  const [pending, setPending] = useState(false)
  const [notice, setNotice] = useState('')

  const reset = useCallback(() => {
    setAiSecret(pickSecret(items))
    setUserSecret(null)
    setPhase('pickSecret')
    setTurn('you')
    setAiCandidates(items)
    setFlipped(new Set())
    setGuessMode(false)
    setAiQuestion(null)
    setAiGuess(null)
    setLog([])
    setInput('')
    setPending(false)
    setNotice('')
  }, [items])

  const endGame = useCallback(
    (youWon: boolean) => {
      void confirm({
        title: youWon ? 'You won!' : 'The computer won!',
        message: youWon
          ? `You guessed it — the computer's food was ${aiSecret.emoji} ${aiSecret.name}. Play again?`
          : `The computer guessed your ${userSecret?.emoji} ${userSecret?.name}. Play again?`,
        confirmLabel: 'Play again',
        cancelLabel: 'Quit',
      }).then((again) => {
        if (again) reset()
      })
    },
    [confirm, aiSecret, userSecret, reset],
  )

  // The computer plans its move: guess when it is down to one suspect,
  // otherwise ask the question that best splits its remaining suspects.
  const aiMove = useCallback(
    (cands: GwItem[]) => {
      if (cands.length <= 1) {
        setAiGuess(cands[0] ?? null)
        setAiQuestion(null)
        return
      }
      const q = bestQuestion(cands, edition)
      if (q) {
        setAiQuestion(q)
        setAiGuess(null)
      } else {
        setAiGuess(cands[0])
        setAiQuestion(null)
      }
    },
    [edition],
  )

  // --- Picking your secret ---
  function chooseSecret(it: GwItem) {
    setUserSecret(it)
    setPhase('play')
    setTurn('you')
    setLog([{ who: 'ai', text: `Great, you picked a food. I picked one too — now ask me yes/no questions!` }])
  }

  // --- Your turn: ask the computer a free-text yes/no question ---
  async function ask(e: React.FormEvent) {
    e.preventDefault()
    const q = input.trim()
    if (!q || pending || turn !== 'you') return
    setNotice('')
    setPending(true)
    try {
      const resp = await api.post<AnswerResp>('/api/guesswho/answer', {
        item_name: aiSecret.name,
        item_attributes: aiSecret.attrs,
        question: q,
      })
      if (resp.answer === 'invalid') {
        setNotice('Only yes/no questions are allowed. Try something like "Is it sweet?"')
        return
      }
      const yes = resp.answer === 'yes'
      setLog((l) => [...l, { who: 'you', text: `You: ${q}` }, { who: 'ai', text: `Computer: ${yes ? 'Yes' : 'No'}` }])
      setInput('')
      setTurn('ai')
      aiMove(aiCandidates)
    } catch {
      setNotice('Something went wrong reaching the computer. Try again.')
    } finally {
      setPending(false)
    }
  }

  // --- AI turn: you answer the computer's question about YOUR food ---
  function answerAi(yes: boolean) {
    if (!aiQuestion || !userSecret) return
    const next = filterByAnswer(aiCandidates, aiQuestion.attr, yes)
    setAiCandidates(next)
    setLog((l) => [
      ...l,
      { who: 'ai', text: `Computer: ${aiQuestion.text}` },
      { who: 'you', text: `You: ${yes ? 'Yes' : 'No'}` },
    ])
    setAiQuestion(null)
    setTurn('you')
  }

  // --- AI turn: you confirm the computer's final guess ---
  function respondToGuess(yes: boolean) {
    if (!aiGuess) return
    setLog((l) => [...l, { who: 'ai', text: `Computer: Is your food ${aiGuess.emoji} ${aiGuess.name}?` }, { who: 'you', text: `You: ${yes ? 'Yes' : 'No'}` }])
    if (yes) {
      endGame(false)
      return
    }
    const next = aiCandidates.filter((c) => c.id !== aiGuess.id)
    setAiCandidates(next)
    setAiGuess(null)
    if (next.length === 0) {
      endGame(true) // computer ran out of guesses
    } else {
      setTurn('you')
    }
  }

  // --- Board tile click: flip-down to eliminate, or make your final guess ---
  function tileClick(it: GwItem) {
    if (phase === 'pickSecret') {
      chooseSecret(it)
      return
    }
    if (guessMode) {
      setGuessMode(false)
      if (it.id === aiSecret.id) endGame(true)
      else endGame(false)
      return
    }
    setFlipped((prev) => {
      const next = new Set(prev)
      if (next.has(it.id)) next.delete(it.id)
      else next.add(it.id)
      return next
    })
  }

  const yourTurnActive = phase === 'play' && turn === 'you' && !guessMode
  const remaining = useMemo(() => aiCandidates.length, [aiCandidates])

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-col gap-1">
        <h2 className="text-2xl font-bold text-slate-900 dark:text-white">Guess Who</h2>
        <p className="text-xs text-slate-500 dark:text-slate-400">
          {phase === 'pickSecret'
            ? 'Pick your secret food. The computer will try to guess it while you guess the computer’s.'
            : 'Food edition'}
        </p>
      </div>

      {/* Status row */}
      {phase === 'play' && (
        <div className="flex flex-wrap items-center gap-4 text-sm">
          {userSecret && (
            <span className="text-slate-600 dark:text-slate-300">
              Your secret: <span className="text-lg">{userSecret.emoji}</span> {userSecret.name}
            </span>
          )}
          <span className="text-slate-500">Computer is still considering {remaining} food{remaining === 1 ? '' : 's'}</span>
        </div>
      )}

      {/* Board */}
      <div className="grid grid-cols-4 gap-2 sm:grid-cols-6">
        {items.map((it) => {
          const down = flipped.has(it.id)
          return (
            <button
              key={it.id}
              type="button"
              data-testid={`gw-tile-${it.id}`}
              onClick={() => tileClick(it)}
              className={[
                'flex flex-col items-center justify-center gap-1 rounded-xl border p-2 transition',
                guessMode
                  ? 'border-pink-400 ring-2 ring-pink-300 hover:bg-pink-50 dark:hover:bg-pink-950'
                  : 'border-slate-200 dark:border-slate-700',
                down
                  ? 'opacity-30 grayscale'
                  : 'bg-white hover:-translate-y-0.5 hover:shadow-md dark:bg-slate-900',
              ].join(' ')}
            >
              <span className="text-3xl leading-none">{it.emoji}</span>
              <span className="text-[11px] font-medium text-slate-700 dark:text-slate-300">{it.name}</span>
            </button>
          )
        })}
      </div>

      {/* Controls */}
      {phase === 'pickSecret' && (
        <p className="text-sm font-medium text-pink-600">Tap a food above to make it your secret.</p>
      )}

      {yourTurnActive && (
        <form onSubmit={ask} className="flex flex-col gap-2">
          <div className="flex gap-2">
            <input
              value={input}
              onChange={(e) => setInput(e.target.value)}
              placeholder='Ask a yes/no question, e.g. "Is it sweet?"'
              data-testid="gw-question"
              disabled={pending}
              className="flex-1 rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm text-slate-900 dark:border-slate-700 dark:bg-slate-900 dark:text-white"
            />
            <button
              type="submit"
              data-testid="gw-ask"
              disabled={pending || !input.trim()}
              className="rounded-lg bg-pink-500 px-4 py-2 text-sm font-medium text-white hover:bg-pink-600 disabled:opacity-40"
            >
              {pending ? '…' : 'Ask'}
            </button>
            <button
              type="button"
              data-testid="gw-guess-mode"
              onClick={() => setGuessMode(true)}
              className="rounded-lg border border-slate-300 px-4 py-2 text-sm font-medium text-slate-700 hover:bg-slate-100 dark:border-slate-700 dark:text-slate-200 dark:hover:bg-slate-800"
            >
              I'll guess!
            </button>
          </div>
          {notice && <p className="text-sm font-medium text-red-500" data-testid="gw-notice">{notice}</p>}
        </form>
      )}

      {guessMode && (
        <p className="flex items-center gap-3 text-sm font-medium text-pink-600">
          Tap the food you think the computer picked.
          <button
            type="button"
            onClick={() => setGuessMode(false)}
            className="rounded-lg border border-slate-300 px-3 py-1 text-xs text-slate-600 hover:bg-slate-100 dark:border-slate-700 dark:text-slate-300 dark:hover:bg-slate-800"
          >
            Cancel
          </button>
        </p>
      )}

      {phase === 'play' && turn === 'ai' && aiQuestion && (
        <div className="flex flex-wrap items-center gap-3" data-testid="gw-ai-question">
          <span className="text-sm font-medium text-slate-800 dark:text-slate-100">Computer asks: {aiQuestion.text}</span>
          <button type="button" onClick={() => answerAi(true)} className="rounded-lg bg-green-500 px-4 py-1.5 text-sm font-medium text-white hover:bg-green-600">Yes</button>
          <button type="button" onClick={() => answerAi(false)} className="rounded-lg bg-slate-500 px-4 py-1.5 text-sm font-medium text-white hover:bg-slate-600">No</button>
        </div>
      )}

      {phase === 'play' && turn === 'ai' && aiGuess && (
        <div className="flex flex-wrap items-center gap-3" data-testid="gw-ai-guess">
          <span className="text-sm font-medium text-slate-800 dark:text-slate-100">
            Computer guesses: Is your food {aiGuess.emoji} {aiGuess.name}?
          </span>
          <button type="button" onClick={() => respondToGuess(true)} className="rounded-lg bg-green-500 px-4 py-1.5 text-sm font-medium text-white hover:bg-green-600">Yes</button>
          <button type="button" onClick={() => respondToGuess(false)} className="rounded-lg bg-slate-500 px-4 py-1.5 text-sm font-medium text-white hover:bg-slate-600">No</button>
        </div>
      )}

      {/* History */}
      {log.length > 0 && (
        <div className="max-h-32 overflow-y-auto rounded-lg bg-slate-50 p-3 text-xs text-slate-600 dark:bg-slate-800/50 dark:text-slate-300">
          {log.map((l, i) => (
            <div key={i}>{l.text}</div>
          ))}
        </div>
      )}

      <ConfirmModal {...confirmProps} />
    </div>
  )
}
