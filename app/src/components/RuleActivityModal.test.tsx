import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import RuleActivityModal from "./RuleActivityModal";

vi.mock("../lib/api", () => ({
  api: {
    get: vi.fn(),
    post: vi.fn(),
    put: vi.fn(),
    patch: vi.fn(),
    delete: vi.fn(),
  },
}));

import { api } from "../lib/api";
const mockedGet = vi.mocked(api.get);

const sampleFires = [
  {
    ts: "2026-05-13T10:24:09Z",
    rule: "saa_must_spawn",
    tool: "Bash",
    decision: "block",
    reason: "Trigger word 'fix' present, inline Bash blocked.",
  },
  {
    ts: "2026-05-13T10:25:11Z",
    rule: "saa_must_spawn",
    tool: "Agent",
    decision: "allow",
    reason: "Agent call allowed after trigger.",
  },
];

describe("RuleActivityModal", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockedGet.mockResolvedValue(structuredClone(sampleFires));
  });

  it("renders nothing when closed", () => {
    const onClose = vi.fn();
    render(
      <RuleActivityModal
        open={false}
        ruleKey="saa_must_spawn"
        ruleTitle="Always spawn agents"
        onClose={onClose}
      />,
    );
    expect(
      screen.queryByTestId("rule-activity-modal-backdrop"),
    ).not.toBeInTheDocument();
  });

  it("fetches fires when opened and renders rows", async () => {
    const onClose = vi.fn();
    render(
      <RuleActivityModal
        open={true}
        ruleKey="saa_must_spawn"
        ruleTitle="Always spawn agents"
        onClose={onClose}
      />,
    );
    await waitFor(() => {
      expect(mockedGet).toHaveBeenCalledWith(
        "/rules/fires?rule=saa_must_spawn&limit=20",
      );
    });
    await waitFor(() => {
      expect(screen.getByTestId("rule-activity-table")).toBeInTheDocument();
    });
    expect(screen.getByTestId("rule-activity-row-0")).toBeInTheDocument();
    expect(screen.getByTestId("rule-activity-row-1")).toBeInTheDocument();
    expect(screen.getByText("Bash")).toBeInTheDocument();
    expect(screen.getByText("Agent")).toBeInTheDocument();
  });

  it("filters by decision", async () => {
    const onClose = vi.fn();
    render(
      <RuleActivityModal
        open={true}
        ruleKey="saa_must_spawn"
        ruleTitle="Always spawn agents"
        onClose={onClose}
      />,
    );
    await waitFor(() => {
      expect(screen.getByTestId("rule-activity-table")).toBeInTheDocument();
    });
    const select = screen.getByTestId(
      "rule-activity-filter",
    ) as HTMLSelectElement;
    fireEvent.change(select, { target: { value: "block" } });
    await waitFor(() => {
      // Only one row (the block) should remain.
      expect(screen.getByTestId("rule-activity-row-0")).toBeInTheDocument();
      expect(
        screen.queryByTestId("rule-activity-row-1"),
      ).not.toBeInTheDocument();
    });
  });

  it("shows empty state when there are no fires", async () => {
    mockedGet.mockResolvedValueOnce([]);
    const onClose = vi.fn();
    render(
      <RuleActivityModal
        open={true}
        ruleKey="saa_must_spawn"
        ruleTitle="Always spawn agents"
        onClose={onClose}
      />,
    );
    await waitFor(() => {
      expect(screen.getByTestId("rule-activity-empty")).toBeInTheDocument();
    });
  });

  it("closes on backdrop click and Esc", async () => {
    const onClose = vi.fn();
    render(
      <RuleActivityModal
        open={true}
        ruleKey="saa_must_spawn"
        ruleTitle="Always spawn agents"
        onClose={onClose}
      />,
    );
    await waitFor(() => {
      expect(
        screen.getByTestId("rule-activity-modal-backdrop"),
      ).toBeInTheDocument();
    });
    // Backdrop click.
    fireEvent.click(screen.getByTestId("rule-activity-modal-backdrop"));
    expect(onClose).toHaveBeenCalledTimes(1);
    // Esc key.
    fireEvent.keyDown(window, { key: "Escape" });
    expect(onClose).toHaveBeenCalledTimes(2);
  });

  it("shows an error if the fetch fails", async () => {
    mockedGet.mockRejectedValueOnce(new Error("nope"));
    const onClose = vi.fn();
    render(
      <RuleActivityModal
        open={true}
        ruleKey="saa_must_spawn"
        ruleTitle="Always spawn agents"
        onClose={onClose}
      />,
    );
    await waitFor(() => {
      expect(screen.getByTestId("rule-activity-error")).toBeInTheDocument();
    });
  });
});
