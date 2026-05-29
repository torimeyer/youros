# ToriOS Personal Demo Cue Card — 2026-05-29

> **Total target: ~12-15 min** | Ten beats, all personal, zero work content.
>
> Arc: personal magic → ambient capture → spawn → memory → fan-out → external montage → fleet → yourOS proof-of-work → roadmap → meta-recursive close
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
- [ ] Slack connected. Verify: the Slack page shows at least one channel.
- [ ] ADHD mode enabled (Settings > Preferences). This makes the agent pulse pop in the corner when Beat 3 runs.
- [ ] At least 2-3 agents in the Recent tab so the fleet panel looks alive.
- [ ] Dashboard briefing has loaded. If it shows "generating," wait 10 seconds or reload.
- [ ] Tabs pre-opened in order: Dashboard, iMessage, Agents, Specs, GitHub.
- [ ] Phone on silent. Laptop notifications off.

---

## Beat 1 — Personal magic (1:30)

**Setup:** Dashboard is open on screen. The daily briefing card is visible. The calendar grid widget is visible.

**Action:** Just look at it. Read the briefing out loud. Click one action item.

**Visible:**
- AI-generated morning briefing with 2-3 action items (source: `GET /briefing`, rendered in `app/src/pages/Dashboard.tsx`)
- CalendarGridWidget showing today's events (source: `app/src/components/CalendarGridWidget.tsx`)
- Greeting style (configured in Settings, stored via `useAppStore`)

**Narration (say this):**
> "Every morning, this is the first thing I open. Not email. Not Slack. This. It reads my calendar overnight and writes me a little briefing. Here is what is on my plate today, here is what needs my attention first. That is mine. I did not configure that. I told it once how I like to start my day, and now it does it."

**Lines:**
1. "It already knows what today looks like."
2. "I didn't build this view. I described what I wanted and it built it."

**Fallback:** If briefing is still generating, point to the CalendarGridWidget instead. "This pulls live from my Google Calendar. Hair appointment, call with my mom on Saturday. It is all here."

---

## Beat 2 — Ambient capture: iMessage (1:00)

**Setup:** Click to the iMessage page (`app/src/pages/IMessage.tsx`).

**Action:** Open a recent conversation with Scott. Point to the most recent message.

**Visible:**
- Real iMessage threads read from `~/Library/Messages/chat.db` via the macOS SQLite database (source: `api/routers/imessage.py`)
- Contact names, timestamps, message previews
- Attachment thumbnails if any

**Narration (say this):**
> "Scott texted me this morning. It is right here. Not a synced app, not a notification mirror. It reads directly from the Messages database on this laptop. My conversations are on my machine. yourOS reads them locally. Nothing leaves."

**Lines:**
3. "My phone, my laptop, same conversations. No app to install."
4. "This reads from my computer, not from Apple's servers."

**Fallback:** If iMessage shows a connect card (permissions not granted), pivot: "This is what it looks like before you authorize it. Once you do, your full message history is right here." Then move on.

---

## Beat 3 — Spawn (SAA): building something live (2:00)

**Setup:** Open the chat panel. ADHD mode is on.

**Action:** Type the following and hit send:
```
saa - write a bedtime story for the Fox and Mama Lego app. Fox and Mama go on an adventure to find the best Saturday morning pancakes in the whole neighborhood.
```

**Visible:**
- Agent registers in the Active tab (`app/src/pages/Agents.tsx`, Active tab)
- Pulsing green dot, task description: "write bedtime story for Fox and Mama"
- ADHD mode pulse appears in bottom-right corner (`app/src/components/AdhdCheckin.tsx`): "1 agent working"

**Narration (say this):**
> "SAA means spawn an agent. I don't write the story. I describe what I want, and an agent goes and does it. Right now it is running in the background. I will go do something else. When it is done, I will check back. That is the whole model. I direct. It builds."

**Lines:**
5. "SAA. Spawn an agent. One word and it is running."
6. "I come back when it is done."

**Note on Fox and Mama's Best Days:** The Lego app exists as a tracked project in the yourOS task system (tag: `lego-app`, sourced from `api/routers/tasks.py`). The name "Fox and Mama's Best Days" comes from user memory, not the codebase.

**Fallback:** If no internet or agent fails to start, go to the Recent tab and show a completed agent: "Here is one that just finished. You can see the task, when it ran, and tap to read its transcript."

---

## Beat 4 — Memory recall (1:00)

**Setup:** Stay in the chat panel.

**Action:** Ask:
```
What do you know about me?
```

**Visible:**
- Chat response drawing on user memory (source: `api/routers/user_memory.py`, `api/routers/memory.py`)
- Preferences, facts, past context the user has shared

**Narration (say this):**
> "It knows I have a dog. It knows I hate when things are over-explained. It knows I prefer bullet points over paragraphs. I did not configure any of that in a settings form. I mentioned it in passing once and it remembered. Every new conversation starts with that context already loaded."

**Lines:**
7. "I told it once. It remembered."
8. "That is not a settings panel. That is just memory."

**Fallback:** If memory response is thin, ask instead: "What's on my calendar this week?" and show it pulling from the Google Calendar integration.

---

## Beat 5 — Fan-out: the All pill (1:30)

**Setup:** Still in the chat panel. Switch the model selector to "All."

**Action:** With the All pill selected, ask something fun:
```
Is a hot dog a sandwich? Make your case.
```

**Visible:**
- Claude and Gemini (and any other connected model) all responding simultaneously, side-by-side (source: `app/src/components/ChatPanel.tsx`, `allPillPulse`, parallel broadcast fan-out logic)
- Live status pill showing "Asking Claude, Gemini..." while responses stream in

**Narration (say this):**
> "All sends to every model at the same time. Not in sequence. Right now Claude and Gemini are both writing their answer simultaneously. I read them both. I pick whichever one is more convincing. No toggling, no tab-switching."

**Lines:**
9. "All of them. At once. Same question."
10. "I don't pick a model before I ask. I ask all of them and then decide."

**Fallback:** If only one model is connected, say: "Right now I only have Claude connected here, but when Gemini is also connected, they respond side by side in real time."

---

## Beat 6 — External montage: Calendar, Gmail, Slack (1:30)

Move quickly through three tabs. 20-25 seconds each.

### 6a — Calendar
**Action:** Click to Calendar page (`app/src/pages/Calendar.tsx`).

**Visible:** Google Calendar events in the app. Point to a personal appointment.

**Say:** "My calendar, pulled from Google. Hair with Pepper is in there. Call with my mom on Saturday. I can see the week without opening Google."

### 6b — Gmail
**Action:** Click to Gmail page (`app/src/pages/Gmail.tsx`).

**Visible:** Inbox threads inside yourOS.

**Say:** "My inbox is here too. I can read and reply without leaving. yourOS drafts replies for me if I ask." (Point to the reply composer if visible: source `app/src/components/GmailReplyComposer.tsx`, placeholder: "Write your reply, or let yourOS draft one for you.")

### 6c — Slack
**Action:** Click to Slack page (`app/src/pages/Slack.tsx`).

**Visible:** Slack channels and messages.

**Say:** "And Slack. I don't love switching between apps. So I stopped."

**Narration after the montage:**
> "Calendar, email, messages, texts. All running locally. None of this goes to a cloud service. It is on my laptop."

**Fallback for any disconnected service:** Show the connect card. "This is what it looks like before connecting. One-time setup, and then it is here every time I open the app."

---

## Beat 7 — Fleet visibility: the Agents page (1:30)

**Setup:** Click to Agents page (`app/src/pages/Agents.tsx`).

**Action:** Walk through Active, Recent, and Templates tabs.

**Visible:**
- Active tab: The agent from Beat 3 still running (or completing)
- Green pulse dot beside its task name
- Recent tab: History of completed agents with task names and timestamps
- Templates tab: Pre-built agent types ready to launch

**Narration (say this):**
> "This is the fleet. Every agent I have sent. Still running ones here, finished ones there. I can tap any of them and read the full transcript. Every decision it made, every file it touched. I can also send it a message mid-run. It gets my message and adjusts."

**Lines:**
11. "I sent it. It ran. Here is the receipt."
12. "I can message it while it is still working."

**Also show:** If ADHD mode is still pulsing in the corner, point to it. "That is ADHD mode. It reminds me something is running so I don't forget and leave it going for hours."

**Fallback:** If Active tab is empty because Beat 3 already completed, show the Recent tab and read a task name out loud.

---

## Beat 8 — yourOS proof-of-work (2:30)

*This is the serious beat. Let it land.*

**Setup:** Click to Specs page (`app/src/pages/Specs.tsx`), then to GitHub page (`app/src/pages/GitHub.tsx`).

**Action — Part 1 (Specs page):**
Open a spec. Point to the acceptance criteria checkboxes.

**Visible:**
- Spec with status ("draft", "ready", "in-progress", "complete"), source: `app/src/pages/Specs.tsx`
- Acceptance criteria as checkboxes (`acceptance_criteria` field, each a `- [ ]` item)
- "Clear to build" checks before an agent can be spawned on it

**Say:**
> "Before any agent touches a feature, there is a spec. The spec has acceptance criteria. Each one is a checkbox. An agent cannot be spawned on this until the spec is marked ready. That is the discipline."

**Action — Part 2 (GitHub page):**
Open a recent merged PR.

**Visible:** PR description. Point to the footer.

**⚠ UNVERIFIED:** The exact PR footer text "All code in this PR was written by Claude. Tori managed and directed but did not write any code." has not been verified as a hardcoded string in the codebase. This is a practice convention, not a UI feature. Confirm this is something you manually write into PR descriptions before using this beat.

**Say (adjusted to be accurate):**
> "Every PR that ships gets a footer I write myself. It says: all the code in this PR was written by Claude. I managed and directed but did not write any code. That is literally true. I review the diff. I approve. I merge. I do not type the implementation."

**Action — Part 3 (the discipline):**
**Say:**
> "Before anything ships: vitest runs, TypeScript compiles, and there is a live curl check against the endpoint. I do not push to production. I push to Staging. If the curl check passes live, then it ships. The test suite tells me the code is correct. The curl tells me the feature is actually working."

**Narration (full beat):**
> "I built this app the same way I use it. I wrote specs. I spawned agents. I reviewed the work and merged the PRs. The commit history is a record of every conversation. I have shipped over a hundred features to this app without writing a single line of code. Not because I couldn't. Because I didn't need to."

**Lines:**
13. "The spec is the contract. The agent fulfills it."
14. "I don't write code. I build software."

---

## Beat 9 — Adventure templates: what's next (1:00)

**Setup:** Go back to Dashboard. Scroll to the Adventure card (source: `app/src/pages/Dashboard.tsx`, `AdventureTemplate` from `app/src/lib/adventures.ts`).

**Action:** Show the Adventure card with templates. Click one or hover over it.

**Visible:**
- Adventure templates with titles, taglines, and icons
- A description field where you add context before spawning
- "Start this adventure" spawns a set of tasks and agents automatically

**Narration (say this):**
> "When I want to start something new, I pick a template. I describe what I'm going for. It breaks it into tasks and kicks off the work. I come back to something started."

**Lines:**
15. "I describe what I want. It figures out the steps."

**Fallback:** If Adventure card is dismissed, go to the Backlog page (`app/src/pages/Backlog.tsx`) and show the task list. "Here is everything I've asked it to build. Open, in progress, done. A backlog it manages for me."

---

## Beat 10 — Meta-recursive close (0:30)

**Setup:** Return to chat panel.

**Action:** Type and send:
```
What should we build next for you?
```

**Visible:**
- Chat response: yourOS suggesting improvements to itself.

**Narration (say this):**
> "I asked it what we should build next. For the app itself. It tells me. And then I probably will."

**Then:** Close the laptop lid (or just stop talking). Let the silence do the work.

**Line:**
16. "It tells me what it needs. I build it. We go again."

---

## Cuts if running long

| Beat | Safe to cut | Replacement |
|------|-------------|-------------|
| Beat 2 (iMessage) | Yes, if audience won't care about local data | Skip, go straight to Beat 3 |
| Beat 5 (All pill) | Yes if only one model is connected | Replace with: describe it, don't show it |
| Beat 6c (Slack) | Yes | Drop to Calendar + Gmail only |
| Beat 9 (Adventure templates) | Yes | Jump straight to Beat 10 |
| Beat 4 (Memory) | Maybe | Merge with Beat 1 by quoting something it remembered during the briefing |

To hit **10 minutes** from 12-15: cut Beats 2, 5 (describe only), and 9.

---

## Last-resort fallbacks

If yourOS is not loading at all (blank screen, backend down):

1. Open the Arcade / Break Room (`app/src/pages/BreakRoom.tsx`). "I built this too. The games are here because I wanted them. I spawned an agent for each one." Shows the fleet model in a low-stakes way.
2. Show the GitHub page directly. Walk through recent PR titles. Read the commit messages. "Every one of these was an agent. Every message was me."
3. Pull up the Agents page from cache. Even if the backend is down, recently-viewed agent data may still be in the browser.

If you lose your place:

> "Let me show you the one thing I want you to remember. [Go to Beat 8 only.] I do not write code. I build software. That distinction is the whole point of this."

---

## Spoken lines / bookends (numbered master list)

Use these as anchors. They are the things people will repeat after.

1. "It already knows what today looks like."
2. "I didn't build this view. I described what I wanted and it built it."
3. "My phone, my laptop, same conversations. No app to install."
4. "This reads from my computer, not from Apple's servers."
5. "SAA. Spawn an agent. One word and it is running."
6. "I come back when it is done."
7. "I told it once. It remembered."
8. "That is not a settings panel. That is just memory."
9. "All of them. At once. Same question."
10. "I don't pick a model before I ask. I ask all of them and then decide."
11. "I sent it. It ran. Here is the receipt."
12. "I can message it while it is still working."
13. "The spec is the contract. The agent fulfills it."
14. "I don't write code. I build software."
15. "I describe what I want. It figures out the steps."
16. "It tells me what it needs. I build it. We go again."

**Opening line (before Beat 1):**
> "I want to show you something that is actually mine."

**Closing line (after Beat 10):**
> "Everything you just saw was built by Claude. I directed every part of it. I did not write a line. That is the whole point."

---

## Feature verification index

All beats grounded in the codebase. Verification sources:

| Beat | Feature | Verified via |
|------|---------|-------------|
| 1 | Morning briefing | `app/src/pages/Dashboard.tsx`, `GET /briefing` |
| 1 | Calendar widget | `app/src/components/CalendarGridWidget.tsx` |
| 2 | iMessage integration | `api/routers/imessage.py`, `app/src/pages/IMessage.tsx` |
| 3 | Agent spawn (SAA) | `app/src/pages/Agents.tsx` Active tab |
| 3 | ADHD mode pulse | `app/src/components/AdhdCheckin.tsx` |
| 3 | Lego-app project | `api/routers/tasks.py` tag: `lego-app` |
| 4 | User memory | `api/routers/user_memory.py`, `api/routers/memory.py` |
| 5 | All pill / fan-out | `app/src/components/ChatPanel.tsx` (allPillPulse, parallel broadcast) |
| 6a | Google Calendar | `app/src/pages/Calendar.tsx` |
| 6b | Gmail | `app/src/pages/Gmail.tsx`, `app/src/components/GmailReplyComposer.tsx` |
| 6c | Slack | `app/src/pages/Slack.tsx` |
| 7 | Agents page (Active/Recent/Templates) | `app/src/pages/Agents.tsx` |
| 7 | ADHD mode | `app/src/components/AdhdCheckin.tsx` |
| 7 | WelcomeBack on return | `app/src/components/WelcomeBack.tsx` |
| 8 | Specs + AC checkboxes | `app/src/pages/Specs.tsx` |
| 8 | GitHub page | `app/src/pages/GitHub.tsx` |
| 9 | Adventure templates | `app/src/pages/Dashboard.tsx`, `app/src/lib/adventures.ts` |
| 9 | Backlog fallback | `app/src/pages/Backlog.tsx` |
| FR | Arcade / Break Room | `app/src/pages/BreakRoom.tsx` |

**⚠ UNVERIFIED beats (confirm before demoing):**

1. **Beat 8 — PR footer text.** "All code in this PR was written by Claude. Tori managed and directed but did not write any code." is a practice, not a hardcoded UI string. Verify you actually write this in your PR descriptions before citing it live.
2. **Beat 3 — "Fox and Mama's Best Days" as the Lego app name.** The tag `lego-app` is in the codebase (`api/routers/tasks.py`), but the name "Fox and Mama's Best Days" comes from memory, not a file. Confirm the app is still called this before using the name on stage.
