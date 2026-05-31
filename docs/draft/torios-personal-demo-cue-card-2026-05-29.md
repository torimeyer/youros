# ToriOS Personal Demo Cue Card (5-minute cut)

*Yesterday's 5-minute, outcome-first cut, recovered from the 2026-05-30 session and re-applied 2026-05-31. The earlier 10-step, ~12-15 min version is in git history if you ever need it.*

**Target: ~4:30 spoken.** One golden rule, because last time ran 24 minutes:

> **Outcome on stage. Mechanism only in answers.** Never show an agent file, a spec, or a terminal. Show the app and what it produced. Read the lines as written and stop where it says stop.

**The spine of the demo:** I go from one line to a finished feature, task to spec to build, and it just works. Beats 3 and 4 fill the time while that build runs.

**Pre-flight (before anyone's watching):** yourOS running (8000 + 3010) · `touch ~/.myos/.demo_mode` · logged in, your name shows, dark mode · iMessage authorized + Calendar connected · one throwaway task in the backlog · phone silent.

---

### BEAT 1: Open · ~0:30
*[Onboarding screen showing.]*
> "This is your personal operating system, tailored to what you care about most. I'll show you mine, toriOS. But I built it to be customized, so really, this is *your* OS."

*[Click in. Stop talking until Tasks loads.]*

### BEAT 2: Give it a real job, then walk away · ~0:45
*Kicks off the background build that lands in Beat 5. Don't open the spec. Don't say "acceptance criteria."*
*[Tasks page. Pick a throwaway task.]*
> "This is my backlog. Everything on my mind, in one place. Watch what happens when I actually start one."

*[Click **Create Spec**, then **Build**.]*
> "I gave it one line. Now it goes off and does the whole thing, and I don't have to babysit it. Let's leave it running and do some real-life stuff while it works."

*[Walk away. Go to chat. Don't narrate the build.]*

### BEAT 3: Text a real person · ~1:00
*[Chat open.]*
> "I can just talk to it like a person. Watch. 'Text Scott that I'm running five minutes late.'"

*[Type it. Send. Let it go.]*
> "That went to my real messages. It didn't just tell me to do it, it did it."

*[Stop. Let it land.]*

### BEAT 4: Put something on the calendar · ~1:00
> "Same thing with my week. 'Add coffee with Scott Friday at 10.'"

*[Type it. Send. Let the event appear.]*
> "On my real calendar. One sentence, and it's handled."

*[Stop. Let it sit.]*

### BEAT 5: The job lands, then close · ~1:15
*The Beat 2 build completes. This is the payoff.*
*[Go back to the task. Show the finished result.]*
> "Remember the one line I typed a few minutes ago? While we were doing other things, it finished. That's the whole point. I describe what I want, and it comes back done."

*[Pause.]*
> "And this is just mine. Yours wouldn't look like this at all. You'd point it at whatever *you* care about. That's why it's your OS."

*[Stop. End.]*

---

### If a live beat misbehaves (one line, move on, don't debug on stage)
- **Build not done by Beat 5:** "It's still working in the background, which is exactly the point." Show an earlier result or skip to the close.
- **iMessage fails:** "When I'm not on a projector that lands in my real messages."
- **Calendar slow:** "That's landing on my real calendar."
- **Dead air, the one recovery line:** "While that runs, here's what's happening: it's doing the boring parts so I don't have to." Then wait. No second sentence.

### Q&A bank (only if asked, this is where mechanism lives)
- **"How does it actually do that?"** -> "It breaks the job into pieces, runs a few small workers at once, and they hand the finished result back. I keep all of that out of the way so I only see what I asked for."
- **"Is this just ChatGPT?"** -> "No. A chatbot answers you. This goes and does the work, on my real apps, my messages, my calendar, my codebase."
- **"What if it gets it wrong?"** -> "I review before anything lands. It drafts, I approve. And every step is logged, so I can always see what it did."
- **"Could I have this?"** -> "Yes, that's the whole idea. You'd point it at what you care about. Yours wouldn't look like mine."
- **"What were those 'workers'?"** *(technical asker)* -> "Coordinated agents. The value isn't that it calls an AI, it's that it runs a team of them and keeps the work straight across sessions."

### Cuts if you're over time
- Drop Beat 4 (calendar) first. Beats 1, 2, 3, 5 are the spine.
- Never cut Beat 5. The job landing is the whole demo.
