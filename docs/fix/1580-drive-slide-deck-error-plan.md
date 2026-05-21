# →1580 Drive slide deck preview error — fix plan

## Root cause

Three bugs working together:

1. **`is_authenticated()` only checks file existence** — doesn't validate the token is live.
2. **`_refresh_if_needed()` swallows refresh failures** — returns the old expired access token silently. The token file stays on disk, so `is_authenticated()` keeps returning True.
3. **`drive_file_structured_preview` raises 500 on any `_get_file_meta` failure** — `invalid_grant` (expired refresh token) becomes a 500, which QuickLook shows as "Could not load preview (500)".
4. **Empty slides fallback shows nothing** — when `_fetch_slides_thumbnails` fails and no Drive thumbnail is available, `sample.slides` is `[]`. QuickLook renders "Slide 1 of 0" with nav buttons but no content and no fallback link.

## Verified by

```
curl https://127.0.0.1:8000/api/drive/preview/11sm6Lbe8WEZES_3hRIu0DqUZQW--r3cGje3z99urd2c
# → {"detail":"Could not get file info from Drive: ('invalid_grant: Token has been expired or revoked.', ...)"}
```

## Fixes

### 1. Backend — `api/routers/drive.py`
In `drive_file_structured_preview`, detect auth errors and return 401 instead of 500. This lets the frontend distinguish "reconnect" from "server error".

```python
except Exception as exc:
    exc_str = str(exc).lower()
    if "invalid_grant" in exc_str or "token has been expired" in exc_str or "revoked" in exc_str:
        raise HTTPException(status_code=401, detail="Your Google connection expired. Please reconnect from the Drive page.")
    raise HTTPException(status_code=500, detail=f"Could not get file info from Drive: {exc}")
```

### 2. Frontend — `app/src/components/QuickLook.tsx`
- Add `webViewLink?: string` prop.
- When `driveError` is set, show message + "Open in Google Drive" link (using the prop).
- When slides kind has `slides.length === 0`, show "Preview not available" + "Open in Google Slides" link instead of blank view.

### 3. Frontend — `app/src/pages/Drive.tsx`
Pass `webViewLink={previewFile.webViewLink}` to `<QuickLook>` so the fallback link is available even before preview data loads.

## Files changed
- [ ] `api/routers/drive.py` — 401 for auth errors in structured preview
- [ ] `app/src/components/QuickLook.tsx` — fallback UI for error + empty slides
- [ ] `app/src/pages/Drive.tsx` — pass webViewLink to QuickLook
- [ ] `api/tests/test_drive.py` — test for 401 on auth error
