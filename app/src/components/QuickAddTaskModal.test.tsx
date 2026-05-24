import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import QuickAddTaskModal from "./QuickAddTaskModal";

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

describe("QuickAddTaskModal", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("does not render when open is false", () => {
    render(
      <QuickAddTaskModal open={false} onClose={vi.fn()} onSuccess={vi.fn()} />
    );
    expect(screen.queryByText("New task")).not.toBeInTheDocument();
  });

  it("renders the form when open is true", () => {
    render(
      <QuickAddTaskModal open={true} onClose={vi.fn()} onSuccess={vi.fn()} />
    );
    expect(screen.getByText("New task")).toBeInTheDocument();
    expect(screen.getByPlaceholderText("Task title")).toBeInTheDocument();
  });

  it("auto-focuses the title input when opened", async () => {
    render(
      <QuickAddTaskModal open={true} onClose={vi.fn()} onSuccess={vi.fn()} />
    );
    const input = screen.getByPlaceholderText("Task title");
    await waitFor(() => {
      expect(document.activeElement).toBe(input);
    });
  });

  it("submits with the correct endpoint and body", async () => {
    mockedPost.mockResolvedValueOnce({ task_id: "T-001" });
    const onClose = vi.fn();
    const onSuccess = vi.fn();

    render(
      <QuickAddTaskModal open={true} onClose={onClose} onSuccess={onSuccess} />
    );

    fireEvent.change(screen.getByPlaceholderText("Task title"), {
      target: { value: "Write docs" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Add task" }));

    await waitFor(() => {
      expect(mockedPost).toHaveBeenCalledWith("/tasks", {
        title: "Write docs",
        priority: "P1",
      });
    });
  });

  it("calls onSuccess and onClose on successful submit", async () => {
    mockedPost.mockResolvedValueOnce({ task_id: "T-001" });
    const onClose = vi.fn();
    const onSuccess = vi.fn();

    render(
      <QuickAddTaskModal open={true} onClose={onClose} onSuccess={onSuccess} />
    );

    fireEvent.change(screen.getByPlaceholderText("Task title"), {
      target: { value: "Write docs" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Add task" }));

    await waitFor(() => {
      expect(onSuccess).toHaveBeenCalled();
      expect(onClose).toHaveBeenCalled();
    });
  });

  it("shows an inline error and stays open on failure", async () => {
    mockedPost.mockRejectedValueOnce(new Error("boom"));
    const onClose = vi.fn();

    render(
      <QuickAddTaskModal open={true} onClose={onClose} onSuccess={vi.fn()} />
    );

    fireEvent.change(screen.getByPlaceholderText("Task title"), {
      target: { value: "Broken" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Add task" }));

    await waitFor(() => {
      expect(
        screen.getByText("Could not create task. Try again.")
      ).toBeInTheDocument();
    });
    expect(onClose).not.toHaveBeenCalled();
  });

  it("Enter key submits the form", async () => {
    mockedPost.mockResolvedValueOnce({ task_id: "T-001" });

    render(
      <QuickAddTaskModal open={true} onClose={vi.fn()} onSuccess={vi.fn()} />
    );

    const input = screen.getByPlaceholderText("Task title");
    fireEvent.change(input, { target: { value: "From Enter" } });
    fireEvent.keyDown(input, { key: "Enter" });

    await waitFor(() => {
      expect(mockedPost).toHaveBeenCalledWith("/tasks", {
        title: "From Enter",
        priority: "P1",
      });
    });
  });

  it("Escape key closes the modal", () => {
    const onClose = vi.fn();
    render(
      <QuickAddTaskModal open={true} onClose={onClose} onSuccess={vi.fn()} />
    );

    fireEvent.keyDown(window, { key: "Escape" });
    expect(onClose).toHaveBeenCalled();
  });

  it("clicking the backdrop closes the modal", () => {
    const onClose = vi.fn();
    render(
      <QuickAddTaskModal open={true} onClose={onClose} onSuccess={vi.fn()} />
    );

    fireEvent.click(screen.getByTestId("quick-add-task-backdrop"));
    expect(onClose).toHaveBeenCalled();
  });

  it("allows choosing a different priority", async () => {
    mockedPost.mockResolvedValueOnce({ task_id: "T-001" });

    render(
      <QuickAddTaskModal open={true} onClose={vi.fn()} onSuccess={vi.fn()} />
    );

    fireEvent.change(screen.getByPlaceholderText("Task title"), {
      target: { value: "Urgent one" },
    });
    fireEvent.click(screen.getByRole("button", { name: /P0/ }));
    fireEvent.click(screen.getByRole("button", { name: "Add task" }));

    await waitFor(() => {
      expect(mockedPost).toHaveBeenCalledWith("/tasks", {
        title: "Urgent one",
        priority: "P0",
      });
    });
  });
});
