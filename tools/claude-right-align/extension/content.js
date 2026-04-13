/**
 * Claude UI: Right-align user message bubbles
 * Content script for Chrome/Firefox extension
 *
 * Strategy:
 * 1. Inject CSS immediately via the stylesheet (content.css)
 * 2. Detect message turns structurally via JS as a fallback
 * 3. Re-run on DOM mutations to catch dynamically added messages
 */

(function () {
  'use strict';

  // Known stable selectors for user turn containers.
  // These cover different versions of the claude.ai UI.
  const USER_TURN_SELECTORS = [
    '[data-testid="human-turn"]',
    '[data-testid="user-message"]',
    '.human-turn',
    '[class*="HumanMessage"]',
    '[class*="userMessage"]',
    '[class*="human-turn"]',
  ].join(', ');

  // Marker added to elements we've already processed
  const MARKER = 'data-claude-right-align';

  /**
   * Apply the right-align marker to all matching user turn elements.
   * The CSS rule [data-claude-right-align="true"] handles the visual layout.
   */
  function applyRightAlign() {
    document.querySelectorAll(USER_TURN_SELECTORS).forEach((el) => {
      if (!el.hasAttribute(MARKER)) {
        el.setAttribute(MARKER, 'true');
      }
    });

    // Structural fallback: scan the conversation for turns that look like
    // user messages based on DOM structure (avatar-free turns, etc.)
    detectByStructure();
  }

  /**
   * Structural detection fallback.
   *
   * Claude.ai renders conversations as alternating human/assistant turns.
   * Human turns typically:
   *   - Contain no code blocks or markdown headers in the outermost wrapper
   *   - Are preceded by an assistant turn
   *   - Have a visually distinct background
   *
   * This is intentionally conservative to avoid mis-tagging assistant turns.
   */
  function detectByStructure() {
    // Look for the main conversation container
    const possibleContainers = [
      document.querySelector('[data-testid="conversation-turn-0"]'),
      document.querySelector('main'),
      document.querySelector('[class*="conversation"]'),
      document.querySelector('[class*="Conversation"]'),
    ].filter(Boolean);

    if (possibleContainers.length === 0) return;

    // Find all direct children of the conversation that look like turns
    possibleContainers.forEach((container) => {
      const turns = container.children;
      for (let i = 0; i < turns.length; i++) {
        const turn = turns[i];
        if (turn.hasAttribute(MARKER)) continue;

        // Heuristic: if this turn contains the assistant icon/logo, it's Claude's.
        // If it does NOT contain the assistant icon, it may be a user turn.
        const hasAssistantMarker =
          turn.querySelector('[data-testid="assistant-turn"]') !== null ||
          turn.querySelector('[class*="AssistantMessage"]') !== null ||
          turn.querySelector('[class*="assistant-turn"]') !== null ||
          turn.querySelector('[class*="assistantMessage"]') !== null;

        const hasUserMarker =
          turn.querySelector('[data-testid="human-turn"]') !== null ||
          turn.querySelector('[class*="HumanMessage"]') !== null ||
          turn.getAttribute('data-author') === 'human';

        if (hasUserMarker && !hasAssistantMarker) {
          turn.setAttribute(MARKER, 'true');
        }
      }
    });
  }

  // Run immediately on script load (CSS handles most visible turns)
  applyRightAlign();

  // Watch for new messages being added to the DOM (live chat updates)
  const observer = new MutationObserver((mutations) => {
    let hasRelevantChanges = false;
    for (const mutation of mutations) {
      if (mutation.addedNodes.length > 0) {
        hasRelevantChanges = true;
        break;
      }
    }
    if (hasRelevantChanges) {
      applyRightAlign();
    }
  });

  observer.observe(document.body, {
    childList: true,
    subtree: true,
  });
})();
