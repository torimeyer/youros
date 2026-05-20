import { describe, it, expect, beforeEach, vi } from "vitest";
import { renderHook, act } from "@testing-library/react";
import { render, screen, fireEvent } from "@testing-library/react";
import { isChatAgent, useChatFilter, ChatFilterChip } from "./AgentsFilter";

// ---------------------------------------------------------------------------
// isChatAgent
// ---------------------------------------------------------------------------

describe("isChatAgent", () => {
  it("returns true for source=chat agents", () => {
    expect(isChatAgent({ source: "chat", name: "some-name", task: undefined, description: undefined })).toBe(true);
  });

  it("returns true for inferred cc session with no task or description", () => {
    expect(
      isChatAgent({ source: "claude-code", name: "claude-code-abc1234", task: undefined, description: undefined })
    ).toBe(true);
  });

  it("returns false for inferred cc session WITH a task description", () => {
    expect(
      isChatAgent({ source: "claude-code", name: "claude-code-abc1234", task: "Fix the login bug", description: undefined })
    ).toBe(false);
  });

  it("returns false for inferred cc session with a description", () => {
    expect(
      isChatAgent({ source: "claude-code", name: "claude-code-abc1234", task: undefined, description: "Claude Code session (cwd: /foo)" })
    ).toBe(false);
  });

  it("returns false for named worktree agents", () => {
    expect(
      isChatAgent({ source: "claude-code", name: "review-ghost-detection-agents-ux-1234", task: "fix ghost reaper", description: undefined })
    ).toBe(false);
  });

  it("returns false for source=api agents", () => {
    expect(isChatAgent({ source: "api", name: "my-agent", task: undefined, description: undefined })).toBe(false);
  });

  it("returns false for source=claude-code with numeric-only suffix shorter than 4 chars", () => {
    // name must match /^claude-code-[0-9a-f]{4,}/ — short suffixes are not inferred sessions
    expect(
      isChatAgent({ source: "claude-code", name: "claude-code-abc", task: undefined, description: undefined })
    ).toBe(false);
  });
});

// ---------------------------------------------------------------------------
// useChatFilter
// ---------------------------------------------------------------------------

describe("useChatFilter", () => {
  beforeEach(() => {
    localStorage.clear();
  });

  it("defaults to showChat=false", () => {
    const { result } = renderHook(() => useChatFilter());
    expect(result.current.showChat).toBe(false);
  });

  it("toggles showChat on call", () => {
    const { result } = renderHook(() => useChatFilter());
    act(() => result.current.toggleShowChat());
    expect(result.current.showChat).toBe(true);
    act(() => result.current.toggleShowChat());
    expect(result.current.showChat).toBe(false);
  });

  it("persists preference to localStorage", () => {
    const { result } = renderHook(() => useChatFilter());
    act(() => result.current.toggleShowChat());
    expect(localStorage.getItem("agents.showChatAgents")).toBe("true");
  });

  it("restores persisted preference on mount", () => {
    localStorage.setItem("agents.showChatAgents", "true");
    const { result } = renderHook(() => useChatFilter());
    expect(result.current.showChat).toBe(true);
  });
});

// ---------------------------------------------------------------------------
// ChatFilterChip
// ---------------------------------------------------------------------------

describe("ChatFilterChip", () => {
  it("renders nothing when hiddenCount=0 and showChat=false", () => {
    const { container } = render(
      <ChatFilterChip showChat={false} hiddenCount={0} onToggle={() => {}} />
    );
    expect(container.firstChild).toBeNull();
  });

  it("renders 'Hiding chat agents (N)' when showChat=false and hiddenCount>0", () => {
    render(<ChatFilterChip showChat={false} hiddenCount={3} onToggle={() => {}} />);
    expect(screen.getByText(/Hiding chat agents \(3\)/)).toBeInTheDocument();
    expect(screen.getByText("show")).toBeInTheDocument();
  });

  it("renders 'Showing chat agents' when showChat=true", () => {
    render(<ChatFilterChip showChat={true} hiddenCount={0} onToggle={() => {}} />);
    expect(screen.getByText("Showing chat agents")).toBeInTheDocument();
    expect(screen.getByText("hide")).toBeInTheDocument();
  });

  it("calls onToggle when clicked", () => {
    const onToggle = vi.fn();
    render(<ChatFilterChip showChat={false} hiddenCount={2} onToggle={onToggle} />);
    fireEvent.click(screen.getByTestId("recent-chat-toggle"));
    expect(onToggle).toHaveBeenCalledOnce();
  });

  it("has accessible aria-label on toggle button", () => {
    render(<ChatFilterChip showChat={false} hiddenCount={5} onToggle={() => {}} />);
    const btn = screen.getByTestId("recent-chat-toggle");
    expect(btn.getAttribute("aria-label")).toMatch(/5 hidden chat agents/i);
  });
});
