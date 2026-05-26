interface Props {
  diff: string
}

export default function InlineDiff({ diff }: Props) {
  const lines = diff.split('\n')
  return (
    <div className="overflow-x-auto bg-white dark:bg-slate-900 font-mono text-xs leading-5" data-testid="inline-diff">
      {lines.map((line, i) => {
        let cls = 'px-3 whitespace-pre text-slate-600 dark:text-slate-400'
        if (line.startsWith('+') && !line.startsWith('+++')) {
          cls = 'px-3 whitespace-pre bg-green-950/60 text-green-700 dark:text-green-300'
        } else if (line.startsWith('-') && !line.startsWith('---')) {
          cls = 'px-3 whitespace-pre bg-red-950/60 text-red-700 dark:text-red-300'
        } else if (line.startsWith('@@')) {
          cls = 'px-3 whitespace-pre text-blue-600 dark:text-blue-400'
        }
        return (
          <div key={i} className={cls}>{line || ' '}</div>
        )
      })}
    </div>
  )
}
