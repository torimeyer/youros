import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import ProvenanceChip from "./ProvenanceChip";

afterEach(() => {
  vi.restoreAllMocks();
});

const SIDECAR = {
  agent_name: "Builder",
  task_id: "42",
  created_at: new Date(Date.now() - 7200000).toISOString(),
  prompt_summary: "Build the thing",
};

describe("ProvenanceChip", () => {
  it("renders chip when fetch returns sidecar", async () => {
    vi.spyOn(global, "fetch").mockResolvedValueOnce({
      json: async () => SIDECAR,
      ok: true,
    } as Response);

    render(<ProvenanceChip filePath="/some/file.md" />);

    await waitFor(() =>
      expect(screen.getByTestId("provenance-chip")).toBeInTheDocument()
    );

    const chip = screen.getByTestId("provenance-chip");
    expect(chip.textContent).toContain("Created by Builder");
    expect(chip.textContent).toContain("Task #42");
  });

  it("renders nothing when fetch returns empty", async () => {
    vi.spyOn(global, "fetch").mockResolvedValueOnce({
      json: async () => ({}),
      ok: true,
    } as Response);

    render(<ProvenanceChip filePath="/some/file.md" />);

    await new Promise((r) => setTimeout(r, 50));
    expect(screen.queryByTestId("provenance-chip")).not.toBeInTheDocument();
  });

  it("shows time-ago in plain language", async () => {
    vi.spyOn(global, "fetch").mockResolvedValueOnce({
      json: async () => SIDECAR,
      ok: true,
    } as Response);

    render(<ProvenanceChip filePath="/some/file.md" />);

    await waitFor(() =>
      expect(screen.getByTestId("provenance-chip")).toBeInTheDocument()
    );

    const text = screen.getByTestId("provenance-chip").textContent ?? "";
    expect(text).toMatch(/ago|just now/i);
  });

  it("renders nothing when fetch fails", async () => {
    vi.spyOn(global, "fetch").mockRejectedValueOnce(new Error("network"));

    render(<ProvenanceChip filePath="/some/file.md" />);

    await new Promise((r) => setTimeout(r, 50));
    expect(screen.queryByTestId("provenance-chip")).not.toBeInTheDocument();
  });
});
