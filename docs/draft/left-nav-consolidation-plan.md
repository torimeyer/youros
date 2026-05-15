# Left Nav Consolidation Plan

**Status:** Draft — for Tori review, no implementation yet.
**Related:** →1369, →1363

---

## The problem

At 100% zoom on a laptop (~900px tall), the left nav overflows. The bottom of the Comms group (iMessage, Contacts) sits below the fold, and the fixed bottom rail (Tour, Activity, Usage, Rules, Settings, etc.) competes for the same limited space.

Rough height budget at 100% zoom with both groups open:
- Header + logo: ~80px
- Home / Tasks / Agents: ~135px
- FILES & DOCS header + 5 items: ~235px
- Comms header + 8 items: ~355px
- Bottom rail (8 items + toggle + status): ~350px
- **Total: ~1155px** vs ~900px available

Even with one group open and one closed, it barely fits. Bottom rail alone is ~350px.

---

## Options (ordered: highest impact first)

### Option A — Collapse groups by default *(zero code, immediate fix)*

Both FILES & DOCS and Comms start collapsed. Users click the chevron to expand.

- **Saves:** ~480px immediately
- **Pro:** No UI change. Groups already support collapse. Active route auto-expands the right group.
- **Con:** Every session starts with two extra clicks to reach any grouped item. May feel like hiding things.
- **Verdict:** Highest leverage with lowest risk. Good as a baseline regardless of other choices.

---

### Option B — Trim the bottom rail (move Tour, Activity, Rules into Settings)

Bottom rail currently has 8 utility entries. Proposal: collapse 4 into Settings.

| Entry | Where it goes | Rationale |
|---|---|---|
| Tour | Settings → "Help & Tour" tab or button | It's onboarding; one-time use; no need for permanent nav slot |
| Activity | Settings → "Activity" tab | Dev-facing; occasional lookup, not daily |
| Usage | **Keep in bottom rail** | Tori checks this regularly; 1 click is right |
| Rules | **Remove from bottom rail** (already lives at /settings/rules, Settings links there) | Duplicate |

Result: bottom rail goes from 8 items to 5 (What's New, Activity removed, Tour removed, Rules removed, Usage kept, Settings kept + theme + status).

- **Saves:** ~90–120px
- **Pro:** Settings becomes a real hub. Bottom rail becomes a tighter set of daily-use items.
- **Con:** Tour and Activity now need a new home inside Settings (a tab strip or a "More" section). 2 clicks instead of 1 to reach Activity.
- **Verdict:** Worth it for Tour and Rules. Activity is borderline — keep if she checks it often.

---

### Option C — Merge iMessage + Contacts (→1363, already planned)

Combine into a single "People" nav entry at `/people` or just keep `/contacts` and integrate iMessage into it.

- **Saves:** 1 nav slot in Comms (~40px)
- **Pro:** Makes conceptual sense — both are personal contacts. Already tracked in →1363.
- **Con:** Requires a new combined page or meaningful integration work. Not a one-liner.
- **Verdict:** Do it, but as a follow-on once →1363 is scoped. Not a blocker for the scroll fix.

---

### Option D — Split Comms into smaller sub-groups *(instead of one 8-item list)*

Currently Comms has 8 very different things jammed together. Possible split:

- **Google** (Gmail, Calendar) — already grouped in people's heads
- **Work** (Slack, GitHub, Jira, Confluence)
- **Personal** (iMessage, Contacts — or one merged "People" after →1363)

This doesn't save vertical space (still 8 items total), but it makes each sub-group smaller and more likely to be collapsed when not in use.

- **Pro:** Better mental model, smaller groups feel easier to collapse.
- **Con:** Three group headers instead of one = 3 × ~35px overhead = actually adds ~70px. Only net wins if sub-groups are left collapsed more often.
- **Verdict:** Nice to have after the main fixes, not the primary fix.

---

## Recommended approach (pick your wave)

**Minimum viable fix (can ship this week):**
- [ ] Option A: collapse both groups by default

**Good fix (makes it clean):**
- [ ] Option A + Option B: collapse by default, trim bottom rail (move Tour to Settings, remove Rules duplicate)

**Full fix (clean + logical):**
- [ ] Option A + Option B + Option C: all of the above, plus merge iMessage/Contacts after →1363 is scoped

---

## What this leaves alone

- Home / Tasks / Agents — these are the daily workhorses, keep them pinned at top
- What's New — keep in bottom rail (feature engagement)
- Usage — keep in bottom rail (frequent check)
- Settings — keep in bottom rail
- The collapsible group mechanism itself — it already works well, just needs better defaults
