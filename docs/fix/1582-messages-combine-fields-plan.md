# →1582: Messages — Combine search and send recipient fields

## Problem
Two affordances do the same lookup in the Messages (People) page:
1. Top "Search by name or number" field (`people-search-input`)
2. "Send a message" recipient picker (`contact-picker-input` via `ContactPicker`)

Users shouldn't have to figure out which field to use.

## Goal
One field. The ContactPicker in "Send a message" absorbs the top search behavior, then the top field is removed.

## Approach

### ContactPicker enhancements
- Accept optional `contacts: Contact[]` and `conversations: Conversation[]` props
- Accept optional `onSelectConversation?: (chatId: number) => void` callback
- On input change, run the same local filter logic as `peopleResults` (name/phone/email fuzzy match)
- Merge local results with existing `/imessage/contacts/search` API results in the dropdown
- Show local contact rows (`data-testid="contact-picker-contact-row"`)
- Show local conversation rows (`data-testid="contact-picker-convo-row"`)
- On conversation select: call `onSelectConversation(chatId)` and clear input
- On local contact select: if conversation match found → open conversation; otherwise pre-fill identifier

### IMessage component changes
- Move `normalizePhone` to module scope so ContactPicker can use it
- Remove `peopleSearch` / `setPeopleSearch` state
- Remove `peopleResults` useMemo
- Remove `handleSelectContact`
- Remove top search JSX block + results card JSX block
- Pass `contacts`, `conversations`, `onSelectConversation` to ContactPicker

### Test changes
- Rename test IDs: `people-search-input` → `contact-picker-input`
- Rename: `people-search-contact-row` → `contact-picker-contact-row`
- Rename: `people-search-convo-row` → `contact-picker-convo-row`
- Rewrite test helpers to fire events on `contact-picker-input`

## Acceptance criteria
- [ ] Typing a name in the send-message field shows contact + conversation suggestions
- [ ] Typing a phone number shows matching conversation suggestions
- [ ] Selecting a conversation opens it in the thread view
- [ ] Selecting a contact with no conversation pre-fills the identifier
- [ ] Top search field is gone
- [ ] Vitest passes for IMessage.test.tsx
