export interface ReleaseEntry {
  title: string
  description: string
}

export interface ReleaseGroup {
  date: string
  label: string
  entries: ReleaseEntry[]
}

const releaseNotes: ReleaseGroup[] = [
  {
    date: '2026-04-06',
    label: 'April 6, 2026',
    entries: [
      {
        title: 'Labels system',
        description: 'Organize your tasks with colored tags so you can group and filter them however you like.',
      },
      {
        title: 'Task dependencies',
        description: 'See which tasks block other tasks. If something needs to be done first, you will know.',
      },
      {
        title: 'Smart focus',
        description: 'Shows you which task to work on next based on priority and what is blocking what.',
      },
      {
        title: 'Concept search',
        description: 'Press Cmd+K to search across all your tasks and ideas in one place.',
      },
      {
        title: 'Activity feed',
        description: 'See everything that happened in your system, all in one timeline.',
      },
      {
        title: 'Task briefings',
        description: 'Click any task to get the full context: what it is, why it matters, and what has been done.',
      },
      {
        title: 'Document planning',
        description: 'Create plans and break them into tasks automatically.',
      },
      {
        title: 'Session diff',
        description: 'See exactly what changed during each work session.',
      },
      {
        title: 'Task health check',
        description: 'Find duplicate or incomplete tasks so nothing falls through the cracks.',
      },
      {
        title: 'Agent permissions',
        description: 'Approve or deny requests from agents before they take action.',
      },
      {
        title: 'Delegation view',
        description: 'See which tasks are good candidates to hand off to someone (or something) else.',
      },
      {
        title: 'System health panel',
        description: 'A quick look at how your system is running and whether anything needs attention.',
      },
      {
        title: 'Secret management',
        description: 'Your API keys and passwords are stored securely, not in plain text.',
      },
      {
        title: 'Attributed commits',
        description: 'Link code changes back to the task that prompted them, so you always know why something changed.',
      },
      {
        title: 'Thread and group organization',
        description: 'Keep related conversations and items organized together.',
      },
      {
        title: 'MCP server config from ostk',
        description: 'Configure tool servers directly from the ostk command line.',
      },
    ],
  },
]

export default releaseNotes
