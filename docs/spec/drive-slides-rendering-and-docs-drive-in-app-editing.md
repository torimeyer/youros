---
promoted_at: 2026-05-31T05:53:58Z
status: spec
---
# Drive Slides rendering and Docs/Drive in-app editing

Covers →1938 (Slides render) and →1939 (Docs/Drive editing).

---

## Problem

**→1938 — Slides files show "Slide preview is not available"**

When you click a Google Slides file in the Drive page, the preview panel shows
"Slide preview is not available" instead of the actual slides. Every other file
type (Docs, Sheets, images, PDFs) renders. Slides don't.

**→1939 — Drive and Docs files are read-only in yourOS**

You can read a Google Doc in the preview panel, but you cannot make changes. There
is no edit button. Saving anything back to Drive requires leaving yourOS and going
to drive.google.com. Docs and Drive files that yourOS creates should be editable
from the same panel where you read them.

---

## Goals

1. Slides files render slide-by-slide inside the Drive preview panel (no new tab required).
2. Google Docs can be edited inline from the yourOS Drive page and saved back to Drive.
3. The edit experience is clear about what will be saved and where.

---

## Non-goals

- Editing Google Sheets or Slides content inside yourOS (Sheets and Slides are view-only for now).
- Full rich-text formatting toolbar (bold, tables, images) inside the inline editor.
- Offline editing or local draft state.
- Any changes to the auth flow for users who have never connected Google Drive.

---

## Acceptance Criteria

### →1938 — Slides rendering

- [ ] Clicking a Google Slides file in the Drive page opens the preview panel and shows slide thumbnails, not "Slide preview is not available."
- [ ] The panel shows a slide counter ("Slide 2 of 12") and previous/next buttons to step through slides.
- [ ] When the Slides thumbnail API fails, the preview falls back to a PDF export of the presentation and shows it in the existing PDF viewer (rather than showing nothing).
- [ ] Users who need to reauthorize to get the Slides scope see a clear message in the panel ("Reconnect your Google account to enable slide previews") with a reconnect button — not a silent blank state.
- [ ] The fix does not break Docs, Sheets, or PDF previews.

### →1939 — Docs/Drive editing

- [ ] The Docs preview panel shows an "Edit" button (pencil icon) next to the file name.
- [ ] Clicking "Edit" switches the panel to an editable text area pre-filled with the current Doc content.
- [ ] The edit view shows a "Save to Drive" button and a "Cancel" button.
- [ ] Clicking "Save to Drive" sends the updated content to Drive and shows a brief confirmation ("Saved") in the panel.
- [ ] Clicking "Cancel" discards changes and returns to the read view without prompting if nothing was changed; prompts "Discard changes?" if the user typed anything.
- [ ] If saving fails (network error, expired token), the panel shows the error message and keeps the edit view open so the user does not lose their work.
- [ ] The edit button is not shown for file types that cannot be edited in yourOS (Sheets, Slides, PDFs, images, folders).
- [ ] The fix does not break the existing read view for Docs.

---

## Technical approach

### →1938 — Root cause and fix

**Root cause (verified in code):**

The Slides thumbnail fetch uses the Google Slides API (`build("slides", "v1", ...)` at
`api/routers/drive.py:1262`). That API requires the
`https://www.googleapis.com/auth/presentations.readonly` OAuth scope. The current
SCOPES list in `api/services/google_auth.py:47-57` does not include this scope. The
Slides API call therefore fails with a 403. The fallback (`drive.py:1467-1473`) tries
to use the Drive `thumbnailLink` field from file metadata. For presentations, this
field is often absent or returns a URL that may not load without Google auth cookies,
so the fallback also fails and `sample.slides` ends up empty. The frontend
(`app/src/components/QuickLook.tsx:548-563`) renders "Slide preview is not available"
when `slides.length === 0`.

**Fix — two-part:**

1. **Add the Presentations scope** to `api/services/google_auth.py:47`:
   ```
   "https://www.googleapis.com/auth/presentations.readonly",
   ```
   Users with an existing token that lacks this scope will see a reauth prompt
   (the existing `needs_reauth` logic at `drive.py:142` already handles this).

2. **Add a PDF fallback path** for users who have not yet reauthorized: when
   `sample.slides` is empty and there IS an `export_url`, the frontend should
   render the presentation as a PDF iframe (the Drive API can always export a
   presentation to PDF using the existing `_EXPORTABLE_MIME` path at
   `drive.py:57-63`). This gives immediate value without forcing reauth.

**Files to touch:**
- `api/services/google_auth.py` — add presentations.readonly to SCOPES
- `app/src/components/QuickLook.tsx` — add PDF fallback path when `slides.length === 0`
- No changes needed to `drive.py` (the fallback logic is already correct once the scope is present)

### →1939 — Root cause and fix

**Root cause (verified in code):**

The backend already has write scope (`auth/drive` at `google_auth.py:50`). There are
existing write endpoints: `POST /drive/docs/replace-from-md` (replaces Doc content
from a local .md file, `drive.py:1591`) and `POST /drive/docs/batch-update`
(`drive.py:1638`, forwards Docs API batchUpdate). Neither is wired to any UI.

The Doc preview in `QuickLook.tsx:604-650` is read-only HTML with no edit affordance.

**Fix:**

1. **New backend endpoint:** `POST /drive/docs/{doc_id}/update-text` — accepts plain
   text and writes it back as Google Docs content via `batchUpdate` (delete all
   content, insert new text). Simpler and safer than exposing raw batchUpdate to
   the frontend.

2. **Frontend: edit mode in QuickLook for Docs.** When `kind === "doc"`:
   - Show a pencil icon button in the header.
   - Clicking it replaces the read-only HTML block with a `<textarea>` seeded with
     the plain-text content (call `GET /drive/docs/{doc_id}` or derive from the
     existing `sample` blocks).
   - A "Save to Drive" button calls `POST /drive/docs/{doc_id}/update-text`.
   - A "Cancel" button restores the read view.
   - Show a success/error message inline; do not close the panel.

**Files to touch:**
- `api/routers/drive.py` — add `POST /drive/docs/{doc_id}/update-text` endpoint
- `app/src/components/QuickLook.tsx` — add edit mode for `kind === "doc"`

---

## NEEDS CLARIFICATION

**[NC-1] Reauth flow for →1938**
Adding `presentations.readonly` to SCOPES means any existing user token that was
issued without that scope will have `needs_reauth = True` at `drive.py:142`. This
triggers the "reconnect" banner on the Drive page. Is that acceptable, or do we need
a gentler upgrade path (e.g., only show the reconnect banner inside the Slides panel
rather than at the top of the page)?

**[NC-2] Edit scope for →1939**
"Drive and Docs files editable" — does this mean:
  (a) Docs only (the inline editor approach above), or
  (b) Any Drive file yourOS created (not just Docs), or
  (c) All Drive files including ones created outside yourOS?
Option (a) is safest and matches the existing backend. Options (b)/(c) require a
different approach (download, edit locally, re-upload) and are significantly more
complex.

**[NC-3] Edit format for →1939**
The Doc content comes back from the backend as HTML (for rendering). For editing, we
would seed the textarea with the plain-text export (`GET` the plain-text via the
existing `_export_doc_text` path). This means headings and formatting would be lost
on save. Is plain-text round-trip acceptable, or should the edit view preserve
formatting (which would require a rich editor like TipTap)?

---

## Verified against the codebase

| Claim | Evidence |
|-------|---------|
| `presentations.readonly` scope is absent | `api/services/google_auth.py:47-57` — SCOPES list; no `presentations` entry |
| Slides API call uses `build("slides","v1")` | `api/routers/drive.py:1262` — `slides = build("slides", "v1", credentials=creds)` |
| Fallback returns empty slides on failure | `api/routers/drive.py:1467-1473` — `sample = {"slides": [], "truncated": False}` when `thumbnail_url` is None |
| Frontend renders "not available" on empty slides | `app/src/components/QuickLook.tsx:548-563` — `if (slides.length === 0)` shows fallback text |
| `isInlinePreviewable` includes presentations | `app/src/pages/Drive.tsx:114` — `mimeType === 'application/vnd.google-apps.presentation'` |
| Drive scope (write) is already in SCOPES | `api/services/google_auth.py:50` — `auth/drive` (full read/write) |
| Docs batch-update endpoint exists | `api/routers/drive.py:1638` — `POST /drive/docs/batch-update` |
| Docs replace-from-md endpoint exists | `api/routers/drive.py:1591` — `POST /drive/docs/replace-from-md` |
| No edit UI in QuickLook for Docs | `app/src/components/QuickLook.tsx:604-650` — read-only HTML block, no edit button |
| Presentations are exportable to PDF | `api/routers/drive.py:57-63` — `_EXPORTABLE_MIME` includes `vnd.google-apps.presentation` |

## DECISION (locked 2026-05-31, supersedes NEEDS CLARIFICATION above)

- **NC-1 (Slides reauth UX):** Show the reconnect prompt **inside the Slides preview panel only**, not as a page-top banner. Users who haven't reauthorized still get the PDF-export fallback so slides are visible without forced reauth.
- **NC-2 (edit scope):** **Docs only** for v1. The Edit button appears only for `kind === "doc"`. Sheets, Slides, PDFs, images, folders show no Edit button. (Editing arbitrary Drive files is out of scope.)
- **NC-3 (edit format):** **Plain-text round-trip** for v1. Seed the textarea from the plain-text export; saving replaces Doc body text. Show a small caption that rich formatting (headings, tables) is not preserved on save. A rich editor is deferred to a later version.
