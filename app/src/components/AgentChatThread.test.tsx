import { describe, it, expect, vi } from "vitest";
import { render, screen, within } from "@testing-library/react";
import { AgentChatThread } from "./AgentChatThread";

vi.mock("../lib/markdown", () => ({
  renderMarkdown: (text: string) => text,
}));

// These tests lock the banner copy under the thinking dots to the
// backend's delivery_message. Background: the banner used to
// unconditionally promise "within a couple of seconds" even when the
// agent was mid tool chain and not polling /nudges at all, which led
// to silent waits of a minute plus. The backend now knows whether any
// long-poller is parked for the agent and sends an honest message.
// The UI must surface that message.
//
// Note: delivery_message ALSO renders as a tiny line under each nudge
// bubble in the transcript. We scope queries to the thinking indicator
// container (data-testid="agent-chat-thinking") so the banner assertion
// does not double-match on the bubble copy.

function thinkingBanner() {
  const container = screen.getByTestId("agent-chat-thinking");
  return within(container);
}

describe("AgentChatThread - nudge delivery banner copy", () => {
  it("shows backend delivery_message verbatim when present", () => {
    // The honest case: backend detected a parked long-poller, so the
    // message promises sub-second delivery. The UI must display that.
    render(
      <AgentChatThread
        agentName="parked-bot"
        nudges={[
          {
            message: "are you there?",
            timestamp: new Date().toISOString(),
            delivery: "file_only",
            delivery_message:
              "Sent. The agent is waiting and will reply within a second.",
          },
        ]}
        replies={[]}
        onSend={vi.fn()}
        isSending={false}
      />
    );

    expect(
      thinkingBanner().getByText(/will reply within a second/i)
    ).toBeInTheDocument();
  });

  it("shows honest 'next mailbox check' copy when no waiter is parked", () => {
    // The unhappy path: agent is busy on its own work and not polling.
    // Backend refuses to promise a couple of seconds because it cannot.
    // The UI must show that truthful wording instead of old optimism.
    render(
      <AgentChatThread
        agentName="busy-bot"
        nudges={[
          {
            message: "how's it going?",
            timestamp: new Date().toISOString(),
            delivery: "file_only",
            delivery_message:
              "Saved. The agent will pick this up on its next mailbox check, up to 60 seconds from now.",
          },
        ]}
        replies={[]}
        onSend={vi.fn()}
        isSending={false}
      />
    );

    expect(
      thinkingBanner().getByText(/up to 60 seconds from now/i)
    ).toBeInTheDocument();
    // Old optimistic copy must be absent anywhere on the page.
    expect(
      screen.queryByText(/within a couple of seconds/i)
    ).toBeNull();
  });

  it("shows 'The agent has it now' when delivery is stdin", () => {
    // Stdin delivery is instant. Backend says so, UI echoes it.
    render(
      <AgentChatThread
        agentName="fast-bot"
        nudges={[
          {
            message: "update me",
            timestamp: new Date().toISOString(),
            delivery: "stdin",
            delivery_message: "Sent. The agent should respond shortly.",
          },
        ]}
        replies={[]}
        onSend={vi.fn()}
        isSending={false}
      />
    );

    expect(
      thinkingBanner().getByText(/sent\. the agent should respond shortly/i)
    ).toBeInTheDocument();
  });

  it("does not duplicate user bubble on optimistic plus confirm", () => {
    // Regression test for the screenshot where Tori sent "how's it
    // going?" once but it rendered twice in the transcript. The parent
    // page pushes an optimistic record on Send, then the next
    // /nudges poll returns the persisted record from disk (and from
    // session memory). Both records share the same timestamp and text
    // so the chat thread must collapse them to a single bubble. Two
    // genuinely distinct messages with the same text but different
    // timestamps still both render: the dedupe key is role+ts+text,
    // not role+text alone.
    const ts = "2026-04-15T12:00:00+00:00";
    const dupedNudge = {
      message: "how's it going?",
      timestamp: ts,
      delivery: "file_only" as const,
      delivery_message: "Sent.",
    };

    render(
      <AgentChatThread
        agentName="dupe-bot"
        nudges={[dupedNudge, { ...dupedNudge }, { ...dupedNudge }]}
        replies={[
          {
            message: "On it, give me a sec.",
            timestamp: "2026-04-15T12:00:01+00:00",
            kind: "ack",
          },
          {
            message: "On it, give me a sec.",
            timestamp: "2026-04-15T12:00:01+00:00",
            kind: "ack",
          },
          {
            message: "On it, give me a sec.",
            timestamp: "2026-04-15T12:00:01+00:00",
            kind: "ack",
          },
        ]}
        onSend={vi.fn()}
        isSending={false}
      />
    );

    // Only one user bubble renders, even though the parent passed
    // three duplicate nudges with the same id.
    const userBubbles = screen.getAllByTestId("agent-chat-user-bubble");
    expect(userBubbles).toHaveLength(1);
    expect(userBubbles[0]).toHaveTextContent("how's it going?");

    // Only one ack bubble renders, even though the parent passed
    // three duplicate ack replies with the same id.
    const ackBubbles = screen.getAllByTestId("agent-chat-ack-bubble");
    expect(ackBubbles).toHaveLength(1);
    expect(ackBubbles[0]).toHaveTextContent("On it, give me a sec.");
  });

  it("renders separate bubbles for two real messages with same text", () => {
    // Negative test for the dedupe above: if Tori actually sends the
    // same text twice (different timestamps), both bubbles must
    // render. The dedupe collapses identical id+text records, not
    // identical text alone.
    render(
      <AgentChatThread
        agentName="repeat-bot"
        nudges={[
          {
            message: "ping",
            timestamp: "2026-04-15T12:00:00+00:00",
            delivery: "file_only",
            delivery_message: "Sent.",
          },
          {
            message: "ping",
            timestamp: "2026-04-15T12:00:30+00:00",
            delivery: "file_only",
            delivery_message: "Sent.",
          },
        ]}
        replies={[]}
        onSend={vi.fn()}
        isSending={false}
      />
    );

    const userBubbles = screen.getAllByTestId("agent-chat-user-bubble");
    expect(userBubbles).toHaveLength(2);
  });

  it("falls back to a non promising line when delivery_message is missing", () => {
    // Old nudges written before the field existed must still render
    // something sensible. The fallback does NOT over promise.
    render(
      <AgentChatThread
        agentName="legacy-bot"
        nudges={[
          {
            message: "ping",
            timestamp: new Date().toISOString(),
          },
        ]}
        replies={[]}
        onSend={vi.fn()}
        isSending={false}
      />
    );

    expect(
      thinkingBanner().getByText(/pick this up on its next mailbox check/i)
    ).toBeInTheDocument();
    // Old optimistic wording is gone even in the fallback branch.
    expect(
      screen.queryByText(/within a couple of seconds/i)
    ).toBeNull();
  });
});

describe("AgentChatThread - conversational reply kind (needle 857)", () => {
  it("renders a conversational reply with an AI badge", () => {
    render(
      <AgentChatThread
        agentName="chat-bot"
        nudges={[
          {
            message: "what are you working on?",
            timestamp: "2026-04-23T10:00:00Z",
          },
        ]}
        replies={[
          {
            message: "Looking into the test failures right now.",
            timestamp: "2026-04-23T10:00:03Z",
            kind: "conversational",
          },
        ]}
        onSend={vi.fn()}
        isSending={false}
      />
    );

    expect(screen.getByTestId("agent-chat-conversational-row")).toBeInTheDocument();
    expect(screen.getByTestId("agent-chat-conversational-bubble")).toBeInTheDocument();
    expect(
      screen.getByText("Looking into the test failures right now.")
    ).toBeInTheDocument();
    // AI badge is present
    expect(screen.getByTestId("agent-chat-conversational-label")).toBeInTheDocument();
    expect(screen.getByTestId("agent-chat-conversational-label").textContent).toBe("AI");
  });

  it("does not show thinking dots after a conversational reply", () => {
    render(
      <AgentChatThread
        agentName="chat-bot"
        nudges={[
          {
            message: "ping",
            timestamp: "2026-04-23T10:00:00Z",
          },
        ]}
        replies={[
          {
            message: "Still working on the build.",
            timestamp: "2026-04-23T10:00:02Z",
            kind: "conversational",
          },
        ]}
        onSend={vi.fn()}
        isSending={false}
      />
    );

    // Thinking dots should be gone once any substantive reply (ack excluded) lands
    expect(screen.queryByTestId("agent-chat-thinking")).toBeNull();
  });

  it("still shows thinking dots after an ack but not after conversational", () => {
    const { rerender } = render(
      <AgentChatThread
        agentName="chat-bot"
        nudges={[{ message: "hey", timestamp: "2026-04-23T10:00:00Z" }]}
        replies={[
          { message: "Got your message.", timestamp: "2026-04-23T10:00:01Z", kind: "ack" },
        ]}
        onSend={vi.fn()}
        isSending={false}
      />
    );
    // Ack only: dots still showing
    expect(screen.getByTestId("agent-chat-thinking")).toBeInTheDocument();

    rerender(
      <AgentChatThread
        agentName="chat-bot"
        nudges={[{ message: "hey", timestamp: "2026-04-23T10:00:00Z" }]}
        replies={[
          { message: "Got your message.", timestamp: "2026-04-23T10:00:01Z", kind: "ack" },
          { message: "Here is my answer.", timestamp: "2026-04-23T10:00:04Z", kind: "conversational" },
        ]}
        onSend={vi.fn()}
        isSending={false}
      />
    );
    // Conversational reply landed: dots gone
    expect(screen.queryByTestId("agent-chat-thinking")).toBeNull();
  });
});
