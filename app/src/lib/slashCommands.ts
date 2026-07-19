/**
 * Slash command manifest for mychat.
 *
 * Each command implements SlashCommand. To add a new command, add one entry
 * to the array returned by createSlashCommands(). Nothing else needs to change.
 *
 * Triggers receive a CommandContext with callbacks from ChatPanel so they can
 * mutate chat state without importing the panel directly.
 */

export interface SlashCommand {
  id: string             // "/handoff"
  label: string          // "Handoff"
  description: string    // plain language, no jargon
  category: 'workflow' | 'mode' | 'data' | 'system'
  trigger: () => Promise<void> | void
  keywords?: string[]
}

/** Callbacks provided by ChatPanel when instantiating the command manifest. */
export interface CommandContext {
  /** Append a system message bubble to the chat. */
  addSystemMessage: (text: string) => void
  /** Clear all messages from the current tab. */
  clearChat: () => void
  /** Open the quick-add needle (task) modal. */
  openNeedleModal: () => void
  /** Open the spec wizard modal. */
  openSpecModal: () => void
  /** Open the gem picker (navigate to /gems). */
  openGemPicker: () => void
  /** Open the giphy picker. */
  openGiphy: () => void
  /** Toggle plan mode flag (→1395 owns the UI). */
  togglePlanMode: () => void
  /** Call POST /api/skills/run for skill-based commands. */
  runSkill: (skillId: string) => Promise<void>
}

/**
 * Instantiates the 12-command manifest with live trigger callbacks.
 * Call this inside a useMemo in ChatPanel.
 */
export function createSlashCommands(ctx: CommandContext): SlashCommand[] {
  return [
    {
      id: '/handoff',
      label: 'Handoff',
      description: 'Write a handoff doc capturing current work state',
      category: 'workflow',
      keywords: ['hand', 'off', 'docs', 'state'],
      trigger: () => ctx.runSkill('handoff'),
    },
    {
      id: '/plan',
      label: 'Plan',
      description: 'Switch to plan mode for thinking through a big request',
      category: 'mode',
      keywords: ['planning', 'think', 'architect'],
      trigger: () => ctx.togglePlanMode(),
    },
    {
      id: '/review',
      label: 'Review',
      description: 'Run a code review on the current branch',
      category: 'workflow',
      keywords: ['code', 'pr', 'diff', 'branch'],
      trigger: () => ctx.runSkill('review'),
    },
    {
      id: '/init',
      label: 'Init',
      description: 'Create a CLAUDE.md file for the current project',
      category: 'workflow',
      keywords: ['setup', 'claude.md', 'initialize', 'project'],
      trigger: () => ctx.runSkill('init'),
    },
    {
      id: '/build',
      label: 'Build',
      description: 'Start an agent that builds the thing you describe',
      category: 'workflow',
      keywords: ['make', 'create', 'feature', 'agent'],
      trigger: () => ctx.runSkill('build'),
    },
    {
      id: '/diagnose',
      label: 'Diagnose',
      description: 'Find the root cause of a problem, fix it, and add tests',
      category: 'workflow',
      keywords: ['debug', 'fix', 'bug', 'problem'],
      trigger: () => ctx.runSkill('diagnose'),
    },
    {
      id: '/help',
      label: 'Help',
      description: 'List all available slash commands',
      category: 'system',
      keywords: ['commands', 'list', 'show'],
      trigger: () => {
        const lines = createSlashCommands(ctx).map(
          (c) => `**${c.id}** — ${c.description}`,
        )
        ctx.addSystemMessage(`**Slash commands**\n\n${lines.join('\n')}`)
      },
    },
    {
      id: '/clear',
      label: 'Clear',
      description: 'Clear the current chat',
      category: 'system',
      keywords: ['reset', 'wipe', 'start over'],
      trigger: () => ctx.clearChat(),
    },
    {
      id: '/spec',
      label: 'Spec',
      description: 'Draft a new spec for a feature or idea',
      category: 'data',
      keywords: ['document', 'design', 'draft', 'requirements'],
      trigger: () => ctx.openSpecModal(),
    },
    {
      id: '/task',
      label: 'Task',
      description: 'Add a task to the backlog',
      category: 'data',
      keywords: ['task', 'todo', 'issue', 'work'],
      trigger: () => ctx.openNeedleModal(),
    },
    {
      id: '/gem',
      label: 'Gem',
      description: 'Switch to a different gem or AI persona',
      category: 'mode',
      keywords: ['persona', 'assistant', 'switch', 'gems'],
      trigger: () => ctx.openGemPicker(),
    },
    {
      id: '/giphy',
      label: 'Giphy',
      description: 'Search and send an animated GIF',
      category: 'system',
      keywords: ['gif', 'search', 'image', 'animate'],
      trigger: () => ctx.openGiphy(),
    },
  ]
}

/**
 * Filter the command list by a query string.
 * Matches against id and keywords. Case-insensitive, prefix match.
 */
export function filterCommands(
  commands: SlashCommand[],
  query: string,
): SlashCommand[] {
  if (!query) return commands
  const q = query.toLowerCase()
  return commands.filter((c) => {
    const inId = c.id.slice(1).toLowerCase().startsWith(q)
    const inLabel = c.label.toLowerCase().startsWith(q)
    const inKeywords = c.keywords?.some((k) => k.toLowerCase().startsWith(q))
    return inId || inLabel || inKeywords
  })
}
