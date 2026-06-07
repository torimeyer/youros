# →1575 Gmail empty inbox — diagnosis + fix plan

## Root cause

Token file at `~/.youros/google_token.json` has `expires_at` 3.5 days in the past.
`_refresh_if_needed` attempts a token refresh, Google returns `invalid_grant`
(refresh token revoked/expired), but the `except Exception: return tokens` swallows
the error and returns the dead token silently.

Downstream failures:
1. `_build_gmail_service` builds a `Credentials` object with the expired token.
2. The Gmail API call fails with `invalid_grant`.
3. `gmail_messages()` router maps it to a generic 500 (plain string detail).
4. Frontend `fetchMessages()` catch swallows the 500 and sets messages to [].
5. `auth/status` returns `needs_reauth: false` (only checks file existence + scope strings).
6. No ConnectCard is shown. User sees empty inbox with no explanation.

## Fixes

### 1. `api/services/google_auth.py` — `_refresh_if_needed`
- Catch `urllib.error.HTTPError` separately.
- Read the error body; if `error == "invalid_grant"`, write `"revoked": True`
  to the token file and call `_invalidate_google_status_cache()`, then raise.
- On successful refresh, pop any existing `revoked` key from the new token.

### 2. `api/routers/gmail.py` — `_compute_gmail_status`
- After the scope check, read the token file and check `tokens.get("revoked")`.
- If True, set `reauth = True`.

### 3. `api/routers/gmail.py` — `gmail_messages` exception handler
- Before the `accessnotconfigured` check, add an `invalid_grant` check.
- Return 403 with `{"needs_reauth": True, "message": "..."}` + invalidate cache.

### 4. `app/src/pages/Gmail.tsx` — `fetchMessages` catch
- When the 403 response has `detail.needs_reauth == True`, call
  `setAuthStatus(prev => {...prev, needs_reauth: true})`.
- This triggers the existing ConnectCard without any new UI code.

## Acceptance criteria
- [ ] `curl /api/gmail/auth/status` returns `needs_reauth: true` when token is revoked
- [ ] `curl /api/gmail/messages` returns 403 with `needs_reauth: true` (not 500)
- [ ] Gmail page shows ConnectCard (not empty inbox) when token is revoked
- [ ] All existing Gmail tests pass
