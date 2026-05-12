/**
 * myOS Gemini Capture — background service worker
 * Receives capture-turn messages from content.js and POSTs them to the local myOS backend.
 */

chrome.runtime.onMessage.addListener((message) => {
  if (message.type !== 'capture-turn') return;

  chrome.storage.local.get(['authToken', 'backendUrl'], (result) => {
    const token = result.authToken || '';
    const backendUrl = (result.backendUrl || 'http://127.0.0.1:8000').replace(/\/$/, '');

    if (!token) {
      console.warn('[myOS Capture] No auth token configured. Open Options to set one.');
      return;
    }

    fetch(`${backendUrl}/api/gemini-capture`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'Authorization': `Bearer ${token}`,
      },
      body: JSON.stringify(message.payload),
    }).catch((err) => {
      console.error('[myOS Capture] Failed to send turn:', err);
    });
  });
});
