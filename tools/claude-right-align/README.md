# Claude Right-Align User Messages

Right-aligns your messages in [claude.ai](https://claude.ai) so they appear on the right side of the screen, like iMessage or WhatsApp. Claude's responses stay left-aligned.

See `mockup/index.html` for a before/after visual comparison.

---

## Option A: Userscript (Tampermonkey / Greasemonkey)

**Best for:** quick setup with no developer-mode browser changes needed.

### Install

1. Install [Tampermonkey](https://www.tampermonkey.net/) for your browser.
2. Open Tampermonkey > Dashboard > click the **+** tab.
3. Delete the default script text, then paste the full contents of `userscript/claude-right-align.user.js`.
4. Press **Ctrl+S** (or Cmd+S on Mac) to save.
5. Open [claude.ai](https://claude.ai) and start a conversation. Your messages will appear on the right.

---

## Option B: Browser Extension (Chrome / Edge / Firefox)

**Best for:** permanent installation that survives browser restarts without Tampermonkey.

### Chrome or Edge

1. Open `chrome://extensions` (or `edge://extensions`).
2. Turn on **Developer mode** (toggle in the top-right corner).
3. Click **Load unpacked**.
4. Select the `extension/` folder inside this directory.
5. Done. Open [claude.ai](https://claude.ai) and your messages will appear right-aligned.

### Firefox

1. Open `about:debugging#/runtime/this-firefox`.
2. Click **Load Temporary Add-on**.
3. Navigate to the `extension/` folder and select `manifest.json`.
4. Done. Note: in Firefox, temporary add-ons are removed when the browser closes. For permanent installation you need to package and sign the extension.

---

## How it works

The extension injects a small CSS file and a JavaScript content script into every claude.ai page.

**CSS:** targets known HTML selectors for user message containers and applies `justify-content: flex-end`, pushing bubbles to the right while capping their width at 72% so they do not stretch edge to edge.

**JavaScript:** adds a DOM observer that watches for new messages (including streaming replies) and applies a fallback marker attribute (`data-claude-right-align`) to any user turns it detects by structure, in case the CSS selectors do not match after a UI update.

### Selectors used

| Selector | Why |
|---|---|
| `[data-testid="human-turn"]` | Most stable, uses data attributes |
| `[data-testid="user-message"]` | Alternative test ID |
| `.human-turn` | Semantic class name |
| `[class*="HumanMessage"]` | React component name pattern |
| `[class*="userMessage"]` | Alternative naming pattern |
| `[data-claude-right-align="true"]` | JS-applied fallback marker |

### Updating selectors after a UI change

If claude.ai updates its HTML and the extension stops working:

1. Open claude.ai and right-click on one of your messages.
2. Select **Inspect** to open DevTools.
3. Look at the element's class names or `data-` attributes.
4. Add the new selector to `content.css` and `content.js` (in the `USER_TURN_SELECTORS` array).
5. Reload the extension.

---

## Files

```
claude-right-align/
  extension/
    manifest.json       Chrome/Firefox extension config
    content.js          DOM observer and structural fallback
    content.css         CSS rules for right-alignment
    icons/              Extension toolbar icons
  userscript/
    claude-right-align.user.js   Tampermonkey/Greasemonkey script
  mockup/
    index.html          Before/after visual comparison (open in any browser)
  README.md
```
