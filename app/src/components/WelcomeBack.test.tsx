import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import WelcomeBack from "./WelcomeBack";

const baseContext = {
  active_agents: [],
  recent_agents: [],
  in_progress_tasks: [],
  last_chat: null,
  next_step: "Pick up where you left off.",
};

describe("WelcomeBack — agent name display", () => {
  it("shows the task description as primary label, not the raw process name", () => {
    render(
      <WelcomeBack
        context={{
          ...baseContext,
          active_agents: [
            {
              name: "claude-code-4908",
              task: "Fix delete-all 403",
              current_step: "running tests",
              status: "running",
            },
          ],
        }}
        onDismiss={() => {}}
      />,
    );
    expect(screen.getByText("Fix delete-all 403")).toBeInTheDocument();
    expect(screen.queryByText("claude-code-4908")).not.toBeInTheDocument();
  });

  it("falls back to agent name when task is empty", () => {
    render(
      <WelcomeBack
        context={{
          ...baseContext,
          active_agents: [
            {
              name: "my-named-agent",
              task: "",
              current_step: "",
              status: "running",
            },
          ],
        }}
        onDismiss={() => {}}
      />,
    );
    expect(screen.getByText("my-named-agent")).toBeInTheDocument();
  });

  it("still shows current_step as secondary text alongside the task label", () => {
    render(
      <WelcomeBack
        context={{
          ...baseContext,
          active_agents: [
            {
              name: "claude-code-4908",
              task: "Fix delete-all 403",
              current_step: "running tests",
              status: "running",
            },
          ],
        }}
        onDismiss={() => {}}
      />,
    );
    expect(screen.getByText("Fix delete-all 403")).toBeInTheDocument();
    expect(screen.getByText("running tests")).toBeInTheDocument();
  });
});
