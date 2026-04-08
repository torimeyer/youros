import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import ClarificationPrompt from "./ClarificationPrompt";

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

describe("ClarificationPrompt", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("renders the clarifying question", () => {
    render(
      <ClarificationPrompt
        straw="build an app"
        question="Web or mobile?"
        onClose={vi.fn()}
        onCreated={vi.fn()}
      />
    );

    expect(screen.getByText("Web or mobile?")).toBeInTheDocument();
    expect(screen.getByText("Quick question")).toBeInTheDocument();
  });

  it("sends the answer to the /ideas/answer endpoint", async () => {
    mockedPost.mockResolvedValueOnce({
      status: "created",
      tasks: [
        { id: "1", title: "Task 1", description: "", priority: "P2", order: 0 },
      ],
    });

    const onCreated = vi.fn();
    render(
      <ClarificationPrompt
        straw="build an app"
        question="Web or mobile?"
        onClose={vi.fn()}
        onCreated={onCreated}
      />
    );

    const input = screen.getByPlaceholderText("Type your answer");
    fireEvent.change(input, { target: { value: "web" } });
    fireEvent.click(screen.getByRole("button", { name: "Send answer" }));

    await waitFor(() => {
      expect(mockedPost).toHaveBeenCalledWith("/ideas/answer", {
        straw: "build an app",
        answer: "web",
      });
    });
  });

  it("calls onCreated with task count when tasks are created", async () => {
    mockedPost.mockResolvedValueOnce({
      status: "created",
      tasks: [
        { id: "1", title: "A", description: "", priority: "P2", order: 0 },
        { id: "2", title: "B", description: "", priority: "P2", order: 1 },
        { id: "3", title: "C", description: "", priority: "P2", order: 2 },
      ],
    });

    const onCreated = vi.fn();
    render(
      <ClarificationPrompt
        straw="idea"
        question="Question?"
        onClose={vi.fn()}
        onCreated={onCreated}
      />
    );

    fireEvent.change(screen.getByPlaceholderText("Type your answer"), {
      target: { value: "answer" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Send answer" }));

    await waitFor(() => {
      expect(onCreated).toHaveBeenCalledWith(3);
    });
  });

  it("swaps the question when the backend asks another one", async () => {
    mockedPost.mockResolvedValueOnce({
      status: "needs_clarification",
      question: "Is it for teams or individuals?",
    });

    render(
      <ClarificationPrompt
        straw="idea"
        question="First question?"
        onClose={vi.fn()}
        onCreated={vi.fn()}
      />
    );

    fireEvent.change(screen.getByPlaceholderText("Type your answer"), {
      target: { value: "something" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Send answer" }));

    await waitFor(() => {
      expect(screen.getByText("Is it for teams or individuals?")).toBeInTheDocument();
    });
    expect(screen.queryByText("First question?")).not.toBeInTheDocument();
    // Round indicator should advance.
    expect(screen.getByText("Round 2 of 3")).toBeInTheDocument();
  });

  it("stops asking after 3 rounds", async () => {
    mockedPost.mockResolvedValue({
      status: "needs_clarification",
      question: "Still vague?",
    });

    render(
      <ClarificationPrompt
        straw="idea"
        question="First?"
        onClose={vi.fn()}
        onCreated={vi.fn()}
      />
    );

    // Round 1 -> 2
    fireEvent.change(screen.getByPlaceholderText("Type your answer"), {
      target: { value: "a1" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Send answer" }));
    await waitFor(() => {
      expect(screen.getByText("Round 2 of 3")).toBeInTheDocument();
    });

    // Round 2 -> 3
    fireEvent.change(screen.getByPlaceholderText("Type your answer"), {
      target: { value: "a2" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Send answer" }));
    await waitFor(() => {
      expect(screen.getByText("Round 3 of 3")).toBeInTheDocument();
    });

    // Round 3 -> cap. Should show error message, not a 4th question.
    fireEvent.change(screen.getByPlaceholderText("Type your answer"), {
      target: { value: "a3" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Send answer" }));

    await waitFor(() => {
      expect(
        screen.getByText(/still need more info/i)
      ).toBeInTheDocument();
    });
  });

  it("clicking Cancel calls onClose", () => {
    const onClose = vi.fn();
    render(
      <ClarificationPrompt
        straw="idea"
        question="Q?"
        onClose={onClose}
        onCreated={vi.fn()}
      />
    );

    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(onClose).toHaveBeenCalled();
  });

  it("Send button is disabled when input is empty", () => {
    render(
      <ClarificationPrompt
        straw="idea"
        question="Q?"
        onClose={vi.fn()}
        onCreated={vi.fn()}
      />
    );

    const button = screen.getByRole("button", {
      name: "Send answer",
    }) as HTMLButtonElement;
    expect(button.disabled).toBe(true);
  });

  it("Enter key submits the answer", async () => {
    mockedPost.mockResolvedValueOnce({
      status: "created",
      tasks: [{ id: "1", title: "T", description: "", priority: "P2", order: 0 }],
    });

    render(
      <ClarificationPrompt
        straw="idea"
        question="Q?"
        onClose={vi.fn()}
        onCreated={vi.fn()}
      />
    );

    const input = screen.getByPlaceholderText("Type your answer");
    fireEvent.change(input, { target: { value: "my answer" } });
    fireEvent.keyDown(input, { key: "Enter" });

    await waitFor(() => {
      expect(mockedPost).toHaveBeenCalled();
    });
  });

  it("shows a friendly error when the request fails", async () => {
    mockedPost.mockRejectedValueOnce(new Error("boom"));

    render(
      <ClarificationPrompt
        straw="idea"
        question="Q?"
        onClose={vi.fn()}
        onCreated={vi.fn()}
      />
    );

    fireEvent.change(screen.getByPlaceholderText("Type your answer"), {
      target: { value: "hi" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Send answer" }));

    await waitFor(() => {
      expect(
        screen.getByText("Something went wrong. Try again.")
      ).toBeInTheDocument();
    });
  });
});
