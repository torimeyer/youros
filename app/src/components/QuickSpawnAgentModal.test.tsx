import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import QuickSpawnAgentModal from "./QuickSpawnAgentModal";

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

const mockedPost = vi.mocked(api.post);

describe("QuickSpawnAgentModal", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("does not render when open is false", () => {
    render(
      <QuickSpawnAgentModal open={false} onClose={vi.fn()} onSuccess={vi.fn()} />
    );
    expect(
      screen.queryByRole("heading", { name: "Spawn agent" })
    ).not.toBeInTheDocument();
  });

  it("renders the form when open is true", () => {
    render(
      <QuickSpawnAgentModal open={true} onClose={vi.fn()} onSuccess={vi.fn()} />
    );
    expect(
      screen.getByRole("heading", { name: "Spawn agent" })
    ).toBeInTheDocument();
    expect(
      screen.getByPlaceholderText("e.g. research-assistant")
    ).toBeInTheDocument();
  });

  it("auto-focuses the name input when opened", async () => {
    render(
      <QuickSpawnAgentModal open={true} onClose={vi.fn()} onSuccess={vi.fn()} />
    );
    const input = screen.getByPlaceholderText("e.g. research-assistant");
    await waitFor(() => {
      expect(document.activeElement).toBe(input);
    });
  });

  it("submits with the correct endpoint and body", async () => {
    mockedPost.mockResolvedValueOnce({ ok: true });

    render(
      <QuickSpawnAgentModal open={true} onClose={vi.fn()} onSuccess={vi.fn()} />
    );

    fireEvent.change(screen.getByPlaceholderText("e.g. research-assistant"), {
      target: { value: "research-assistant" },
    });
    fireEvent.change(
      screen.getByPlaceholderText(/Describe the job/),
      { target: { value: "look into X" } }
    );
    fireEvent.click(screen.getByRole("button", { name: "Spawn agent" }));

    await waitFor(() => {
      expect(mockedPost).toHaveBeenCalledWith("/agents/spawn", {
        name: "research-assistant",
        prompt: "look into X",
        model: "sonnet",
        budget: 2.0,
      });
    });
  });

  it("calls onSuccess and onClose on successful submit", async () => {
    mockedPost.mockResolvedValueOnce({ ok: true });
    const onClose = vi.fn();
    const onSuccess = vi.fn();

    render(
      <QuickSpawnAgentModal open={true} onClose={onClose} onSuccess={onSuccess} />
    );

    fireEvent.change(screen.getByPlaceholderText("e.g. research-assistant"), {
      target: { value: "agent-1" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Spawn agent" }));

    await waitFor(() => {
      expect(onSuccess).toHaveBeenCalled();
      expect(onClose).toHaveBeenCalled();
    });
  });

  it("shows an inline error and stays open on failure", async () => {
    mockedPost.mockRejectedValueOnce(new Error("boom"));
    const onClose = vi.fn();

    render(
      <QuickSpawnAgentModal open={true} onClose={onClose} onSuccess={vi.fn()} />
    );

    fireEvent.change(screen.getByPlaceholderText("e.g. research-assistant"), {
      target: { value: "broken-agent" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Spawn agent" }));

    await waitFor(() => {
      expect(
        screen.getByText("Could not spawn agent. Try again.")
      ).toBeInTheDocument();
    });
    expect(onClose).not.toHaveBeenCalled();
  });

  it("Enter in the name field moves focus to the prompt textarea", () => {
    render(
      <QuickSpawnAgentModal open={true} onClose={vi.fn()} onSuccess={vi.fn()} />
    );

    const nameInput = screen.getByPlaceholderText("e.g. research-assistant");
    fireEvent.change(nameInput, { target: { value: "my-agent" } });
    fireEvent.keyDown(nameInput, { key: "Enter" });

    const textarea = screen.getByPlaceholderText(/Describe the job/);
    expect(document.activeElement).toBe(textarea);
  });

  it("Enter in the textarea submits, Shift+Enter does not", async () => {
    mockedPost.mockResolvedValueOnce({ ok: true });

    render(
      <QuickSpawnAgentModal open={true} onClose={vi.fn()} onSuccess={vi.fn()} />
    );

    fireEvent.change(screen.getByPlaceholderText("e.g. research-assistant"), {
      target: { value: "my-agent" },
    });
    const textarea = screen.getByPlaceholderText(/Describe the job/);
    fireEvent.change(textarea, { target: { value: "prompt" } });

    // Shift+Enter should not submit.
    fireEvent.keyDown(textarea, { key: "Enter", shiftKey: true });
    expect(mockedPost).not.toHaveBeenCalled();

    // Plain Enter should submit.
    fireEvent.keyDown(textarea, { key: "Enter" });
    await waitFor(() => {
      expect(mockedPost).toHaveBeenCalled();
    });
  });

  it("Escape key closes the modal", () => {
    const onClose = vi.fn();
    render(
      <QuickSpawnAgentModal open={true} onClose={onClose} onSuccess={vi.fn()} />
    );

    fireEvent.keyDown(window, { key: "Escape" });
    expect(onClose).toHaveBeenCalled();
  });

  it("clicking the backdrop closes the modal", () => {
    const onClose = vi.fn();
    render(
      <QuickSpawnAgentModal open={true} onClose={onClose} onSuccess={vi.fn()} />
    );

    fireEvent.click(screen.getByTestId("quick-spawn-agent-backdrop"));
    expect(onClose).toHaveBeenCalled();
  });
});
