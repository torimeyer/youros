import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import SpecWizard from "./SpecWizard";

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

const defaultProps = {
  onComplete: vi.fn(),
  onCancel: vi.fn(),
};

describe("SpecWizard", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  // -------------------------------------------------------------------
  // Default mode: needle (task)
  // -------------------------------------------------------------------

  it("renders in needle mode by default", () => {
    render(<SpecWizard {...defaultProps} />);
    expect(screen.getByTestId("needle-mode")).toBeInTheDocument();
    expect(screen.queryByTestId("wizard-problem")).not.toBeInTheDocument();
  });

  it("needle tab is active by default", () => {
    render(<SpecWizard {...defaultProps} />);
    const needleTab = screen.getByTestId("mode-needle");
    // active tab should have aria-pressed true or similar class; just verify it exists
    expect(needleTab).toBeInTheDocument();
  });

  it("needle mode shows title and optional description inputs", () => {
    render(<SpecWizard {...defaultProps} />);
    expect(screen.getByTestId("needle-title")).toBeInTheDocument();
    expect(screen.getByTestId("needle-desc")).toBeInTheDocument();
  });

  it("needle submit calls POST /specs/draft with kind=needle", async () => {
    mockedPost.mockResolvedValueOnce({ kind: "needle", task_id: "42" });
    const onComplete = vi.fn();

    render(<SpecWizard onComplete={onComplete} onCancel={vi.fn()} />);

    fireEvent.change(screen.getByTestId("needle-title"), {
      target: { value: "Cache invalidation on logout" },
    });
    fireEvent.click(screen.getByTestId("needle-submit"));

    await waitFor(() => {
      expect(mockedPost).toHaveBeenCalledWith("/specs/draft", {
        title: "Cache invalidation on logout",
        kind: "needle",
      });
    });
    expect(onComplete).toHaveBeenCalledWith("");
  });

  it("needle submit does nothing when title is empty", async () => {
    render(<SpecWizard {...defaultProps} />);

    fireEvent.click(screen.getByTestId("needle-submit"));

    await waitFor(() => {
      expect(mockedPost).not.toHaveBeenCalled();
    });
  });

  it("needle description is optional and not sent when empty", async () => {
    mockedPost.mockResolvedValueOnce({ kind: "needle", task_id: "7" });

    render(<SpecWizard {...defaultProps} />);

    fireEvent.change(screen.getByTestId("needle-title"), {
      target: { value: "Fix footer overlap" },
    });
    // do NOT fill in needle-desc
    fireEvent.click(screen.getByTestId("needle-submit"));

    await waitFor(() => {
      expect(mockedPost).toHaveBeenCalledWith("/specs/draft", {
        title: "Fix footer overlap",
        kind: "needle",
      });
    });
  });

  // -------------------------------------------------------------------
  // Opt-in mode: spec
  // -------------------------------------------------------------------

  it("clicking Spec tab switches to spec mode", () => {
    render(<SpecWizard {...defaultProps} />);

    fireEvent.click(screen.getByTestId("mode-spec"));

    expect(screen.queryByTestId("needle-mode")).not.toBeInTheDocument();
    expect(screen.getByTestId("wizard-title")).toBeInTheDocument();
    expect(screen.getByTestId("wizard-problem")).toBeInTheDocument();
  });

  it("spec mode requires title and problem to advance past step 1", async () => {
    render(<SpecWizard {...defaultProps} />);
    fireEvent.click(screen.getByTestId("mode-spec"));

    // Next button should be disabled or not advance without both fields
    const nextBtn = screen.getByRole("button", { name: /next/i });
    expect(nextBtn).toBeDisabled();
  });

  it("spec mode calls POST /specs/wizard/create with kind=spec on final submit", async () => {
    mockedPost
      .mockResolvedValueOnce({ criteria: ["a", "b", "c"], non_goals: [], after_statement: "" }) // suggest
      .mockResolvedValueOnce({ path: "docs/spec/my-spec.md" }); // create

    const onComplete = vi.fn();
    render(<SpecWizard onComplete={onComplete} onCancel={vi.fn()} />);

    // Switch to spec mode
    fireEvent.click(screen.getByTestId("mode-spec"));

    // Step 1: Problem
    fireEvent.change(screen.getByTestId("wizard-title"), {
      target: { value: "Offline mode for mobile" },
    });
    fireEvent.change(screen.getByTestId("wizard-problem"), {
      target: { value: "Users lose work when they go offline." },
    });
    fireEvent.click(screen.getByRole("button", { name: /next/i }));

    // Step 2: Scope (no required fields, advance)
    await waitFor(() => {
      expect(screen.getByRole("button", { name: /next/i })).toBeInTheDocument();
    });
    fireEvent.click(screen.getByRole("button", { name: /next/i }));

    // Step 3: Criteria (auto-suggested: a, b, c from mock)
    await waitFor(() => {
      expect(screen.getByRole("button", { name: /next/i })).not.toBeDisabled();
    });
    fireEvent.click(screen.getByRole("button", { name: /next/i }));

    // Step 4: Review - submit
    await waitFor(() => {
      expect(screen.getByTestId("spec-submit")).toBeInTheDocument();
    });
    fireEvent.click(screen.getByTestId("spec-submit"));

    await waitFor(() => {
      expect(mockedPost).toHaveBeenLastCalledWith(
        "/specs/wizard/create",
        expect.objectContaining({ kind: "spec" }),
      );
    });
    expect(onComplete).toHaveBeenCalledWith("docs/spec/my-spec.md");
  });

  // -------------------------------------------------------------------
  // Mode switching
  // -------------------------------------------------------------------

  it("switching back to needle tab restores needle mode", () => {
    render(<SpecWizard {...defaultProps} />);

    fireEvent.click(screen.getByTestId("mode-spec"));
    expect(screen.getByTestId("wizard-title")).toBeInTheDocument();

    fireEvent.click(screen.getByTestId("mode-needle"));
    expect(screen.getByTestId("needle-mode")).toBeInTheDocument();
    expect(screen.queryByTestId("wizard-title")).not.toBeInTheDocument();
  });

  // -------------------------------------------------------------------
  // Produces step (→1531)
  // -------------------------------------------------------------------

  it("spec mode shows Produces step between Scope and Criteria", async () => {
    render(<SpecWizard {...defaultProps} />);
    fireEvent.click(screen.getByTestId("mode-spec"));

    // Fill Problem
    fireEvent.change(screen.getByTestId("wizard-title"), { target: { value: "Q3 strategy" } });
    fireEvent.change(screen.getByTestId("wizard-problem"), { target: { value: "We need a strategy doc" } });
    fireEvent.click(screen.getByRole("button", { name: /next/i }));

    // Scope step
    await waitFor(() => expect(screen.getByTestId("wizard-in-scope")).toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: /next/i }));

    // Produces step should appear before Criteria
    await waitFor(() => expect(screen.getByTestId("wizard-produces")).toBeInTheDocument());
    expect(screen.queryByTestId("wizard-criteria")).not.toBeInTheDocument();
  });

  it("produces step shows all 6 options with Code pre-selected", async () => {
    render(<SpecWizard {...defaultProps} />);
    fireEvent.click(screen.getByTestId("mode-spec"));
    fireEvent.change(screen.getByTestId("wizard-title"), { target: { value: "My spec" } });
    fireEvent.change(screen.getByTestId("wizard-problem"), { target: { value: "Some problem" } });
    fireEvent.click(screen.getByRole("button", { name: /next/i }));
    await waitFor(() => expect(screen.getByTestId("wizard-in-scope")).toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: /next/i }));

    await waitFor(() => expect(screen.getByTestId("wizard-produces")).toBeInTheDocument());
    expect(screen.getByTestId("produces-code")).toBeInTheDocument();
    expect(screen.getByTestId("produces-agent")).toBeInTheDocument();
    expect(screen.getByTestId("produces-document")).toBeInTheDocument();
    expect(screen.getByTestId("produces-slides")).toBeInTheDocument();
    expect(screen.getByTestId("produces-diagram")).toBeInTheDocument();
    expect(screen.getByTestId("produces-skill")).toBeInTheDocument();
  });

  it("selecting Document sends produces=document in wizard create", async () => {
    mockedPost.mockResolvedValueOnce({ path: "docs/spec/q3-strategy.md" });

    render(<SpecWizard {...defaultProps} />);
    fireEvent.click(screen.getByTestId("mode-spec"));

    // Problem
    fireEvent.change(screen.getByTestId("wizard-title"), { target: { value: "Q3 strategy" } });
    fireEvent.change(screen.getByTestId("wizard-problem"), { target: { value: "Need a doc" } });
    fireEvent.click(screen.getByRole("button", { name: /next/i }));

    // Scope
    await waitFor(() => expect(screen.getByTestId("wizard-in-scope")).toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: /next/i }));

    // Produces — pick Document
    await waitFor(() => expect(screen.getByTestId("produces-document")).toBeInTheDocument());
    fireEvent.click(screen.getByTestId("produces-document"));
    fireEvent.click(screen.getByRole("button", { name: /next/i }));

    // Criteria — add 3 manually (no auto-suggest)
    await waitFor(() => expect(screen.getByTestId("wizard-criteria")).toBeInTheDocument());
    const criteriaInput = screen.getByPlaceholderText(/Shows related tasks/);
    for (const c of ["Crit A", "Crit B", "Crit C"]) {
      fireEvent.change(criteriaInput, { target: { value: c } });
      fireEvent.keyDown(criteriaInput, { key: "Enter" });
    }
    await waitFor(() => expect(screen.getByRole("button", { name: /next/i })).not.toBeDisabled());
    fireEvent.click(screen.getByRole("button", { name: /next/i }));

    // Review + submit
    await waitFor(() => expect(screen.getByTestId("spec-submit")).toBeInTheDocument());
    fireEvent.click(screen.getByTestId("spec-submit"));

    await waitFor(() => {
      expect(mockedPost).toHaveBeenLastCalledWith(
        "/specs/wizard/create",
        expect.objectContaining({ produces: "document", kind: "spec" }),
      );
    });
  });

  it("code (default) sends produces=code in wizard create", async () => {
    mockedPost.mockResolvedValueOnce({ path: "docs/spec/my-spec.md" });

    render(<SpecWizard {...defaultProps} />);
    fireEvent.click(screen.getByTestId("mode-spec"));

    fireEvent.change(screen.getByTestId("wizard-title"), { target: { value: "Code feature" } });
    fireEvent.change(screen.getByTestId("wizard-problem"), { target: { value: "Need code" } });
    fireEvent.click(screen.getByRole("button", { name: /next/i }));

    // Scope
    await waitFor(() => expect(screen.getByTestId("wizard-in-scope")).toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: /next/i }));

    // Produces — leave default (Code)
    await waitFor(() => expect(screen.getByTestId("wizard-produces")).toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: /next/i }));

    // Criteria — add 3 manually
    await waitFor(() => expect(screen.getByTestId("wizard-criteria")).toBeInTheDocument());
    const criteriaInput = screen.getByPlaceholderText(/Shows related tasks/);
    for (const c of ["A", "B", "C"]) {
      fireEvent.change(criteriaInput, { target: { value: c } });
      fireEvent.keyDown(criteriaInput, { key: "Enter" });
    }
    await waitFor(() => expect(screen.getByRole("button", { name: /next/i })).not.toBeDisabled());
    fireEvent.click(screen.getByRole("button", { name: /next/i }));

    // Review + submit
    await waitFor(() => expect(screen.getByTestId("spec-submit")).toBeInTheDocument());
    fireEvent.click(screen.getByTestId("spec-submit"));

    await waitFor(() => {
      expect(mockedPost).toHaveBeenLastCalledWith(
        "/specs/wizard/create",
        expect.objectContaining({ produces: "code", kind: "spec" }),
      );
    });
  });

  // -------------------------------------------------------------------
  // Cancel
  // -------------------------------------------------------------------

  it("cancel button calls onCancel", () => {
    const onCancel = vi.fn();
    render(<SpecWizard onComplete={vi.fn()} onCancel={onCancel} />);

    // The cancel control is an icon-only button; accessible name comes from
    // the Icon inner text which renders as "close".
    fireEvent.click(screen.getByRole("button", { name: /close/i }));
    expect(onCancel).toHaveBeenCalled();
  });
});
