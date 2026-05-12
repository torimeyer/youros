/**
 * myOS Gemini Capture — content script
 * Watches the Gemini chat DOM for new turns and forwards them to the background service worker.
 * Only emits when captureEnabled is true in chrome.storage.local.
 */

let captureEnabled = false;

chrome.storage.local.get(['captureEnabled'], (result) => {
  captureEnabled = !!result.captureEnabled;
});

chrome.storage.onChanged.addListener((changes) => {
  if (changes.captureEnabled !== undefined) {
    captureEnabled = !!changes.captureEnabled.newValue;
  }
});

function getConversationId() {
  // Gemini URLs look like /app/abc123 or have a conversation param.
  const match = window.location.pathname.match(/\/app\/([^/?#]+)/);
  if (match) return match[1];
  const params = new URLSearchParams(window.location.search);
  return params.get('id') || window.location.pathname.replace(/\//g, '-').replace(/^-/, '') || 'unknown';
}

function getGemName() {
  // Gemini shows the active gem/persona name in the page title or a header element.
  const header = document.querySelector('[data-testid="gem-title"], .gem-title, h1[class*="title"]');
  if (header) return header.textContent.trim() || null;
  const title = document.title;
  if (title && !title.toLowerCase().includes('gemini')) return title.trim();
  return null;
}

function extractTurns() {
  const turns = [];
  // User messages
  document.querySelectorAll('[data-testid="user-message"], .user-query-text, [class*="user-message"]').forEach((el, i) => {
    turns.push({ role: 'user', text: el.textContent.trim(), index: i });
  });
  // Model responses
  document.querySelectorAll('[data-testid="model-response"], .model-response-text, [class*="model-response"], [class*="response-container"] [class*="markdown"]').forEach((el, i) => {
    turns.push({ role: 'model', text: el.textContent.trim(), index: i });
  });
  return turns;
}

let lastSentKey = '';

function captureCurrentTurns() {
  if (!captureEnabled) return;

  const conversationId = getConversationId();
  const gemName = getGemName();
  const turns = extractTurns();
  if (!turns.length) return;

  const key = JSON.stringify(turns.map(t => ({ role: t.role, len: t.text.length })));
  if (key === lastSentKey) return;
  lastSentKey = key;

  const timestamp = new Date().toISOString();
  turns.forEach((turn, globalIdx) => {
    chrome.runtime.sendMessage({
      type: 'capture-turn',
      payload: {
        conversation_id: conversationId,
        role: turn.role,
        text: turn.text,
        timestamp,
        turn_index: globalIdx,
        gem_name: gemName,
      },
    });
  });
}

// Watch for DOM mutations (new turns appended as user sends / model streams)
const observer = new MutationObserver(() => {
  captureCurrentTurns();
});

observer.observe(document.body, { childList: true, subtree: true });

// Also capture on navigation (Gemini is a SPA)
let lastHref = window.location.href;
setInterval(() => {
  if (window.location.href !== lastHref) {
    lastHref = window.location.href;
    lastSentKey = '';
    setTimeout(captureCurrentTurns, 1000);
  }
}, 500);
