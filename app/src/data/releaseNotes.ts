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
    date: '2026-04-22',
    label: 'April 22, 2026 (security patch)',
    entries: [
      {
        title: 'Rotated a leaked Stitch API key',
        description: 'A Google Cloud API key for the Stitch MCP server was accidentally tracked in the repo and flagged by Google as exposed. The old key is dead (Google auto-deleted it), the file that held it is now gitignored, and a template ships in its place. Paste your own key into .mcp.json if you use Stitch.',
      },
      {
        title: 'Pre-commit secret scan',
        description: 'A new pre-commit hook blocks any commit that contains an obvious API key pattern so this class of leak cannot happen again. Install with scripts/install-git-hooks.sh.',
      },
    ],
  },
  {
    date: '2026-04-22',
    label: 'April 22, 2026',
    entries: [
      {
        title: 'Real Build-it',
        description: 'When you click Build-it on a spec, real AI agents now go off and do the actual work. They write code, edit files, and close their tasks when the work lands. Expect it to take a few minutes instead of a few seconds. You will get a notification the moment the feature is ready to try.',
      },
      {
        title: 'Reliable calendar',
        description: 'Events you create from chat now show up on the Calendar tab right away, no reload needed. Deleting events works whether you made them in chat or directly in Google Calendar, with a 5 second undo window that will not bounce back if something else syncs in the background.',
      },
      {
        title: 'Smoother chat',
        description: 'Chat responses no longer flicker or rewind while typing. The switch from plain text to styled formatting happens with a quick fade so it feels clean. Long responses stream evenly with no jitter.',
      },
      {
        title: 'A better tour',
        description: 'The first-time tour now has 7 steps including the Specs and Build-it flow. Tour tooltips stay inside the screen on every step, and the Chat step no longer names just one AI.',
      },
      {
        title: 'Cleaner briefing',
        description: 'Your daily briefing no longer lists test tasks, scratch notes, or accidental long paste-ins as closed work. Only real tasks make it into the summary.',
      },
      {
        title: 'Accurate session count',
        description: 'The sidebar badge that shows how many sessions are active now counts only real sessions. Old background reload rows no longer pad the number.',
      },
      {
        title: 'Smarter chat tools',
        description: 'When Claude uses tools in chat, it now prefers ostk commands over raw shell tools. Cleaner, faster, and it matches the rest of myOS.',
      },
      {
        title: 'Feature completion notification',
        description: 'When a Build-it finishes, a release notes modal pops up so features never arrive silently. You always know when something new is ready to try.',
      },
    ],
  },
  {
    date: '2026-04-13',
    label: 'April 13, 2026',
    entries: [
      {
        title: 'Enterprise mode',
        description: 'Set up an org, invite teammates with magic links or SSO, enforce budget limits and agent permissions, and monitor team usage from the admin dashboard. Everything an IT admin needs to roll out myOS to a team.',
      },
      {
        title: 'Smarter chat',
        description: 'Chat now supports slash commands (/tasks, /agents, /status, /help), creates calendar events, sends emails, and uploads files to Drive. The AI thinks more deeply on complex questions, and follow-up messages are faster because it remembers the conversation.',
      },
      {
        title: 'Cost tracking',
        description: 'New page in the sidebar showing all AI spending: agent budgets, usage by AI model, spending over time, and how much your system saved you by reusing previous work.',
      },
      {
        title: 'Quality of life',
        description: 'Task titles auto-fix grammar and capitalize brand names. Activity and session history are combined into one page. Naming is consistent across the app (no more "Needles" or "Hay").',
      },
    ],
  },
  {
    date: '2026-04-12',
    label: 'April 12, 2026',
    entries: [
      {
        title: 'Chat memory',
        description: 'The AI now remembers what you talked about in your last conversation. No more repeating yourself. Turn it off in Settings if you prefer a blank slate each time.',
      },
      {
        title: 'Create tasks from emails',
        description: 'See a "Create task" button on every email in the Gmail view. One click and the AI pulls out a title and description for you.',
      },
      {
        title: 'Create tasks from meetings',
        description: 'After a meeting prep briefing, click "Create tasks from this briefing" and the AI extracts action items and turns each one into a task.',
      },
      {
        title: 'Mobile layout',
        description: 'Every page now works on your phone and tablet. The chat panel goes full screen on mobile, buttons are big enough to tap, and nothing scrolls sideways.',
      },
      {
        title: 'Push notifications',
        description: 'Get native alerts when agents finish, tasks are due, or emails arrive, even when your browser tab is closed. Enable it from the notification bell.',
      },
      {
        title: 'Offline mode',
        description: 'Lost your internet? Tasks and the dashboard still work from cache. Any changes you make queue up and sync when you are back online.',
      },
      {
        title: 'Slack integration',
        description: 'Connect your Slack workspace. Browse channels, read and send messages, and create tasks from any Slack message.',
      },
      {
        title: 'GitHub issues sync',
        description: 'Connect a GitHub repo with a personal access token. Import issues as tasks, or push tasks back to GitHub as new issues.',
      },
      {
        title: 'Linear and Jira import',
        description: 'Migrating from another tool? Import your Linear or Jira issues into myOS with one click. Priorities, statuses, and labels all map over.',
      },
      {
        title: 'Smart briefing with actions',
        description: 'Your daily briefing now includes clickable action items: close a stale task, reply to an email, prep for a meeting. One click to act.',
      },
      {
        title: 'Agent pattern learning',
        description: 'myOS tracks which agent setups work well and which keep failing. Proven templates get a green badge. Failing ones get specific suggestions to fix them.',
      },
      {
        title: 'Related context on tasks',
        description: 'Open any task and check the Related tab to see connected emails, calendar events, and Drive files. All found automatically by matching keywords.',
      },
      {
        title: 'Enterprise setup wizard',
        description: 'Setting up enterprise mode now walks you through it step by step: name your org, invite your team, set spending guardrails, done.',
      },
      {
        title: 'Chat model toggle',
        description: 'Switch between Claude and Gemini right from the chat input. No more digging into Settings to change your default AI.',
      },
      {
        title: 'Recurring tasks on the Tasks page',
        description: 'Recurring tasks moved out of Settings and into the Tasks page where they belong. New "Recurring" tab with one-click preset suggestions to get started.',
      },
      {
        title: 'Settings layout cleanup',
        description: 'Every settings section is now half-page in a two-column grid. Cleaner, easier to scan.',
      },
      {
        title: 'Pause and resume tasks',
        description: 'Put a task on hold without closing it. Hit Pause in the action menu, and it moves to the Paused tab. Resume it whenever you are ready.',
      },
      {
        title: 'Decision log',
        description: 'Track why decisions were made. Log them from the Activity page and they show up in search results and audit trails.',
      },
      {
        title: 'Faster daily briefing',
        description: 'Your briefing now pulls activity data directly from the system instead of asking the AI to reconstruct it. Loads faster and more accurate.',
      },
      {
        title: 'Deep search',
        description: 'Cmd+K now searches everything: tasks, ideas, decisions, threads, audit events, and past session transcripts. Results are grouped by category.',
      },
      {
        title: 'Agent corrections',
        description: 'Send a course correction to a running agent with the amber Correct button. Visually distinct from regular follow-ups so the agent knows to change direction.',
      },
      {
        title: 'Priority change tracking',
        description: 'When you change a task priority, you can note why. The reason is saved in the audit trail.',
      },
      {
        title: 'Smarter idea breakdown',
        description: 'Breaking an idea into tasks is smarter now. It groups related pieces together before asking the AI, which is faster and costs less.',
      },
      {
        title: 'Context pressure alerts',
        description: 'Running agents show a warning when they are running low on memory. Yellow at 70% full, red at 90%.',
      },
      {
        title: 'Coordination locks',
        description: 'See which agents are working on the same thing. If one gets stuck, you can unstick it from the Agents page.',
      },
    ],
  },
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
        title: 'Global search',
        description: 'Press Cmd+K to search across all your tasks and ideas by keyword.',
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
        title: 'What changed',
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
        title: 'Tool setup from the command line',
        description: 'Set up tool integrations directly from the command line.',
      },
    ],
  },
]

export default releaseNotes
