"use client";

import { useState } from "react";

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
 */
export function PanicCloseButton({ onConfirm }: Props) {
  const [open, setOpen] = useState(false);
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);

  const confirmable = text.trim() === "PANIC";

  async function handle() {
    setBusy(true);
    try {
      await onConfirm();
      setOpen(false);
      setText("");
    } finally {
      setBusy(false);
    }
  }

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
          onClick={() => !busy && setOpen(false)}
          data-testid="panic-close-modal"
        >
          <div
            className="w-[440px] rounded-lg bg-white dark:bg-slate-900 p-5 shadow-xl"
            onClick={(e) => e.stopPropagation()}
          >
            <h3 className="text-base font-semibold text-rose-700">
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
            <div className="mt-4 flex justify-end gap-2">
              <button
                type="button"
                data-testid="panic-close-cancel"
                onClick={() => setOpen(false)}
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
