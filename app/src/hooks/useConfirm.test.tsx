import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent, act } from "@testing-library/react";
import { useConfirm } from "./useConfirm";
import ConfirmModal from "../components/ConfirmModal";

// Harness component that wires useConfirm + ConfirmModal together.
function TestHarness({
  onResult,
}: {
  onResult: (value: boolean) => void;
}) {
  const { confirm, confirmProps } = useConfirm();

  const handleTrigger = async () => {
    const result = await confirm({
      title: "Are you sure?",
      message: "This cannot be undone.",
      confirmLabel: "Yes, do it",
      danger: true,
    });
    onResult(result);
  };

  return (
    <>
      <button data-testid="trigger" onClick={handleTrigger}>
        Trigger
      </button>
      <ConfirmModal {...confirmProps} />
    </>
  );
}

describe("useConfirm", () => {
  it("resolves true when the confirm button is clicked", async () => {
    const onResult = vi.fn();
    render(<TestHarness onResult={onResult} />);

    // Open the dialog.
    await act(async () => {
      fireEvent.click(screen.getByTestId("trigger"));
    });

    expect(screen.getByText("Are you sure?")).toBeInTheDocument();

    // Click confirm.
    await act(async () => {
      fireEvent.click(screen.getByTestId("confirm-modal-confirm"));
    });

    expect(onResult).toHaveBeenCalledWith(true);
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("resolves false when the cancel button is clicked", async () => {
    const onResult = vi.fn();
    render(<TestHarness onResult={onResult} />);

    await act(async () => {
      fireEvent.click(screen.getByTestId("trigger"));
    });

    await act(async () => {
      fireEvent.click(screen.getByTestId("confirm-modal-cancel"));
    });

    expect(onResult).toHaveBeenCalledWith(false);
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("resolves false when Escape is pressed", async () => {
    const onResult = vi.fn();
    render(<TestHarness onResult={onResult} />);

    await act(async () => {
      fireEvent.click(screen.getByTestId("trigger"));
    });

    await act(async () => {
      fireEvent.keyDown(window, { key: "Escape" });
    });

    expect(onResult).toHaveBeenCalledWith(false);
  });

  it("resolves true when Enter is pressed", async () => {
    const onResult = vi.fn();
    render(<TestHarness onResult={onResult} />);

    await act(async () => {
      fireEvent.click(screen.getByTestId("trigger"));
    });

    await act(async () => {
      fireEvent.keyDown(window, { key: "Enter" });
    });

    expect(onResult).toHaveBeenCalledWith(true);
  });
});
