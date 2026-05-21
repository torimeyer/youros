# →1577 Messages Bugs Plan

Three independent bugs in `app/src/pages/IMessage.tsx` and `api/services/imessage.py`.

## (a) "not connected" flash before status check finishes

**Root cause**: `connectionState` state is initialized from `readConnectionCache()`. If
the last session ended with `'not_connected'`, the cached value is returned and the
ConnectCard renders immediately — before the fresh `/imessage/status` API call fires.

**Fix**: Initialize `connectionState` to `'loading'` unconditionally. The connection
cache is not reliable enough to short-circuit the status check.

File: `app/src/pages/IMessage.tsx` line ~218

## (b) Empty state shows instead of cached conversations during load

**Root cause**: When `connectionState === 'loading'`, there is an early return that
renders a full-page spinner. This blocks the cached conversations (already in state
from `readCache()`) from rendering.

**Fix**: Only show the full-page spinner when there are no cached conversations.
When `conversations.length > 0`, skip the early return so the list renders from
cache while the status check runs.

File: `app/src/pages/IMessage.tsx` line ~378 (the early return block)

## (c) Unread count is wrong — shows unreads when Messages.app shows zero

**Root cause**: SQL counts unread as `SUM(CASE WHEN m.is_read = 0 AND m.is_from_me = 0)`.
The `is_read` column in `chat.db` goes stale and is not reliably updated by Messages.app.
The Messages.app itself uses `chat.last_read_message_timestamp` (a field in the `chat`
table set to the date of the last message read) to determine badge counts.

**Verified**: Live query on chat 105 shows `computed_unread=10` vs `timestamp_unread=0`.
The Messages.app shows zero; the timestamp approach is correct.

**Fix**: Update the `unread_count` calculation in `get_conversations_sync()` to prefer
`last_read_message_timestamp` when > 0, falling back to `is_read=0` when the timestamp
is unset (chat was never opened on this Mac or timestamp was never written).

```sql
SUM(CASE
    WHEN m.is_from_me = 1 THEN 0
    WHEN c.last_read_message_timestamp > 0 AND m.date > c.last_read_message_timestamp THEN 1
    WHEN c.last_read_message_timestamp = 0 AND m.is_read = 0 THEN 1
    ELSE 0
END) as unread_count
```

File: `api/services/imessage.py` lines ~424-425
