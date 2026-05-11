"use client";

import { useEffect, useState } from "react";

interface Props {
  onConfirm: () => Promise<void> | void;
}

/**
 * PanicCloseButton — last-resort "close everything" trigger.
 *
 * Renders a rose button that opens a modal requiring the user to
 * type the literal string `PANIC` before the confirm button
 * un-disables. {@link onConfirm} is invoked exactly once per
 * confirmation; the modal is responsible only for the typed-gate.
 *
 * On `onConfirm` rejection (HTTP 5xx, network failure) the error
 * is surfaced inline in rose-700; the modal stays open so the
 * trader can retry. ESC dismisses (unless mid-flight).
 */
export function PanicCloseButton({ onConfirm }: Props) {
  const [open, setOpen] = useState(false);
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [errMsg, setErrMsg] = useState<string | null>(null);

  const confirmable = text.trim() === "PANIC";

  async function handle() {
    setBusy(true);
    setErrMsg(null);
    try {
      await onConfirm();
      setOpen(false);
      setText("");
    } catch (e) {
      setErrMsg(e instanceof Error ? e.message : "Panic close failed");
    } finally {
      setBusy(false);
    }
  }

  function cancel() {
    setOpen(false);
    setText("");
    setErrMsg(null);
  }

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape" && !busy) {
        cancel();
      }
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [open, busy]);

  return (
    <>
      <button
        type="button"
        data-testid="panic-close-button"
        onClick={() => setOpen(true)}
        className="rounded-md bg-rose-600 px-3 py-1.5 text-xs
          font-semibold text-white hover:bg-rose-700"
      >
        PANIC CLOSE
      </button>
      {open && (
        <div
          className="fixed inset-0 z-[70] flex items-center
            justify-center bg-black/40"
          onClick={() => !busy && cancel()}
          data-testid="panic-close-modal"
          role="dialog"
          aria-modal="true"
          aria-labelledby="panic-dialog-title"
        >
          <div
            className="w-[440px] rounded-lg bg-white dark:bg-slate-900 p-5 shadow-xl"
            onClick={(e) => e.stopPropagation()}
          >
            <h3
              id="panic-dialog-title"
              className="text-base font-semibold text-rose-700"
            >
              Close all open positions?
            </h3>
            <p className="mt-2 text-xs text-slate-600 dark:text-slate-300">
              This will submit market-close orders for every
              algo-opened position via Kite. Type{" "}
              <code className="font-mono">PANIC</code> to confirm.
            </p>
            <input
              data-testid="panic-close-input"
              value={text}
              onChange={(e) => setText(e.target.value)}
              className="mt-3 w-full rounded border border-slate-300
                dark:border-slate-600 bg-white dark:bg-slate-800
                px-2 py-1 text-sm"
              placeholder="Type PANIC"
              autoFocus
            />
            {errMsg && (
              <p
                className="mt-2 text-xs text-rose-700"
                data-testid="panic-close-error"
              >
                {errMsg}
              </p>
            )}
            <div className="mt-4 flex justify-end gap-2">
              <button
                type="button"
                data-testid="panic-close-cancel"
                onClick={cancel}
                disabled={busy}
                className="rounded-md px-3 py-1.5 text-xs text-slate-600 hover:bg-slate-100 dark:hover:bg-slate-800"
              >
                Cancel
              </button>
              <button
                type="button"
                data-testid="panic-close-confirm"
                onClick={handle}
                disabled={!confirmable || busy}
                className="rounded-md bg-rose-600 px-3 py-1.5 text-xs
                  font-semibold text-white hover:bg-rose-700
                  disabled:opacity-50 disabled:cursor-not-allowed"
              >
                {busy ? "Closing…" : "Close all"}
              </button>
            </div>
          </div>
        </div>
      )}
    </>
  );
}
