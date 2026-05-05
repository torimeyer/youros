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
  // v3.11.1
  {
    date: '2026-05-04',
    label: 'May 4, 2026',
    entries: [
      {
        title: 'Gemini answers as Gemini',
        description: 'Stronger system prompt for the Gemini chat path. Gemini now opens with "I am Gemini, Google\'s AI model" when asked which AI it is, and is forbidden from describing itself as local or embedded.',
      },
      {
        title: 'Cleaner public install',
        description: 'Removed internal-only documents from the install. Strategy notes, internal diagnoses, and agent backup files no longer ship to colleagues.',
      },
    ],
  },
  // v3.11.0
  {
    date: '2026-05-04',
    label: 'May 4, 2026',
    entries: [
      {
        title: '5 new agent templates',
        description: 'Campaign Brief, Budget Builder, Investor Update, Customer Reply, and Design Critique are now built in. Pick one from the template picker and it drops in ready to run.',
      },
      {
        title: 'Sharper built-in templates',
        description: 'Six existing templates (across research, build, and analysis categories) got rewritten descriptions and tighter prompts so they do a better job explaining what they are and produce more useful results.',
      },
      {
        title: 'New Spec skips straight to the wizard',
        description: 'You no longer name a spec before opening it. Click New Spec and the step-by-step wizard opens immediately.',
      },
      {
        title: 'Gemini has its own identity',
        description: 'When you switch to Gemini, it introduces itself as Gemini rather than taking on the myOS persona. Each AI now speaks as itself.',
      },
      {
        title: 'Agent template aliases',
        description: 'Agent files now support an ALIASES directive, so a template can be found under any of its alternate names in the picker and command bar.',
      },
    ],
  },
  {
    date: '2026-05-02',
    label: 'May 2, 2026',
    entries: [
      {
        title: 'ADHD mode sticks across devices',
        description: 'Turning ADHD mode on in one browser used to reset when you opened myOS on another device. The setting now lives on the server, so it follows you everywhere.',
      },
      {
        title: 'Settings sections stay in the right place',
        description: 'Data Management and Shared Links were showing up on every Settings tab, not just Privacy & Data. They now appear only where they belong.',
      },
    ],
  },
  {
    date: '2026-05-01',
    label: 'May 1, 2026',
    entries: [
      {
        title: 'ADHD Mode',
        description: 'Turn it on in Settings and myOS checks in when you\'ve been away, welcomes you back with a summary of where things stand, and offers a focus mode that dims distractions while you\'re heads-down.',
      },
      {
        title: 'SDD Wizard',
        description: 'A step-by-step wizard that drafts a Software Design Doc for any feature. Answer a few questions and myOS fills in context it already knows (open tasks, linked files, recent agents) and saves the spec to your library.',
      },
      {
        title: 'Agents Insights Tab',
        description: 'Each agent now has an Insights tab showing what it did, which files it touched, and how long it ran. No more digging through transcripts.',
      },
      {
        title: 'Editable Agent Template Aliases',
        description: 'Rename any agent template to something that fits your workflow. The new name shows in the picker, the Agents list, and the command bar.',
      },
      {
        title: 'Settings Nav Cleanup',
        description: 'Data Management and Shared Links now live only under Privacy & Data, so the Settings sidebar is shorter and easier to scan.',
      },
    ],
  },
  {
    date: '2026-04-30',
    label: 'April 30, 2026',
    entries: [
      {
        title: 'Jira and Confluence',
        description: 'Connect your Atlassian account. Jira tickets show up in your Inbox, you can comment and change status without leaving the app, and a background poller keeps everything in sync.',
      },
      {
        title: 'Slack setup in the app',
        description: 'Configure your Slack connection from a form in the app instead of editing a config file. The Connect button stays disabled until the setup is valid.',
      },
      {
        title: 'Settings tabs',
        description: 'Settings sections are now tabs instead of one long scrolling page. Click a section and only that section shows.',
      },
      {
        title: 'Cleaner agent templates',
        description: 'Agent template details no longer show the description and prompt twice. Less clutter, same information.',
      },
      {
        title: 'Lighter sidebar',
        description: 'Utility links (What\'s New, Tour, Usage, Settings) sit below a separator with smaller text so they stay out of the way.',
      },
    ],
  },
  {
    date: '2026-04-29',
    label: 'April 29, 2026',
    entries: [
      {
        title: 'Flag it, reply from Inbox',
        description: 'Flag a Slack message for follow-up. It lands in your Inbox. Open a reply right there, let Gemini Enterprise draft it, edit if you want, and send. The loop is one page, not three.',
      },
      {
        title: 'Unified Inbox',
        description: 'Everything that needs your attention in one place: flagged Slack messages and tasks that came in from outside. Dismiss what you\'ve handled, convert anything to a task, or reply without leaving.',
      },
      {
        title: 'Gemini Enterprise replies',
        description: 'Your organization\'s Gemini writes Slack replies in your voice. One click to draft, yours to edit, one more to send.',
      },
    ],
  },
  {
    date: '2026-04-28',
    label: 'April 28, 2026',
    entries: [
      {
        title: 'Files, finally',
        description: 'Your workspace now has a Files tab. See everything agents have created or changed, when it happened, and which agent touched it. Open, preview, and share any file without leaving the app.',
      },
      {
        title: 'Setup that knows you',
        description: 'The setup wizard now asks what you actually use myOS for and builds your starter pack around that. Pick your focus and your first agents, specs, and task suggestions are already calibrated to fit.',
      },
      {
        title: 'Brainstorm',
        description: 'Describe a decision you\'re stuck on. Get back 5 to 8 concrete options, each with tradeoffs and effort, plus the one or two the agent would actually recommend. Faster than a whiteboard, sharper than a gut check.',
      },
      {
        title: 'Tasks that read the room',
        description: 'Write "blocking" or "by Friday" in a task and myOS suggests the right priority for you. Tasks that sit untouched too long quietly drop a level so your urgent list doesn\'t turn into a graveyard.',
      },
      {
        title: 'Your AI setup, explained',
        description: 'A new page in Settings shows your full AI configuration in plain English: which model, which provider, and how the pieces fit. Share the link with a teammate so they can match your setup exactly.',
      },
      {
        title: 'Agents read the brief',
        description: 'When you launch a Build-it, the agent gets your spec\'s acceptance criteria automatically. No pasting requirements. It knows what done looks like before it starts.',
      },
      {
        title: 'Specs is now Plans',
        description: 'Same tab, cleaner name. Everything you had is still there.',
      },
      {
        title: 'Agents that don\'t stop',
        description: 'Finish one job, pick up the next. When the queue is empty, stop. No more nudging.',
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
        description: 'Create specs and break them into tasks automatically.',
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
