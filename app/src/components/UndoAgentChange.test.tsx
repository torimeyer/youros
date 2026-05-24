import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import UndoAgentChange from "./UndoAgentChange";

// Mock the api module so no real HTTP calls are made.
vi.mock("../lib/api", () => ({
  api: {
    get: vi.fn(),
    post: vi.fn(),
  },
}));

import { api } from "../lib/api";

const mockGet = vi.mocked(api.get);
const mockPost = vi.mocked(api.post);

const FAKE_CHANGE = {
  commit_sha: "abc1234def5678901234567890123456789012ab",
  message: "feat: add something useful",
  files_changed: ["src/foo.ts", "src/bar.ts"],
  diff: "--- a/src/foo.ts\n+++ b/src/foo.ts\n@@ -1 +1 @@\n-old\n+new",
};

beforeEach(() => {
  vi.clearAllMocks();
});

describe("UndoAgentChange", () => {
  it("renders the undo button", () => {
    render(<UndoAgentChange agentName="my-agent" />);
    expect(screen.getByTestId("undo-agent-change-btn")).toBeInTheDocument();
    expect(screen.getByTestId("undo-agent-change-btn")).toHaveTextContent("Undo last change");
  });

  it("shows 'Checking…' while loading", async () => {
    mockGet.mockReturnValue(new Promise(() => {})); // never resolves
    render(<UndoAgentChange agentName="my-agent" />);
    fireEvent.click(screen.getByTestId("undo-agent-change-btn"));
    expect(await screen.findByText(/checking/i)).toBeInTheDocument();
  });

  it("opens modal with diff after clicking the button", async () => {
    mockGet.mockResolvedValueOnce(FAKE_CHANGE);
    render(<UndoAgentChange agentName="my-agent" />);
    fireEvent.click(screen.getByTestId("undo-agent-change-btn"));

    await waitFor(() => expect(screen.getByTestId("undo-agent-change-modal")).toBeInTheDocument());
    expect(screen.getByTestId("undo-diff-view")).toBeInTheDocument();
    expect(screen.getByTestId("undo-diff-view")).toHaveTextContent("-old");
    expect(screen.getByText("feat: add something useful")).toBeInTheDocument();
    expect(screen.getAllByText(/src\/foo\.ts/).length).toBeGreaterThan(0);
  });

  it("calls the undo endpoint with commit_sha and confirm:true when confirmed", async () => {
    mockGet.mockResolvedValueOnce(FAKE_CHANGE);
    mockPost.mockResolvedValueOnce({ reverted: FAKE_CHANGE.commit_sha, revert_commit: "deadbeef", message: "Done." });

    render(<UndoAgentChange agentName="my-agent" />);
    fireEvent.click(screen.getByTestId("undo-agent-change-btn"));
    await screen.findByTestId("undo-agent-change-confirm");
    fireEvent.click(screen.getByTestId("undo-agent-change-confirm"));

    await waitFor(() =>
      expect(mockPost).toHaveBeenCalledWith(
        "/agents/my-agent/undo",
        { commit_sha: FAKE_CHANGE.commit_sha, confirm: true },
      )
    );
  });

  it("shows success message after undo completes", async () => {
    mockGet.mockResolvedValueOnce(FAKE_CHANGE);
    mockPost.mockResolvedValueOnce({ reverted: FAKE_CHANGE.commit_sha, revert_commit: "deadbeef", message: "Done." });

    render(<UndoAgentChange agentName="my-agent" />);
    fireEvent.click(screen.getByTestId("undo-agent-change-btn"));
    await screen.findByTestId("undo-agent-change-confirm");
    fireEvent.click(screen.getByTestId("undo-agent-change-confirm"));

    await screen.findByTestId("undo-success-msg");
    expect(screen.getByTestId("undo-success-msg")).toHaveTextContent(/undone/i);
  });

  it("shows error message when the undo endpoint fails", async () => {
    mockGet.mockResolvedValueOnce(FAKE_CHANGE);
    mockPost.mockRejectedValueOnce(new Error("git revert failed: conflicts"));

    render(<UndoAgentChange agentName="my-agent" />);
    fireEvent.click(screen.getByTestId("undo-agent-change-btn"));
    await screen.findByTestId("undo-agent-change-confirm");
    fireEvent.click(screen.getByTestId("undo-agent-change-confirm"));

    await screen.findByTestId("undo-error-msg");
    expect(screen.getByTestId("undo-error-msg")).toHaveTextContent(/git revert failed/i);
  });

  it("shows error message when last-change endpoint returns 404", async () => {
    mockGet.mockRejectedValueOnce(new Error("No commits found for agent 'my-agent'."));

    render(<UndoAgentChange agentName="my-agent" />);
    fireEvent.click(screen.getByTestId("undo-agent-change-btn"));

    await screen.findByTestId("undo-error-msg");
    expect(screen.getByTestId("undo-error-msg")).toHaveTextContent(/no commits found/i);
  });

  it("closes modal when Cancel is clicked", async () => {
    mockGet.mockResolvedValueOnce(FAKE_CHANGE);

    render(<UndoAgentChange agentName="my-agent" />);
    fireEvent.click(screen.getByTestId("undo-agent-change-btn"));
    await screen.findByTestId("undo-agent-change-cancel");
    fireEvent.click(screen.getByTestId("undo-agent-change-cancel"));

    expect(screen.queryByTestId("undo-agent-change-modal")).not.toBeInTheDocument();
  });
});
