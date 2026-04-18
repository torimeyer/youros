import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import ConfirmModal from "./ConfirmModal";

describe("ConfirmModal", () => {
  it("does not render when open is false", () => {
    render(
      <ConfirmModal
        open={false}
        title="Delete it?"
        message="This cannot be undone."
        onConfirm={vi.fn()}
        onCancel={vi.fn()}
      />
    );
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("renders title and message when open is true", () => {
    render(
      <ConfirmModal
        open={true}
        title="Delete it?"
        message="This cannot be undone."
        onConfirm={vi.fn()}
        onCancel={vi.fn()}
      />
    );
    expect(screen.getByText("Delete it?")).toBeInTheDocument();
    expect(screen.getByText("This cannot be undone.")).toBeInTheDocument();
  });

  it("uses custom confirmLabel and cancelLabel", () => {
    render(
      <ConfirmModal
        open={true}
        title="Remove template?"
        message="It will be gone."
        confirmLabel="Remove"
        cancelLabel="Never mind"
        onConfirm={vi.fn()}
        onCancel={vi.fn()}
      />
    );
    expect(screen.getByTestId("confirm-modal-confirm")).toHaveTextContent("Remove");
    expect(screen.getByTestId("confirm-modal-cancel")).toHaveTextContent("Never mind");
  });

  it("calls onConfirm when confirm button is clicked", () => {
    const onConfirm = vi.fn();
    render(
      <ConfirmModal
        open={true}
        title="Sure?"
        message="Yes or no."
        onConfirm={onConfirm}
        onCancel={vi.fn()}
      />
    );
    fireEvent.click(screen.getByTestId("confirm-modal-confirm"));
    expect(onConfirm).toHaveBeenCalledTimes(1);
  });

  it("calls onCancel when cancel button is clicked", () => {
    const onCancel = vi.fn();
    render(
      <ConfirmModal
        open={true}
        title="Sure?"
        message="Yes or no."
        onConfirm={vi.fn()}
        onCancel={onCancel}
      />
    );
    fireEvent.click(screen.getByTestId("confirm-modal-cancel"));
    expect(onCancel).toHaveBeenCalledTimes(1);
  });

  it("calls onConfirm when Enter is pressed", () => {
    const onConfirm = vi.fn();
    render(
      <ConfirmModal
        open={true}
        title="Sure?"
        message="Yes or no."
        onConfirm={onConfirm}
        onCancel={vi.fn()}
      />
    );
    fireEvent.keyDown(window, { key: "Enter" });
    expect(onConfirm).toHaveBeenCalledTimes(1);
  });

  it("calls onCancel when Escape is pressed", () => {
    const onCancel = vi.fn();
    render(
      <ConfirmModal
        open={true}
        title="Sure?"
        message="Yes or no."
        onConfirm={vi.fn()}
        onCancel={onCancel}
      />
    );
    fireEvent.keyDown(window, { key: "Escape" });
    expect(onCancel).toHaveBeenCalledTimes(1);
  });

  it("calls onCancel when backdrop is clicked", () => {
    const onCancel = vi.fn();
    render(
      <ConfirmModal
        open={true}
        title="Sure?"
        message="Yes or no."
        onConfirm={vi.fn()}
        onCancel={onCancel}
      />
    );
    fireEvent.click(screen.getByTestId("confirm-modal-backdrop"));
    expect(onCancel).toHaveBeenCalledTimes(1);
  });

  it("applies danger styling when danger is true", () => {
    render(
      <ConfirmModal
        open={true}
        title="Delete?"
        message="Cannot be undone."
        danger={true}
        onConfirm={vi.fn()}
        onCancel={vi.fn()}
      />
    );
    const btn = screen.getByTestId("confirm-modal-confirm");
    expect(btn.className).toMatch(/bg-red/);
  });
});
