import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import AdhdCheckin from "./AdhdCheckin";

describe("AdhdCheckin — agent name display", () => {
  it("shows the task description as primary label, not the raw process name", async () => {
    render(
      <AdhdCheckin
        running_count={1}
        agents={[
          {
            name: "claude-code-4908",
            task: "Fix delete-all 403",
            current_step: "",
          },
        ]}
        intervalSeconds={0}
      />,
    );
    expect(await screen.findByText("Fix delete-all 403")).toBeInTheDocument();
    expect(screen.queryByText("claude-code-4908")).not.toBeInTheDocument();
  });

  it("falls back to agent name when task is empty", async () => {
    render(
      <AdhdCheckin
        running_count={1}
        agents={[{ name: "my-named-agent", task: "", current_step: "" }]}
        intervalSeconds={0}
      />,
    );
    expect(await screen.findByText("my-named-agent")).toBeInTheDocument();
  });

  it("does not repeat task text as secondary line when already shown as primary", async () => {
    render(
      <AdhdCheckin
        running_count={1}
        agents={[
          {
            name: "claude-code-4908",
            task: "Fix delete-all 403",
            current_step: "",
          },
        ]}
        intervalSeconds={0}
      />,
    );
    await screen.findByText("Fix delete-all 403");
    // task should appear exactly once — not duplicated as both primary AND secondary
    expect(screen.getAllByText(/fix delete-all 403/i)).toHaveLength(1);
  });

  it("shows current_step as secondary when both task and step are present", async () => {
    render(
      <AdhdCheckin
        running_count={1}
        agents={[
          {
            name: "claude-code-4908",
            task: "Fix delete-all 403",
            current_step: "running tests",
          },
        ]}
        intervalSeconds={0}
      />,
    );
    await screen.findByText("Fix delete-all 403");
    expect(screen.getByText(/running tests/)).toBeInTheDocument();
  });
});
