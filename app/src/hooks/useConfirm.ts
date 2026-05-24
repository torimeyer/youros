import { useCallback, useRef, useState } from "react";

export interface ConfirmOptions {
  title: string;
  message: string;
  confirmLabel?: string;
  cancelLabel?: string;
  danger?: boolean;
}

export interface ConfirmState extends ConfirmOptions {
  open: boolean;
}

/**
 * useConfirm
 *
 * Returns a `confirm` function and modal props to spread onto <ConfirmModal>.
 *
 * Usage:
 *   const { confirm, confirmProps } = useConfirm();
 *   // mount once anywhere in the tree:
 *   <ConfirmModal {...confirmProps} />
 *   // call sites:
 *   if (!(await confirm({ title: 'Delete?', message: 'Cannot be undone.', danger: true }))) return;
 */
export function useConfirm() {
  const [state, setState] = useState<ConfirmState>({
    open: false,
    title: "",
    message: "",
  });

  // Resolver reference so we can resolve the promise from the button handlers.
  const resolveRef = useRef<((value: boolean) => void) | null>(null);

  const confirm = useCallback((opts: ConfirmOptions): Promise<boolean> => {
    return new Promise<boolean>((resolve) => {
      resolveRef.current = resolve;
      setState({ open: true, ...opts });
    });
  }, []);

  const handleConfirm = useCallback(() => {
    setState((s) => ({ ...s, open: false }));
    resolveRef.current?.(true);
    resolveRef.current = null;
  }, []);

  const handleCancel = useCallback(() => {
    setState((s) => ({ ...s, open: false }));
    resolveRef.current?.(false);
    resolveRef.current = null;
  }, []);

  const confirmProps = {
    open: state.open,
    title: state.title,
    message: state.message,
    confirmLabel: state.confirmLabel,
    cancelLabel: state.cancelLabel,
    danger: state.danger,
    onConfirm: handleConfirm,
    onCancel: handleCancel,
  };

  return { confirm, confirmProps };
}
