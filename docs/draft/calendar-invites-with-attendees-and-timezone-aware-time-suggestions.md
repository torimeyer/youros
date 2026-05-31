# Calendar invites with attendees and timezone-aware time suggestions

**Status:** draft  
**Tasks:** →1941, →1994  
**Date:** 2026-05-31

---

## Problem

When the user asks to schedule a meeting, the calendar integration today only creates events for themselves. There is no way to add attendees, and no help picking a time that actually works for everyone. The user ends up manually checking each person's calendar in a separate tab before picking a time. This is the friction that makes scheduling feel slow even when the calendar is connected.

---

## Goals

1. Let the user add attendees to any calendar event they create, either from the Calendar page or through chat.
2. Suggest times that work for everyone by checking when each person is free.
3. If the user has a Google Workspace account, offer autocomplete for attendee names from the organization directory when they start typing a name or email.
4. Show each suggested time slot in the user's local time zone and flag when attendees are in different time zones.

---

## Non-goals

- Handling RSVP responses or tracking who accepted.
- Reading other people's event details (only free/busy status is needed).
- Sending calendar invites from outside Google Calendar (e.g., iCal format, email fallback).
- Support for room or resource booking.
- Recurring event scheduling in the initial version.

---

## Acceptance Criteria

### Attendee input
- [ ] When creating an event from the Calendar page, the user can type one or more email addresses into an attendees field and have them added to the event.
- [ ] As the user types, matching names and emails from their Google Workspace directory (if available) appear as suggestions below the input.
- [ ] Each attendee shows as a chip with their name or email and an X to remove them before saving.
- [ ] The attendees list is optional. Creating an event with no attendees works exactly as before.

### Time suggestions
- [ ] After the user picks a date range and adds at least one attendee, the app queries when everyone including the user is free and shows up to 5 suggested time slots that are free for everyone.
- [ ] Each suggestion shows the day, start time, end time, and how many calendar conflicts it avoids.
- [ ] If no fully free slot exists in the chosen range, the app shows the best available options and labels them "some conflicts" with a count of who is busy.
- [ ] The user can tap any suggestion to prefill the start and end fields.

### Time zone awareness
- [ ] Time slots are shown in the user's local time zone (read from the browser or from the connected Google account's calendar time zone).
- [ ] If any attendee's calendar is in a different time zone, the suggestion tooltip shows what time that is for them (e.g., "3pm your time, 8pm for london@example.com").
- [ ] If the user types a start time in a different offset (e.g., an ISO string with a UTC offset), the app converts it to local display time before showing it.

### Event creation with attendees
- [ ] When the user confirms an event, it is sent to Google Calendar with the attendees list, which triggers an email invite from Google to each attendee.
- [ ] The response from Google includes a `htmlLink` that is shown to the user so they can open the event directly.
- [ ] If creating the event fails because the calendar scope is missing, the user sees a plain-language message asking them to reconnect their account.

### Chat path
- [ ] When the user says something like "schedule a meeting with alice@example.com and bob@example.com Tuesday afternoon", the AI uses the attendees in the calendar tool call so they are included in the created event.
- [ ] The AI can ask for a time range ("should I look for a slot Tuesday or Wednesday?") before querying free/busy if the user has not specified a time.

---

## How this uses the existing calendar stack

The app already has a full Google Calendar integration via direct `googleapiclient` calls.

**What already exists (reuse):**

| File | What it does |
|------|-------------|
| `api/routers/calendar.py` (gen=6) | `POST /calendar/events` endpoint with `CreateEventBody` |
| `api/services/calendar.py` (gen=3) | `create_event(summary, start, end, all_day, description, location)` calls Google API directly |
| `api/services/google_auth.py` (gen=9, line 53) | `https://www.googleapis.com/auth/calendar` scope is already in `SCOPES` |
| `api/services/tool_executor.py` (gen=4, line 359) | `create_calendar_event` AI tool; routes through `calendar_service.create_event()` |

**What needs to be added:**

| Gap | Where | What to change |
|-----|-------|---------------|
| Attendees on event creation | `api/routers/calendar.py:28` `CreateEventBody` | Add `attendees: list[str] = []` field |
| Attendees passed to Google | `api/services/calendar.py:154` `create_event()` | Add `attendees` param; inject `body["attendees"] = [{"email": e} for e in attendees]` |
| AI tool updated | `api/services/tool_executor.py:359` `create_calendar_event` schema | Add `attendees` array property and pass to `cal_service.create_event()` |
| Free/busy query | new `api/services/calendar.py` function | Call Google Calendar API `freebusy.query` with user + attendee calendars and time window; no new scope needed (`auth/calendar` already covers it) |
| Free/busy endpoint | `api/routers/calendar.py` | New `POST /calendar/freebusy` that accepts `attendees`, `time_min`, `time_max`, returns busy slots per person |
| Directory lookup | new service (see NEEDS CLARIFICATION) | Call Google People API `people.searchContacts` or Directory API; **requires a new scope** (`contacts.readonly` or admin directory scope) |

---

## NEEDS CLARIFICATION

**1. Which UI surface hosts the invite/attendee UI?**

Option A: Extend the existing Calendar page form (the one that opens when the user clicks "New event").  
Option B: Build attendee entry into the AI chat flow only (no new UI component on the Calendar page).  
Option C: Both.  

The Calendar page already has a form that posts to `POST /calendar/events`. Extending it is straightforward. The chat path is also needed for the natural-language use case.

**2. Directory source for attendee suggestions**

Option A: Google People API (`people.searchContacts`, scope: `contacts.readonly`). This searches the user's own contacts, not their org directory.  
Option B: Google Workspace Admin SDK Directory API (`admin/directory/v1`). This is the org-wide directory. Requires a different OAuth scope and may need admin approval depending on the Workspace plan.  
Option C: Skip directory autocomplete in v1. The user types full email addresses. Autocomplete can be added later.  

The current `SCOPES` list in `api/services/google_auth.py:47` does not include either People or Directory scopes. Adding one requires a re-auth (scope upgrade).

**3. Conflict ranking rule when no fully free slot exists**

When every slot in the chosen range has at least one conflict, what gets surfaced? Options:  
- Rank by fewest conflicts (fewest people busy)  
- Rank by most important attendee free (requires knowing who is "most important")  
- Just show the first 5 slots with conflict counts and let the user decide  

The "fewest conflicts" option is easiest to implement and does not require any notion of seniority.

**4. Time zone of attendees**

The app reads the user's time zone from the browser or from the event's `timeZone` field in Google Calendar (`api/services/calendar.py`, line ~240). Attendees' time zones are not available without querying their calendar settings, which Google does not allow for other users. Options:  
- Infer from freebusy data (not reliable)  
- Show UTC offset from the event time zone of the attendee's calendar (requires reading their calendar metadata, which the `auth/calendar` scope allows for invited users but not arbitrary emails)  
- Skip per-attendee time zone display in v1  

---

## Verified against the codebase

- `api/routers/calendar.py:28` confirms `CreateEventBody` has no `attendees` field today.
- `api/services/calendar.py:154-220` confirms `create_event()` builds the Google API body with no `attendees` key.
- `api/services/google_auth.py:47-57` confirms current `SCOPES`: calendar full scope is present; People API and Directory API scopes are absent.
- `api/services/tool_executor.py:359-394` confirms the AI tool `create_calendar_event` has no `attendees` property in its `input_schema`.
- No existing `freebusy` or `suggest_time` function exists anywhere in `api/services/` or `api/routers/` (confirmed by grep returning 0 matches).
- No existing directory or contacts service exists (grep for `people`, `directory`, `contacts` in `api/services/google_auth.py` returned scope lines only, no API calls).
- `api/services/meeting_prep.py:174-176` reads `attendees` from existing Google Calendar events but does not write them, confirming the data model is understood but not yet written on event creation.
