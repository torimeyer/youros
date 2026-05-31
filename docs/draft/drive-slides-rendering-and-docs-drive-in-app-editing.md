| Presentations are exportable to PDF | `api/routers/drive.py:57-63` — `_EXPORTABLE_MIME` includes `vnd.google-apps.presentation` |

## DECISION (locked 2026-05-31, supersedes NEEDS CLARIFICATION above)

- **NC-1 (Slides reauth UX):** Show the reconnect prompt **inside the Slides preview panel only**, not as a page-top banner. Users who haven't reauthorized still get the PDF-export fallback so slides are visible without forced reauth.
- **NC-2 (edit scope):** **Docs only** for v1. The Edit button appears only for `kind === "doc"`. Sheets, Slides, PDFs, images, folders show no Edit button. (Editing arbitrary Drive files is out of scope.)
- **NC-3 (edit format):** **Plain-text round-trip** for v1. Seed the textarea from the plain-text export; saving replaces Doc body text. Show a small caption that rich formatting (headings, tables) is not preserved on save. A rich editor is deferred to a later version.
