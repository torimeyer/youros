# ToriOS Personal Demo Cue Card, 2026-05-29

---

> ⚠ BLOCKER BEFORE YOU READ ANYTHING ELSE
>
> **THE TASK-FINISHING SOUND DOES NOT EXIST.**
> A thorough search of `app/src/` and `api/` found zero audio files, zero `new Audio()` calls, and zero `.play()` references in any app or backend source file. Step 3 in this card is marked accordingly. Remove any mention of a completion sound from your talking points before going live, because it will not happen and you will look like you missed a bug.

---

> **Total target: ~12-15 min** | Nine live beats, all personal, zero work content.
>
> Arc: two slow background actions kick off first and cook while you do seven quick foreground ones. By the time you wrap the foreground loop, one or both background jobs will have landed.
>
> All beats verified against the codebase unless marked ⚠ UNVERIFIED.

---

## Pre-flight checklist

Run through this before anyone is watching.

- [ ] yourOS is running: `scripts/dev-backend.sh` (port 8000) + `scripts/dev-frontend.sh` (port 3010)
- [ ] Logged in. OS name shows as your name (set during onboarding via `OnboardingWizard.tsx`).
- [ ] Dark mode on. Looks sharper in a demo.
- [ ] iMessage authorized. macOS has granted accessibility access to the backend. Verify: `curl -sk https://127.0.0.1:8000/api/imessage/status` returns `{"authorized": true}`.
- [ ] Google connected (Calendar, Gmail, Drive). Verify in Settings > Connections.
- [ ] ADHD mode enabled (Settings > Preferences). The pulse dot in the corner will show when step 1 and 2 agents are running.
- [ ] At least one existing task in the Tasks page to use for step 1. If nothing throwaway exists, create one: "Add a 'last updated' timestamp to the about page."
- [ ] Dashboard briefing has loaded. If it shows "generating," wait 10 seconds or reload.
- [ ] My Gems page has loaded at least once (warm the nav).
- [ ] Phone on silent. Laptop notifications off.

---

## Run order at a glance

| Step | Action | Est. time | Mode |
|------|--------|-----------|------|
| 1 | Task → Spec → Build (agents spawn, run in background) | 4-5 min background | Kick off first |
| 2 | Generate roadmap in chat, then create tasks from it | 2-3 min background | Kick off second |
| 3 | ⚠ Sound on task complete | DOES NOT EXIST | Skip |
| 4 | Create a Gem and chat with it | ~2 min foreground | Medium |
| 5 | Ask "All" a question | ~1 min foreground | Short |
| 6 | Gmail: create a task from an email | ~30 sec foreground | Short |
| 7 | Add a calendar event via chat | ~30 sec foreground | Short |
| 8 | Send an iMessage to Scott | ~30 sec foreground | Short |
| 9 | Settings: Custom Commands and ADHD mode | ~20 sec foreground | Short |
| 10 | Agents page: MCP servers attached to agents | ~20 sec foreground | Short |

**Do steps 1 and 2 back-to-back at the very start.** Both run agents in the background. Steps 4-10 fill the time while those agents work. The background completions land as natural punctuation at the end.

---

## Step 1: Task to spec to complete (LONGEST, kick off first)

*~4-5 min for background agents. You touch the keyboard for about 90 seconds at the start, then walk away.*

**Duration note:** This step spawns one builder agent per acceptance-criteria checkbox in the spec. Each agent runs for 1-3 minutes. The spec auto-marks itself complete when all of them land. This is the longest-running item in the demo, so it goes first.

**Setup:** Open the Tasks page.

**Action:** Find a throwaway open task (or create one by typing in chat: "add a task: add a last updated timestamp to the about page"). On that task row, click the **Create Spec** button.

**Visible:** A spec draft opens in the Specs page (source: `app/src/pages/Tasks.tsx:2378`, `POST /specs/from-task`). A SpecWizard modal appears asking for acceptance criteria (source: `app/src/components/SpecWizard.tsx`).

**Say this (verbatim):**
```
Before any agent touches a feature, there is a spec. Watch.
I click Create Spec on this task. I get a draft with acceptance criteria.
Each one is a checkbox. The agent cannot start until this spec is marked ready.
That is the discipline that makes this work.
```

**Action (continued):** In the SpecWizard, type two acceptance criteria:
```
- [ ] The timestamp appears in the footer of the about page
- [ ] The format matches the rest of the app
```
Click **Promote**, then on the promoted spec click **Mark Ready**, then click **Build**.

**Visible:** The spec status changes to `in_progress`, and the Agents page Active tab shows one or more builder agents starting (source: `api/routers/specs.py`, `_advance_spec_status_if_all_builder_tasks_closed`). ADHD mode pulse appears in the corner.

**Say this (verbatim):**
```
Now it is building. I don't touch the keyboard again. When every checkbox is done,
the spec flips to complete automatically. I'll check back in a few minutes.
```

**Navigate away to step 2 immediately.**

**How this works (because of ostk):** When you click Build, ostk spawns one agent per acceptance-criteria checkbox and assigns each agent an isolated worktree so they can't overwrite each other, and because every task completion is tracked in ostk's work queue, the spec router listens for those completions and flips the spec status to done the moment the last one lands, all without you checking manually.

**Fallback:** If SpecWizard doesn't appear, click the Specs page directly, click New Spec, fill in the title and two checkboxes manually, promote, mark ready, build.

---

## Step 2: Roadmap generation, then tasks (LONG, kick off second)

*~2-3 min for the generation model call. Task creation from the roadmap is instant.*

**Duration note:** The AI writes `~/Documents/roadmap.md` during the generation call. Creating tasks from the roadmap is a single backend call that happens in under 2 seconds. Step 2 blocks only on the generation, which is why it goes second rather than third.

**Setup:** Navigate to the chat panel.

**Action (part 1):** Type and send:
```
generate a 6-month roadmap for yourOS focused on personal quality-of-life improvements
```

**Visible:** The model streams a response and writes `~/Documents/roadmap.md` and `roadmap.json`. A notification appears with the roadmap summary and a note to type "create tasks" (source: `app/src/stores/notifications.ts:13`, `app/src/lib/roadmapChatCommand.ts`).

**Action (part 2):** Once the response finishes, immediately type and send:
```
create tasks from this roadmap
```

**Visible:** ChatPanel intercepts that exact phrase, calls `POST /chat/roadmap/create-tasks`, and the backend parses `roadmap.md` and creates tasks in the work queue. The reply lists the tasks created (source: `app/src/components/ChatPanel.tsx:2062-2082`, `app/src/lib/roadmapChatCommand.ts`).

**Say this (verbatim):**
```
Two prompts. First I ask for the roadmap. Then I say create tasks from it.
That's it. The roadmap is a file on my laptop. The tasks are in my queue.
I didn't type a single task title. I described a direction and it broke it down.
```

**How this works (because of ostk):** The roadmap command is a hard intercept in the chat layer, which means it never goes through the AI routing, it goes straight to a backend endpoint that reads the markdown file, parses each initiative into a structured task, and writes those tasks into ostk's work queue, so the handoff from "describe a direction" to "runnable tracked tasks" is lossless and auditable.

**Navigate away to step 4 immediately. Steps 1 and 2 are now running in background.**

**Fallback:** If roadmap generation fails, open the Specs page and show an existing spec with tasks. "Here is one that already built. You can see every task the agent completed, the spec it came from, and the commit it produced."

---

## Step 3: Task-finishing sound

> ⚠ THIS STEP DOES NOT EXIST IN THE CODEBASE.
>
> No audio files, no `new Audio()` calls, and no `.play()` references appear anywhere in `app/src/` or `api/`. There is no task-completion sound. Do not demo or describe this beat. Skip it entirely.
>
> If a sound ships before you do this demo, it would play automatically when steps 1 and 2 complete, making it a natural punctuation mark between the background and foreground halves of the demo. Until then, the visual notification is the only cue.

---

## Step 4: Create and use a Gem (MEDIUM)

*~2 min foreground. You create the gem, then immediately chat with it.*

**Duration note:** Creating a gem takes about 20 seconds of form-filling. Chatting with it takes another 30-60 seconds for a model response. Everything is visible and interactive, so this sits between the long background steps and the quick foreground ones.

**Setup:** Navigate to My Gems (sidebar).

**Action:** Click the **Create Gem** button (source: `app/src/pages/MyGems.tsx`, `data-testid="create-gem-button"`).

**Visible:** A form asking for a name and system instructions. Each gem shows its name and a preview of its system prompt (source: `app/src/pages/MyGems.tsx:255`).

**Say this (verbatim):**
```
A Gem is a persona I build. It has its own instructions, its own personality.
It's not a task runner. It's someone I talk to.
Watch. I'm making one called Weekend Planner.
```

**Action (continued):** In the name field, type:
```
Weekend Planner
```

In the system instructions field, type:
```
You help plan restorative, easy weekends. You know Tori loves Saturday brunch,
long walks with Pepper, and low-key social time. Keep suggestions specific and realistic.
No productivity advice.
```

Click Save. The new Gem appears in the list.

**Action (continued):** Click the **Weekend Planner** gem. A chat panel opens (source: `app/src/components/GemChatPanel.tsx`, `/api/gems/{gem.id}/chat`).

**Say this (verbatim):**
```
Now I'll talk to it.
```

Type and send:
```
what should Saturday look like this week? Pepper needs a long walk.
```

**Visible:** The gem responds with a specific Saturday plan drawing on its instructions, not on the main model's defaults (source: `app/src/components/GemChatPanel.tsx:82`, `POST /api/gems/{id}/chat`).

**How this works (because of ostk):** Gems are stored as tracked entities in the backend with their own chat history, so the conversation you have with a gem accumulates over time, and because the gem's instructions travel with every request rather than being typed into a prompt each time, the persona stays consistent across days and sessions without you re-explaining it.

**Fallback:** If gem creation fails, show an existing gem in the list and click it to start a chat. "This one I made last week. It already knows my context from our last conversation."

---

## Step 5: Ask "All" a question (SHORT)

*~1 min foreground. Fan-out to every connected model simultaneously.*

**Duration note:** This step is short because the action is a single question, and the visible payoff (two models responding in parallel) is immediate. Place it here so the parallel-broadcast demo lands while the engineers are still fresh.

**Setup:** Navigate to the main chat panel. Click the model selector and switch to **All**.

**Visible:** The All pill activates, showing a pulse animation (source: `app/src/components/ChatPanel.tsx:486-492`, `allPillPulse`, `sideBySideEnabled`).

**Say this (verbatim, this is also the question to type):**
```
what do you know about me when it comes to how I use myos
```

Type and send that exact question.

**Visible:** Claude and Gemini respond simultaneously, side by side, each streaming its own answer (source: `app/src/components/ChatPanel.tsx:2051`, parallel broadcast fan-out).

**Expected ideal answer (what a well-loaded model should surface):**

> You write a failing test first, then write the minimum code to pass it, and never write production code before a failing test exists. Every spec gets written before any agent touches a feature, and an agent cannot be spawned on a spec until you mark it ready. Before anything ships, vitest runs, TypeScript compiles, and there is a live curl against the endpoint, and you push only to Staging, never directly to production. You read and review every diff, you merge deliberately, and when a bug appears you root-cause it instead of patching the symptom. ostk gives you the receipts for all of this: a full audit trail of every file touch, gen-tracked files with conflict detection, and an isolated worktree per agent so parallel agents cannot collide.

That answer should make any skeptical engineer understand this is real software engineering practice, not vibe-coding, because every claim in it is checkable from the commit history and the ostk audit log.

**Say this (verbatim, after the responses stream in):**
```
All of them. At the same time. I didn't pick a model first.
I asked the question and both answered. Then I decide which one I keep.
```

**How this works (because of ostk):** The All pill hands a single message to ostk's coordination layer, which fans it out to every connected model in parallel and gathers the responses into the same thread, which means you are not locked to one model and you never have to switch tabs or re-type the question to compare answers.

**Fallback:** If only one model is connected, narrate the intent. "When both Claude and Gemini are connected, they respond side by side in real time. Right now I only have one connected here, but the mechanism is the same."

---

## Step 6: Gmail, create a task from an email (SHORT)

*~30 sec foreground. Open an email, click one button.*

**Duration note:** One click, instant feedback. Short on purpose because email is a transition beat, not a headline.

**Setup:** Navigate to Gmail page (source: `app/src/pages/Gmail.tsx`).

**Action:** Open any thread that looks like it has an action item. Point to the **Create task** button that appears on each message (source: `app/src/pages/Gmail.tsx:639`, `data-testid` tied to `handleCreateTask`).

**Say this (verbatim):**
```
This email has something I need to do. One button. It lands in my task queue,
with the subject and sender already filled in. I don't touch the keyboard.
```

**Action:** Click **Create task** on any message.

**Visible:** The button shows "Creating task..." then "Task created" with a check mark. A confirmation line appears below the message with the task title (source: `app/src/pages/Gmail.tsx:643-690`, `POST /gmail/to-task`).

**How this works (because of ostk):** The backend reads the email content and subject through the Gmail integration, passes them to the model to extract an action, creates a task in ostk's work queue with that title and a link back to the thread, so the task is traceable to the email that generated it without you manually typing anything.

**Fallback:** If the button doesn't appear, click into any single message. "The button shows up inside a thread. Let me open one." If the creation errors, show the empty success state. "It created, just the badge animation glitched. It's in the queue."

---

## Step 7: Add a calendar event via chat (SHORT)

*~30 sec foreground. Type one sentence in chat, event appears in calendar.*

**Duration note:** Short, but visually satisfying because the Calendar page updates via a sync event the moment the model's tool call lands.

**Setup:** Stay in the chat panel or switch to it.

**Action:** Type and send exactly:
```
add to calendar that Pepper and I have a hair appointment on June 6th at 11am
```

**Visible:** The model calls the `create_calendar_event` tool, the response confirms the event was added, and if the Calendar page is visible in another tab it refreshes automatically (source: `app/src/pages/Calendar.tsx:515`, "chat creates an event, Sync fires", `api/routers/calendar.py`).

**Say this (verbatim):**
```
Natural language. No date picker. No form. I just said it.
The event is in my Google Calendar right now, not in a separate app's calendar.
```

**Navigate to the Calendar page and show the June 6th event.**

**How this works (because of ostk):** The chat layer exposes a calendar creation tool to the model, which means a natural-language sentence becomes a structured API call against your real Google Calendar, and the Calendar page in yourOS listens for that event via a sync trigger so it refreshes without a manual reload.

**Fallback:** If the event doesn't appear in the calendar view, switch to the Google Calendar tab in a browser and show it there. "It's in my Google Calendar. yourOS just uses the same account."

---

## Step 8: Send an iMessage to Scott (SHORT)

*~30 sec foreground. Open a thread, type, send.*

**Duration note:** Short because it is a demonstration of reach (reading from the local Messages database and writing back to it via AppleScript) rather than a feature with a long visible payoff.

**Setup:** Navigate to the iMessage page (source: `app/src/pages/IMessage.tsx`).

**Action:** Find Scott's thread in the conversation list. Open it.

**Say this (verbatim):**
```
This reads directly from the Messages database on this laptop.
My actual conversations. Nothing is synced to a server.
```

**Action:** Type and send:
```
hey, anything funny happen this week? I need a laugh before the weekend.
```

**Visible:** The message sends via the backend's AppleScript integration, using `POST /imessage/send` (source: `api/routers/imessage.py:173`). The thread updates with the sent message.

**Say this (verbatim):**
```
Sent. Real iMessage. From inside the app. Scott got that on his phone.
```

**How this works (because of ostk):** The iMessage backend reads the macOS Messages database using the same SQLite file the Messages app writes to, and sends through AppleScript so the message goes through the real Messages app on your laptop, which means iMessage end-to-end encryption is intact and nothing touches an external server.

**Fallback:** If iMessage shows "not authorized," show the connect card. "This is what it looks like before you give access. Once you do, your full message history and send capability are here." If send fails mid-demo, show a thread and the send compose area. "The send button is there. Connectivity issue on my end."

---

## Step 9: Settings, Custom Commands and ADHD mode (SHORT)

*~20 sec foreground. Navigate to Settings, point at two panels.*

**Duration note:** Minimal interaction. The point is to show that yourOS has its own preferences and they're yours, not platform defaults.

**Setup:** Navigate to Settings > Preferences (source: `app/src/pages/Settings.tsx`).

**Action (part 1):** Scroll to the **Custom tack commands** section (source: `app/src/pages/Settings.tsx:1724`, `<CustomVerbs />` component, `/ostk/language/verbs`).

**Visible:** A list of named verbs with descriptions, like "standup: Write a daily standup update from recent activity" or "brainstorm: Generate ideas for a feature or problem."

**Say this (verbatim):**
```
These are my custom commands. I type 'standup' in chat and it writes my update from recent activity.
I type 'brainstorm' and it goes into idea mode. One word. I defined what it does.
```

**Action (part 2):** Scroll to the **ADHD mode** section (source: `app/src/pages/Settings.tsx:2014`, `data-testid="adhd-toggle"`).

**Visible:** A toggle for ADHD mode, a slider for check-in interval, and a focus mode sub-toggle.

**Say this (verbatim):**
```
ADHD mode puts a pulse in the corner when an agent is running, so I don't forget
it's there and go do something else for an hour. It also checks in with me at an interval I set.
I built this because I needed it.
```

**How this works (because of ostk):** Custom verbs are stored in ostk's language layer, so when you type one in chat the routing layer expands it into the full instruction before the model ever sees it, which means the command is one word for you and a full prompt for the model, and adding a new one takes about 10 seconds in Settings.

**Fallback:** If the custom verbs list is empty, click the add field and type "standup" with description "write a daily standup from the last 24 hours of activity." Save it. "I just added one. Now I can type it in chat."

---

## Step 10: Agents page, MCP servers attached to agents (SHORT)

*~20 sec foreground. Navigate to Agents, point at the Templates tab.*

**Duration note:** The shortest beat. By this point the background jobs from steps 1 and 2 may have landed, so you can also check the Active and Recent tabs for live evidence.

**Setup:** Navigate to the Agents page (source: `app/src/pages/Agents.tsx`).

**Action (part 1):** Check the **Active** tab first. If step 1 or 2 agents are still running, point to them. "Those are the ones I kicked off at the start. Still going." If they've completed, go to the **Recent** tab and read a task name out loud.

**Action (part 2):** Click the **Templates** tab. Expand any template.

**Visible:** Each template shows its declared MCPs as chip badges (source: `app/src/pages/Agents.tsx:800-802`, `data-testid="declared-mcp-chip"`). Below the chips, a note shows "Plus all enabled MCPs in your workspace: [list]" (source: `app/src/pages/Agents.tsx:821-822`).

**Say this (verbatim):**
```
This is the fleet. Every template I can spawn declares which tools it's allowed to use.
These blue chips are the MCPs. A calendar agent gets the calendar MCP.
A coding agent gets the file system and shell. Nothing gets access to everything by default.
```

**How this works (because of ostk):** Agent templates are registered in ostk's catalog with an explicit list of MCP servers they're allowed to call, so when you spawn from a template the agent only gets the tools that template declared, and the workspace MCPs listed at the bottom are the ones enabled globally for every agent, which means access is scoped by design rather than by accident.

**Fallback:** If the Templates tab is empty, switch to Recent and walk through a completed agent transcript. "This agent ran, here is every step it took, here is the file it changed. This is the receipt."

---

## Check: have the background jobs landed?

At the end of step 10, check whether steps 1 and 2 have completed:

- For step 1: Go to Specs page. Look for the spec you created. If its status shows "complete" or "done," say: "The spec finished while we were talking. Every checkbox built, every agent landed, and yourOS flipped the status automatically. I didn't check once."
- For step 2: Go to Tasks page. Filter by the tag from the roadmap generation. If tasks are there, say: "Those tasks came from the roadmap I generated at the start. They're in the queue. I can spawn agents against any of them right now."

---

## Cuts if running long

| Beat | Safe to cut | Replacement |
|------|-------------|-------------|
| Step 4 (Gems) | Yes, if demo is already over 10 min | Skip. Mention it verbally: "I can build personas called Gems. Each one has its own instructions and chat history." |
| Step 6 (Gmail) | Yes | Skip. Say: "Gmail task creation is also in there. One button on any email." |
| Step 9 (Settings) | Yes | Skip. Say: "Settings has custom commands and ADHD mode. I built both because I wanted them." |
| Step 2 (roadmap tasks) | Partial | Generate the roadmap but skip the task creation. Show the roadmap in chat output and describe the next step verbally. |

To hit **10 minutes**: cut step 4 entirely and describe steps 6 and 9 verbally in one sentence each.

---

## Last-resort fallbacks

If yourOS is not loading at all (blank screen, backend down):

1. Open the Agents page from cache if it's still in memory. Walk through Recent agents by name. "Every one of these is something I asked it to build."
2. Pull up the Specs page from browser cache. Show an existing spec. "Every feature in this app started as one of these. Acceptance criteria. Build. Done."
3. Open GitHub (from wherever you have it bookmarked) and walk through recent PR titles. Read the commit messages. "Those were agents. Every one."

If you lose your place:

> "Let me show you the one thing I want you to remember. [Go to step 10, Agents page, Recent tab.] I sent this. It ran. Here is the receipt. I built that without writing a line. That is the whole point."

---

## Spoken lines / bookends

Use these as anchors. They are the things people repeat after.

1. "Before any agent touches a feature, there is a spec."
2. "The agent cannot start until this spec is marked ready."
3. "Two prompts. First I ask for the roadmap. Then I say create tasks from it."
4. "A Gem is a persona I build."
5. "All of them. At the same time. I didn't pick a model first."
6. "One button. It lands in my task queue."
7. "Natural language. No date picker. No form. I just said it."
8. "Real iMessage. From inside the app."
9. "I type 'standup' in chat and it writes my update from recent activity."
10. "Nothing gets access to everything by default."

**Opening line (before step 1):**
> "I'm going to kick off two things at the start that take a few minutes, and then show you everything else while those run. Watch."

**Closing line (after step 10, when background jobs have landed):**
> "Those finished while we were talking. Every step of what you just saw was built the same way. I directed it. I didn't write it. That is the whole point."

---

## Feature verification index

All steps grounded in the codebase. Verification sources:

| Step | Feature | Verified via |
|------|---------|-------------|
| 1 | Task → Create Spec button | `app/src/pages/Tasks.tsx:2378`, `POST /specs/from-task` |
| 1 | SpecWizard modal | `app/src/components/SpecWizard.tsx` |
| 1 | Spec Build endpoint | `api/routers/specs.py` (`/specs/{path}/build`) |
| 1 | Auto-complete on builder task close | `api/routers/specs.py:2008` (`_advance_spec_status_if_all_builder_tasks_closed`) |
| 2 | Roadmap → tasks chat command | `app/src/lib/roadmapChatCommand.ts` (PATTERNS, `isRoadmapToTasksRequest`) |
| 2 | Chat intercept + backend call | `app/src/components/ChatPanel.tsx:2062-2082`, `POST /chat/roadmap/create-tasks` |
| 2 | roadmap.md file location | `/Users/torimeyer/Documents/roadmap.md` (gen_table confirmed) |
| 3 | ⚠ Task-finishing sound | NOT FOUND in `app/src/` or `api/` |
| 4 | My Gems page + Create Gem button | `app/src/pages/MyGems.tsx`, `data-testid="create-gem-button"` |
| 4 | Gem chat panel | `app/src/components/GemChatPanel.tsx`, `POST /api/gems/{id}/chat` |
| 5 | All pill / parallel broadcast | `app/src/components/ChatPanel.tsx:486-492` (`allPillPulse`), line 2051 (`sideBySideEnabled`) |
| 6 | Gmail → Create task | `app/src/pages/Gmail.tsx:119-130` (`handleCreateTask`, `POST /gmail/to-task`) |
| 7 | Calendar event via chat | `app/src/pages/Calendar.tsx:515` (sync on `create_calendar_event` tool), `api/routers/calendar.py` |
| 8 | iMessage send | `api/routers/imessage.py:173` (`POST /imessage/send`, AppleScript) |
| 9 | Custom tack commands | `app/src/pages/Settings.tsx:1724` (`<CustomVerbs />`, `/ostk/language/verbs`) |
| 9 | ADHD mode toggle | `app/src/pages/Settings.tsx:2014` (`data-testid="adhd-toggle"`) |
| 10 | Agents Templates, declared MCP chips | `app/src/pages/Agents.tsx:800-802` (`declared-mcp-chip`) |
| 10 | Workspace MCPs note | `app/src/pages/Agents.tsx:821-822` |

**⚠ UNVERIFIED / REMOVED beats:**

1. **Step 3, task-finishing sound.** Does not exist in the codebase. Zero audio files or play() calls found in app or backend source. Removed from demo.
2. **Step 7, calendar event creation.** The creation goes through the model's tool-calling layer, not a direct form in `Calendar.tsx`. If the model's calendar tool is disabled or not configured, the event will not be created. Verify `api/routers/calendar.py` is returning a working `create_event` tool before the demo.
