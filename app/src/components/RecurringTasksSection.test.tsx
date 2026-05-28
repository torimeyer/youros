import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import RecurringTasksSection from "./RecurringTasksSection";

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
const mockedPost = vi.mocked(api.post);

describe("RecurringTasksSection", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("shows the empty state when there are no rules", async () => {
    mockedGet.mockResolvedValueOnce({ rules: [] });

    render(<RecurringTasksSection />);

    await waitFor(() => {
      expect(screen.getByText(/No recurring tasks yet/i)).toBeInTheDocument();
    });
  });

  it("renders the list of rules returned by the API", async () => {
    mockedGet.mockResolvedValueOnce({
      rules: [
        {
          id: "rec-abc",
          task_template: {
            title: "Weekly status update",
            description: "Post the team update.",
            priority: "P1",
            ac: "Update posted.",
          },
          schedule: { kind: "weekly", days: [4] },
          last_spawned_at: null,
          active: true,
        },
        {
          id: "rec-def",
          task_template: {
            title: "Pay invoices",
            description: "",
            priority: "P0",
            ac: "",
          },
          schedule: { kind: "monthly", day_of_month: 1 },
          last_spawned_at: null,
          active: false,
        },
      ],
    });

    render(<RecurringTasksSection />);

    await waitFor(() => {
      expect(screen.getByText("Weekly status update")).toBeInTheDocument();
    });
    expect(screen.getByText("Pay invoices")).toBeInTheDocument();
    // Weekly Fri -> "Every Fri"
    expect(screen.getByText(/Every Fri/)).toBeInTheDocument();
    // Monthly on the 1st
    expect(screen.getByText(/On the 1st of each month/)).toBeInTheDocument();
    // Inactive rule shows "Paused"
    expect(screen.getByText("Paused")).toBeInTheDocument();
  });

  it("opens the modal when the add button is clicked", async () => {
    mockedGet.mockResolvedValueOnce({ rules: [] });

    render(<RecurringTasksSection />);

    await waitFor(() => {
      expect(screen.getByTestId("recurring-add-button")).toBeInTheDocument();
    });

    fireEvent.click(screen.getByTestId("recurring-add-button"));
    expect(screen.getByTestId("recurring-modal")).toBeInTheDocument();
    expect(screen.getByText("New recurring task")).toBeInTheDocument();
  });

  it("submits a new recurring task with title and schedule", async () => {
    mockedGet.mockResolvedValueOnce({ rules: [] });
    mockedPost.mockResolvedValueOnce({
      rule: {
        id: "rec-new",
        task_template: { title: "Drink water", description: "", priority: "P1", ac: "" },
        schedule: { kind: "weekly", days: [1] },
        last_spawned_at: null,
        active: true,
      },
    });
    // After submit the component refetches. Give it something to return.
    mockedGet.mockResolvedValueOnce({
      rules: [
        {
          id: "rec-new",
          task_template: { title: "Drink water", description: "", priority: "P1", ac: "" },
          schedule: { kind: "weekly", days: [1] },
          last_spawned_at: null,
          active: true,
        },
      ],
    });

    render(<RecurringTasksSection />);

    await waitFor(() => {
      expect(screen.getByTestId("recurring-add-button")).toBeInTheDocument();
    });

    fireEvent.click(screen.getByTestId("recurring-add-button"));

    fireEvent.change(screen.getByTestId("recurring-title-input"), {
      target: { value: "Drink water" },
    });
    fireEvent.click(screen.getByTestId("recurring-save-button"));

    await waitFor(() => {
      expect(mockedPost).toHaveBeenCalled();
    });
    const call = mockedPost.mock.calls[0];
    expect(call[0]).toBe("/recurring");
    const body = call[1] as {
      task_template: { title: string };
      schedule: { kind: string; days?: number[] };
      active: boolean;
    };
    expect(body.task_template.title).toBe("Drink water");
    expect(body.schedule.kind).toBe("weekly");
    expect(body.active).toBe(true);
  });

  it("shows an error when the title is empty", async () => {
    mockedGet.mockResolvedValueOnce({ rules: [] });

    render(<RecurringTasksSection />);

    await waitFor(() => {
      expect(screen.getByTestId("recurring-add-button")).toBeInTheDocument();
    });

    fireEvent.click(screen.getByTestId("recurring-add-button"));
    fireEvent.click(screen.getByTestId("recurring-save-button"));

    expect(await screen.findByText(/Give the task a title/i)).toBeInTheDocument();
    expect(mockedPost).not.toHaveBeenCalled();
  });
});
