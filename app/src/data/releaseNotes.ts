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
  // v5.13.0
  {
    date: '2026-07-09',
    label: 'July 9, 2026',
    entries: [
      {
        title: 'Portfolio shows your real work and lets you set up themes',
        description: 'The Portfolio page now pulls the same task list you see on the Tasks page instead of internal checklist noise, and a new card lets you name the goals your work supports, like customer trust, so tasks and projects can roll up under them. Status labels are plain words now: past due, waiting on other work, no activity for a week.',
      },
      {
        title: 'Finished specs move into a history view',
        description: 'The Specs page shows only work still in play. Everything marked done lives behind a Show history button, so the list stays focused without losing anything.',
      },
      {
        title: 'The specs badge counts what is actually open',
        description: 'The sidebar badge next to Specs now counts every spec that is not done, matching what the page shows, instead of skipping drafts.',
      },
      {
        title: 'AI suggest works now, and asks you to retry when it cannot',
        description: 'The AI suggest button in the clarity panel used to fail every single time with a cryptic error, because the app stopped waiting after 5 seconds while a normal answer takes about 6. It now waits as long as it needs, and if the backend really is busy it says so in plain words with a Retry button.',
      },
      {
        title: 'A crashed helper can no longer mark its work as finished',
        description: 'If a background helper dies mid-job, its task now stays open instead of being quietly marked complete. Only real saved work closes a task.',
      },
      {
        title: 'Both test suites fully green',
        description: 'All 7,089 backend checks and 3,237 frontend checks pass, including a fix for a hidden hang that wasted a full minute of every backend test run.',
      },
    ],
  },
  // v5.12.0
  {
    date: '2026-07-08',
    label: 'July 8, 2026',
    entries: [
      {
        title: 'Text yourOS: text a job in, get a text back',
        description: 'You can now text yourOS from your phone. Ask it to do something, reply YES to confirm, and it starts the work. When the job finishes, you get one text back with the result. Turn it on from the new Text yourOS card in Settings, where you also control which phone numbers and emails it trusts.',
      },
      {
        title: 'See what all your helpers are doing at a glance',
        description: 'The Agents and Sessions pages now show what each working session is up to: a short label, its current activity, the files it touched recently, and a warning when one looks stuck. If two helpers are about to step on each other, a conflict alert appears before it becomes a problem. A daily digest panel sums up what happened across all of them.',
      },
      {
        title: 'The "your feature is live" popup now speaks human',
        description: 'When a build finishes, the celebration popup used to list the engineering checklist word for word. Now it shows a few short bullets about what you can actually do with the new feature, written in plain language.',
      },
      {
        title: 'Select all your tasks at once',
        description: 'The task list has a select-all button, so bulk actions like building several things in one go no longer require clicking each task one by one.',
      },
      {
        title: 'Faster chat replies',
        description: 'Each chat tab now keeps a warmed-up worker ready between turns, so replies start noticeably sooner instead of paying a cold start on every message.',
      },
      {
        title: 'Clean up your inbox in one move',
        description: 'In the Gmail view you can select a batch of emails and mark them all as read at once.',
      },
      {
        title: 'Finished work is easier to find',
        description: 'Cards for finished helpers now link straight to the documents and files they produced, and a small hint appears when yourOS notices a pattern in how you work that it can help with.',
      },
      {
        title: 'No more surprise permission popup storms',
        description: 'Helpers are now blocked from scanning private home folders they do not need, which stops the bursts of macOS permission popups some jobs used to trigger.',
      },
    ],
  },
  // v5.11.0
  {
    date: '2026-06-18',
    label: 'June 18, 2026',
    entries: [
      {
        title: 'Connecting your AI is simpler',
        description: 'Setup now leads with the Claude or Gemini plan you already have, so if you already pay for one it just works with no key to paste. The pay-as-you-go key option is still there, marked as advanced for people who want it.',
      },
      {
        title: 'Clear that yourOS will not touch your existing AI',
        description: 'The connect step now says plainly that yourOS runs on your computer and uses your existing AI logins. It does not change, replace, or sign into anything you already use.',
      },
      {
        title: 'A calmer first screen',
        description: 'When you first open yourOS, the home view and side menu start simple, with extra sections tucked away until you want them, so there is less on screen at once.',
      },
      {
        title: 'Easier free path with Google Gemini',
        description: 'If you just want to talk to an AI for free, setup now points clearly to a free Google AI Studio key for personal chat.',
      },
    ],
  },
  // v5.10.0
  {
    date: '2026-06-17',
    label: 'June 17, 2026',
    entries: [
      {
        title: 'Easier first-time install',
        description: 'The setup guide now leads with the install command everyone can use and marks the sign-in-key method as a contributors-only option, so newcomers no longer hit a confusing permission error.',
      },
      {
        title: 'Text yourOS from your own phone notes',
        description: 'Messages you send to yourself now reach yourOS, with a safeguard that prevents back-and-forth reply loops.',
      },
      {
        title: 'Your trusted contacts stay put',
        description: 'Changing your settings no longer erases your saved trusted contacts.',
      },
    ],
  },
  // v5.7.0
  {
    date: '2026-06-06',
    label: 'June 6, 2026',
    entries: [
      {
        title: 'Enhanced Light Mode Clarity',
        description: 'A comprehensive visual overhaul for Light Mode. Every page, from your Messages to your Calendar, has been updated with high-contrast text and refined inputs for maximum legibility in bright environments.',
      },
      {
        title: 'Unified Messaging',
        description: 'The messaging module has been renamed to Messages to better reflect its function as your central communication hub for iMessage and beyond.',
      },
    ],
  },
  // v5.5.1
  {
    date: '2026-06-04',
    label: 'June 4, 2026',
    entries: [
      {
        title: 'New plans stay drafts until you say so',
        description: 'When you start a new plan, yourOS fills in the suggested details for you but no longer moves it forward on its own. The plan stays a draft until you choose to promote it, so you decide when it becomes real.',
      },
      {
        title: 'Compare mode answers what you actually asked',
        description: 'When you compare answers from more than one AI at once, each one now replies helpfully to your question instead of saying a tool is missing.',
      },
      {
        title: 'Steadier when lots is running at once',
        description: 'yourOS no longer slows to a stop when many background helpers are working at the same time. The running-helpers list is lighter and stale sessions are cleaned up, so the app stays responsive.',
      },
    ],
  },
  // v5.5.0
  {
    date: '2026-06-03',
    label: 'June 3, 2026',
    entries: [
      {
        title: 'Your private content stays on your computer',
        description: 'yourOS now blocks your chats, notes, specs, and settings from ever being saved into the shared code, and writes your working files to your own machine instead.',
      },
      {
        title: 'Your task list is complete again',
        description: 'Open tasks that had been moved into the archive during a daily cleanup now show up on your board again instead of quietly disappearing. If your list looks longer, those tasks were always open.',
      },
      {
        title: 'Ghost sessions no longer show as running',
        description: 'A stuck or stale agent session no longer appears as Running in Active Sessions. Live agents are kept, only confirmed dead ones are hidden.',
      },
      {
        title: 'Connect Confluence on its own site',
        description: 'If your Confluence lives on a different site than Jira, you can now give it its own address when you connect Atlassian.',
      },
      {
        title: 'Clearer message when the AI model is unreachable',
        description: 'Breaking a spec into tasks now fails fast with a plain message if the AI model cannot be reached, instead of hanging.',
      },
      {
        title: 'Tidier task rows',
        description: 'Removed the dead drag handle. The task number is now the drag handle, and the status circle reads Mark done or Reopen.',
      },
      {
        title: 'Specs can track the files a requirement covers',
        description: 'A requirement can list the files it covers, and the drift check now warns when one of those files is missing.',
      },
    ],
  },
  // v5.4.0
  {
    date: '2026-06-03',
    label: 'June 3, 2026',
    entries: [
      {
        title: 'Onboarding finds your providers reliably',
        description: 'The setup screen no longer shows a "could not check for a connected provider" error. It warms up at startup and answers instantly.',
      },
      {
        title: 'Clearer Google setup',
        description: 'Google Cloud (Vertex AI) and the personal AI Studio key are now two separate cards with shorter steps. If you have signed in to Google Cloud but not picked a project, it tells you exactly what to run.',
      },
      {
        title: 'Atlassian shown correctly',
        description: 'The connection is now labeled Atlassian and shows both Jira and Confluence, instead of only Jira.',
      },
      {
        title: 'Agent teams',
        description: 'Agents can work together as a team with a shared task list and roles, with a team view on the Agents page.',
      },
      {
        title: 'Spec status reflects finished work',
        description: 'A spec whose work is done no longer shows In Progress just because some old tasks were never closed.',
      },
    ],
  },
  // v5.3.0
  {
    date: '2026-06-03',
    label: 'June 3, 2026',
    entries: [
      {
        title: 'Pick your model when you start an agent',
        description: 'The spawn box now lets you choose Claude or Gemini, and starts on Claude. Before, it could only start Gemini.',
      },
      {
        title: 'More reliable backend',
        description: 'Fixed a slowdown that could freeze the backend and force a restart every 30 seconds when the agent list refreshed. The list stays fast now.',
      },
      {
        title: 'Honest agent chat',
        description: 'If you message an agent that has already finished or gone quiet, it now tells you so plainly instead of pretending it is ready and waiting.',
      },
      {
        title: 'Specs stay accurate',
        description: 'Deleting a spec now actually removes it for good, In Progress has its own color so it no longer looks like Needs detail, and the drift panel lets you update a spec to match the code or keep it as is.',
      },
    ],
  },
  // v5.2.0
  {
    date: '2026-06-03',
    label: 'June 3, 2026',
    entries: [
      {
        title: 'Executive Summary',
        description: 'A new Executive Summary page shows how your work is rolling up and whether each piece is on track, and drafts weekly updates you can approve with one click. If no tracker is connected, it shows a simple setup prompt and changes nothing.',
      },
      {
        title: 'Search across your sources',
        description: 'A new search finds matches across all your connected sources and shows where each result came from.',
      },
      {
        title: 'Cleaner, simpler surfaces',
        description: 'The top bar lost its large page title for a cleaner look, the task waves controls are now a single Update waves button, and a plan that has not started yet reads Ready instead of In Progress.',
      },
    ],
  },
  // v4.0.0
  {
    date: '2026-05-28',
    label: 'May 28, 2026',
    entries: [
      {
        title: 'yourOS',
        description: 'The app is now yourOS. The name puts you at the center. Internal plumbing stays the same; every surface you see says yourOS.',
      },
    ],
  },
  // v3.16.0
  {
    date: '2026-05-13',
    label: 'May 13, 2026',
    entries: [
      {
        title: 'Files page',
        description: 'A new Files section shows your decisions, needles, and recent activity directly in the app. Three tabs, a search box, a card per entry.',
      },
      {
        title: 'iMessage contacts',
        description: 'Your macOS contacts now show up in yourOS so messages can be addressed by name. Loaded from your local Contacts app and stays on your machine.',
      },
      {
        title: 'See your model usage',
        description: 'The Costs page now shows your Claude Code subscription quota and Gemini equivalent next to the cost numbers, so you can see what you have left without leaving yourOS.',
      },
      {
        title: 'PDF and Word doc support for Gems',
        description: 'Drag a PDF or Word document into a Gem and yourOS extracts the text and indexes it. No setup, no extra steps.',
      },
      {
        title: 'Rules page',
        description: 'A new Settings → Rules page where every enforcement rule can be toggled on or off. Quick link in the sidebar.',
      },
      {
        title: 'Delete all data button',
        description: 'Settings now has a danger-zone button to wipe your local yourOS data. Useful for starting fresh.',
      },
      {
        title: 'Faster app startup',
        description: 'The per-prompt header that runs before each turn went from 7 seconds to under a second.',
      },
    ],
  },
  // v3.14.0
  {
    date: '2026-05-11',
    label: 'May 11, 2026',
    entries: [
      {
        title: 'Live updates across the app',
        description: 'Dashboard, notifications, sessions, locks, grants, workflows, and calendar now update in real time instead of polling every few seconds. You see new agent activity the moment it happens.',
      },
      {
        title: 'Agent finishes show up while you chat',
        description: 'When a background agent completes during a chat turn, you get a notification right away instead of waiting for the next message.',
      },
    ],
  },
  // v3.11.3
  {
    date: '2026-05-05',
    label: 'May 5, 2026',
    entries: [
      {
        title: 'Connect more of your work in onboarding',
        description: 'The Connect step now offers Atlassian, GitHub, and Google Workspace setup cards so you can wire up Jira, Confluence, your repo, and Drive/Calendar/Gmail without leaving the wizard.',
      },
      {
        title: 'Global footer with About and Privacy',
        description: 'Every page now has a small footer with About and Privacy links, sticking to the bottom of the viewport.',
      },
      {
        title: 'Settings reorganized',
        description: 'The Settings tabs were reordered, Notifications and ADHD were combined into one tab, and the Privacy tab was removed (its contents moved into About).',
      },
      {
        title: 'Chat picks up your Claude subscription automatically',
        description: 'When you sign in via claude.ai, chat now uses your subscription rather than your API key. No menu toggle needed.',
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
        description: 'When you switch to Gemini, it introduces itself as Gemini rather than taking on the yourOS persona. Each AI now speaks as itself.',
      },
      {
        title: 'Agent template aliases',
        description: 'Agent files now support an ALIASES directive, so a template can be found under any of its alternate names in the picker and command bar.',
      },
    ],
  },
  {
    date: '2026-05-01',
    label: 'May 1, 2026',
    entries: [
      {
        title: 'ADHD Mode',
        description: 'Turn it on in Settings and yourOS checks in when you\'ve been away, welcomes you back with a summary of where things stand, and offers a focus mode that dims distractions while you\'re heads-down.',
      },
      {
        title: 'SDD Wizard',
        description: 'A step-by-step wizard that drafts a Software Design Doc for any feature. Answer a few questions and yourOS fills in context it already knows (open tasks, linked files, recent agents) and saves the spec to your library.',
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
        description: 'The setup wizard now asks what you actually use yourOS for and builds your starter pack around that. Pick your focus and your first agents, specs, and task suggestions are already calibrated to fit.',
      },
      {
        title: 'Brainstorm',
        description: 'Describe a decision you\'re stuck on. Get back 5 to 8 concrete options, each with tradeoffs and effort, plus the one or two the agent would actually recommend. Faster than a whiteboard, sharper than a gut check.',
      },
      {
        title: 'Tasks that read the room',
        description: 'Write "blocking" or "by Friday" in a task and yourOS suggests the right priority for you. Tasks that sit untouched too long quietly drop a level so your urgent list doesn\'t turn into a graveyard.',
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
        title: 'A better tour',
        description: 'The first-time tour now has 7 steps including the Specs and Build-it flow.',
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
        description: 'Set up an org, invite teammates with magic links or SSO, enforce budget limits and agent permissions, and monitor team usage from the admin dashboard. Everything an IT admin needs to roll out yourOS to a team.',
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
        description: 'Migrating from another tool? Import your Linear or Jira issues into yourOS with one click. Priorities, statuses, and labels all map over.',
      },
      {
        title: 'Smart briefing with actions',
        description: 'Your daily briefing now includes clickable action items: close a stale task, reply to an email, prep for a meeting. One click to act.',
      },
      {
        title: 'Agent pattern learning',
        description: 'yourOS tracks which agent setups work well and which keep failing. Proven templates get a green badge. Failing ones get specific suggestions to fix them.',
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
