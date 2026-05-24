import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { RecentAgentActions } from "./RecentAgentActions";
import { type AgentInfo } from "../lib/agentUtils";

vi.mock("./Icon", () => ({
  default: ({ name }: { name: string }) => <span data-testid={`icon-${name}`} />,
}));

function makeAgent(overrides: Partial<AgentInfo> = {}): AgentInfo {
  return {
    name: "test-agent",
    status: "completed",
    source: "ui",
    ...overrides,
  };
}

describe("RecentAgentActions", () => {
  it("renders Replan button for Meal Planner template", () => {
    render(<RecentAgentActions agent={makeAgent({ template: "Meal Planner" })} />);
    expect(screen.getByTestId("action-replan")).toBeInTheDocument();
    expect(screen.getByText("Replan")).toBeInTheDocument();
  });

  it("renders Replan button for Trip Planner template", () => {
    render(<RecentAgentActions agent={makeAgent({ template: "Trip Planner" })} />);
    expect(screen.getByTestId("action-replan")).toBeInTheDocument();
  });

  it("renders Replan button for any template name containing planner (case insensitive)", () => {
    render(<RecentAgentActions agent={makeAgent({ template: "Daily Planning" })} />);
    expect(screen.getByTestId("action-replan")).toBeInTheDocument();
  });

  it("renders Replan button for daily-planner template slug", () => {
    render(<RecentAgentActions agent={makeAgent({ template: "daily-planner" })} />);
    expect(screen.getByTestId("action-replan")).toBeInTheDocument();
  });

  it("renders Save to gems button for gem-builder template", () => {
    render(<RecentAgentActions agent={makeAgent({ template: "gem-builder" })} />);
    expect(screen.getByTestId("action-save-to-gems")).toBeInTheDocument();
    expect(screen.getByText("Save to gems")).toBeInTheDocument();
  });

  it("renders Run again for unknown template id", () => {
    render(<RecentAgentActions agent={makeAgent({ template: "some-unknown-template" })} />);
    expect(screen.getByTestId("action-run-again")).toBeInTheDocument();
    expect(screen.getByText("Run again")).toBeInTheDocument();
    expect(screen.queryByTestId("action-replan")).not.toBeInTheDocument();
    expect(screen.queryByTestId("action-save-to-gems")).not.toBeInTheDocument();
  });

  it("renders nothing when agent has no template", () => {
    const { container } = render(<RecentAgentActions agent={makeAgent()} />);
    expect(container.firstChild).toBeNull();
  });

  it("calls onRunAgain with correct args when Run again is clicked", async () => {
    const onRunAgain = vi.fn();
    const { getByTestId } = render(
      <RecentAgentActions agent={makeAgent({ template: "some-template" })} onRunAgain={onRunAgain} />
    );
    getByTestId("action-run-again").click();
    expect(onRunAgain).toHaveBeenCalledWith(expect.stringContaining("some-template"), "some-template");
  });

  it("calls onSaveGem when Save to gems is clicked", () => {
    const onSaveGem = vi.fn();
    const { getByTestId } = render(
      <RecentAgentActions agent={makeAgent({ name: "my-agent", template: "gem-builder" })} onSaveGem={onSaveGem} />
    );
    getByTestId("action-save-to-gems").click();
    expect(onSaveGem).toHaveBeenCalledWith("my-agent");
  });

  it("renders Replan for Review template (not a planner — no Replan)", () => {
    render(<RecentAgentActions agent={makeAgent({ template: "Review" })} />);
    expect(screen.queryByTestId("action-replan")).not.toBeInTheDocument();
    expect(screen.getByTestId("action-run-again")).toBeInTheDocument();
  });
});
