# myOS Gemini Capture

A Chrome extension that captures your [gemini.google.com](https://gemini.google.com) conversations and sends them to your local myOS instance.

## Loading the extension in Chrome

1. Open Chrome and go to `chrome://extensions`
2. Toggle **Developer mode** on (top-right)
3. Click **Load unpacked**
4. Select the `extension/` folder from this repo

The extension icon should appear in your toolbar.

## Setup

1. **Get your auth token**
   Open a terminal and run:
   ```
   cat ~/.myos/extension_token
   ```
   If the file doesn't exist, start the myOS backend once and it'll be created automatically.

2. **Configure the extension**
   Click the extension icon, then click **Settings**.
   - **Backend URL**: leave as `https://127.0.0.1:8000` (default)
   - **Auth token**: paste the value from step 1
   - Click **Save**

3. **Enable capture**
   Click the extension icon and toggle **Capture conversations** ON.

## How it works

- The extension watches the Gemini page for new conversation turns.
- When a turn completes (user submit or model stream done), it's sent to `POST /api/gemini-capture` on your local myOS backend.
- Captured conversations appear in the **Gemini Captures** section on the My Gems page in myOS.
- Capture is **off by default** — you control it from the popup.

## Privacy

All data stays on your machine. Nothing is sent to any remote server other than your local myOS backend running at `127.0.0.1:8000`.
