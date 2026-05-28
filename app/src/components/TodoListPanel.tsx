export interface Todo {
  subject: string
  status: 'pending' | 'in_progress' | 'completed'
  activeForm?: string
}

interface Props {
  todos: Todo[]
}

export function TodoListPanel({ todos }: Props) {
  if (!todos.length) return null
  return (
    <div
      data-testid="todo-list-panel"
      className="inline-flex flex-col gap-1.5 px-4 py-3 rounded-xl border border-slate-200 dark:border-slate-700/50 bg-slate-50/40 dark:bg-slate-800/40 max-w-[85%]"
    >
      <span className="text-xs text-slate-600 dark:text-slate-400 font-medium">This turn</span>
      {todos.map((todo, i) => {
        const icon =
          todo.status === 'completed' ? '✓' :
          todo.status === 'in_progress' ? '◔' : '▸'
        const label =
          todo.status === 'in_progress' && todo.activeForm ? todo.activeForm : todo.subject
        return (
          <div
            key={i}
            data-testid={`todo-item-${todo.status}`}
            className="flex items-start gap-2 text-xs"
          >
            <span className={`mt-0.5 shrink-0 ${
              todo.status === 'completed' ? 'text-green-500' :
              todo.status === 'in_progress' ? 'text-amber-600 dark:text-amber-400' : 'text-slate-500'
            }`}>
              {icon}
            </span>
            <span className={
              todo.status === 'completed' ? 'text-slate-500 line-through' :
              todo.status === 'in_progress' ? 'text-slate-800 dark:text-slate-200' : 'text-slate-600 dark:text-slate-400'
            }>
              {label}
            </span>
          </div>
        )
      })}
    </div>
  )
}
