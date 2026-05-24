/**
 * Paragraph-break rendering tests for AgentChatThread.
 *
 * These run WITHOUT mocking renderMarkdown so we verify the actual DOM
 * structure that reaches the user's browser, not the mocked passthrough
 * used in AgentChatThread.test.tsx.
 *
 * Bug (→1112): agent messages render as walls of text. Sentence-ending
 * periods abut the next sentence's first word ("terminal status.monitor
 * script crashed") because the DOM structure or CSS is collapsing line
 * breaks that should be visible.
 */
import { describe, it, expect } from "vitest";
import { render, screen, within } from "@testing-library/react";
import { vi } from "vitest";
import { AgentChatThread } from "./AgentChatThread";

// Do NOT mock renderMarkdown here — these tests exist precisely to
// verify the real rendering behavior inside the component.

function makeReply(
  message: string,
  kind: "real" | "conversational" | "ack" = "real"
) {
  return {
    message,
    timestamp: "2026-05-10T12:00:00+00:00",
    kind,
  };
}

function renderThread(
  replies: ReturnType<typeof makeReply>[],
  agentName = "test-bot"
) {
  return render(
    <AgentChatThread
      agentName={agentName}
      nudges={[]}
      replies={replies}
      onSend={vi.fn()}
      isSending={false}
    />
  );
}

describe("AgentChatThread – paragraph break rendering (→1112)", () => {
  it("renders two separate <p> elements for a double-newline (\\n\\n) message", () => {
    // This is the primary regression: two paragraphs separated by a blank
    // line must render as two distinct block elements, not as a single
    // run of text.
    renderThread([makeReply("Para one.\n\nPara two.")]);

    const bubble = screen.getByTestId("agent-chat-assistant-bubble");
    const paragraphs = within(bubble).queryAllByRole("paragraph");
    // querySelectorAll is more direct here since jsdom may not expose
    // paragraphs via role; fall back to DOM query.
    const pElements = bubble.querySelectorAll("p");
    expect(pElements.length).toBeGreaterThanOrEqual(2);
    expect(pElements[0].textContent).toContain("Para one.");
    expect(pElements[1].textContent).toContain("Para two.");
  });

  it("renders a single newline as a <br> (soft line break) inside one <p>", () => {
    // Single newline within a paragraph must produce a <br>, not collapse
    // the text onto one line ("terminal status.monitor script crashed").
    renderThread([makeReply("terminal status.\nmonitor script crashed")]);

    const bubble = screen.getByTestId("agent-chat-assistant-bubble");
    const paragraph = bubble.querySelector("p");
    expect(paragraph).not.toBeNull();
    const br = paragraph!.querySelector("br");
    expect(br).not.toBeNull();
    expect(paragraph!.textContent).toContain("terminal status.");
    expect(paragraph!.textContent).toContain("monitor script crashed");
  });

  it("para one and para two are in separate <p> elements, not the same one", () => {
    // Negative assertion: the two pieces of text must live in DISTINCT
    // paragraph elements. If both land in the same <p> the paragraphs
    // have been collapsed and the blank-line break is lost.
    renderThread([makeReply("First paragraph.\n\nSecond paragraph.")]);

    const bubble = screen.getByTestId("agent-chat-assistant-bubble");
    const pElements = bubble.querySelectorAll("p");
    expect(pElements.length).toBeGreaterThanOrEqual(2);
    // Neither paragraph should contain text from the other.
    expect(pElements[0].textContent).not.toContain("Second paragraph.");
    expect(pElements[1].textContent).not.toContain("First paragraph.");
  });

  it("conversational-kind replies also produce correct paragraph structure", () => {
    renderThread([makeReply("First.\n\nSecond.", "conversational")]);

    const bubble = screen.getByTestId("agent-chat-conversational-bubble");
    const pElements = bubble.querySelectorAll("p");
    expect(pElements.length).toBeGreaterThanOrEqual(2);
    expect(pElements[0].textContent).toContain("First.");
    expect(pElements[1].textContent).toContain("Second.");
  });

  it("bubble container does not carry whitespace-pre-line when rendering markdown", () => {
    // whitespace-pre-line on a container that already renders block <p>
    // elements via renderMarkdown is redundant and can cause visual
    // interference: the inherited pre-line white-space collapses the
    // useful margin-based paragraph gap. The fix removes the class from
    // markdown-rendering bubbles (ack bubbles keep it for raw text).
    renderThread([makeReply("Para one.\n\nPara two.")]);

    const bubble = screen.getByTestId("agent-chat-assistant-bubble");
    expect(bubble.className).not.toContain("whitespace-pre-line");
  });

  it("conversational bubble container does not carry whitespace-pre-line when rendering markdown", () => {
    renderThread([makeReply("Para one.\n\nPara two.", "conversational")]);

    const bubble = screen.getByTestId("agent-chat-conversational-bubble");
    expect(bubble.className).not.toContain("whitespace-pre-line");
  });
});
