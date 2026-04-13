// ==UserScript==
// @name         Claude Right-Align User Messages
// @namespace    https://github.com/myos/claude-right-align
// @version      1.0.0
// @description  Right-aligns your messages in claude.ai so they appear on the right side, like in iMessage or WhatsApp.
// @author       myOS
// @match        https://claude.ai/*
// @grant        GM_addStyle
// @run-at       document-idle
// ==/UserScript==

(function () {
  'use strict';

  // ── Inject CSS ────────────────────────────────────────────────────────────

  GM_addStyle(`
    /*
     * Claude Right-Align User Messages
     * Covers multiple known selector patterns for resilience across UI updates.
     */

    /* Turn wrapper: push human turns to the right */
    [data-testid="human-turn"],
    [data-testid="user-message"],
    .human-turn,
    [class*="HumanMessage"],
    [class*="userMessage"],
    [class*="human-turn"] {
      display: flex !important;
      flex-direction: row !important;
      justify-content: flex-end !important;
      align-items: flex-start !important;
    }

    /* Bubble: cap width so it does not span the full viewport */
    [data-testid="human-turn"] > *,
    [data-testid="user-message"] > *,
    .human-turn > *,
    [class*="HumanMessage"] > *,
    [class*="userMessage"] > *,
    [class*="human-turn"] > * {
      max-width: 72% !important;
      text-align: left !important;
    }

    /* JS structural fallback marker */
    [data-claude-right-align="true"] {
      display: flex !important;
      flex-direction: row !important;
      justify-content: flex-end !important;
      align-items: flex-start !important;
    }

    [data-claude-right-align="true"] > * {
      max-width: 72% !important;
      text-align: left !important;
    }
  `);

  // ── JS structural fallback ────────────────────────────────────────────────

  const USER_TURN_SELECTORS = [
    '[data-testid="human-turn"]',
    '[data-testid="user-message"]',
    '.human-turn',
    '[class*="HumanMessage"]',
    '[class*="userMessage"]',
    '[class*="human-turn"]',
  ].join(', ');

  const MARKER = 'data-claude-right-align';

  function applyRightAlign() {
    // Named selectors
    document.querySelectorAll(USER_TURN_SELECTORS).forEach((el) => {
      if (!el.hasAttribute(MARKER)) {
        el.setAttribute(MARKER, 'true');
      }
    });

    // Structural detection: look for conversation containers and scan children
    const containers = [
      document.querySelector('main'),
      document.querySelector('[class*="conversation"]'),
      document.querySelector('[class*="Conversation"]'),
    ].filter(Boolean);

    containers.forEach((container) => {
      Array.from(container.children).forEach((turn) => {
        if (turn.hasAttribute(MARKER)) return;

        const isAssistant =
          turn.querySelector('[data-testid="assistant-turn"]') !== null ||
          turn.querySelector('[class*="AssistantMessage"]') !== null ||
          turn.querySelector('[class*="assistant-turn"]') !== null;

        const isUser =
          turn.querySelector('[data-testid="human-turn"]') !== null ||
          turn.querySelector('[class*="HumanMessage"]') !== null ||
          turn.getAttribute('data-author') === 'human';

        if (isUser && !isAssistant) {
          turn.setAttribute(MARKER, 'true');
        }
      });
    });
  }

  applyRightAlign();

  // Watch for new messages (streaming responses, new conversations, etc.)
  const observer = new MutationObserver((mutations) => {
    for (const m of mutations) {
      if (m.addedNodes.length > 0) {
        applyRightAlign();
        break;
      }
    }
  });

  observer.observe(document.body, { childList: true, subtree: true });
})();
