"use client";
/**
 * Modal for adding / editing a user's BYO provider key.
 *
 * Validates the key prefix (Groq → gsk_, Anthropic → sk-ant-)
 * client-side before calling :hook:`useUserLLMKeys.saveKey`.
 * The plaintext key never leaves this component — only the
 * masked form returned by the server is ever rendered.
 */

import { useEffect, useState } from "react";

interface Props {
  open: boolean;
  provider: "groq" | "anthropic";
  existingLabel?: string | null;
  onSave: (key: string, label: string | null) => Promise<void>;
  onClose: () => void;
}

const PROVIDER_LABEL: Record<
  "groq" | "anthropic",
  { name: string; prefix: string; help: string }
> = {
  groq: {
    name: "Groq",
    prefix: "gsk_",
    help:
      "Paste your Groq API key. Find it at console.groq.com/keys.",
  },
  anthropic: {
    name: "Anthropic",
    prefix: "sk-ant-",
    help:
      "Paste your Anthropic key. Generate one at console.anthropic.com/settings/keys.",
  },
};

export function ConfigureProviderKeyModal({
  open,
  provider,
  existingLabel,
  onSave,
  onClose,
}: Props) {
  const meta = PROVIDER_LABEL[provider];
  const [keyValue, setKeyValue] = useState("");
  const [label, setLabel] = useState(existingLabel || "");
  const [showPlain, setShowPlain] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (open) {
      setKeyValue("");
      setLabel(existingLabel || "");
      setShowPlain(false);
      setError(null);
    }
  }, [open, existingLabel]);

  if (!open) return null;

  const trimmed = keyValue.trim();
  const prefixOk = trimmed.startsWith(meta.prefix);
  const canSave = trimmed.length >= 8 && prefixOk && !saving;

  const handleSave = async () => {
    setSaving(true);
    setError(null);
    try {
      await onSave(trimmed, label.trim() || null);
      onClose();
    } catch (e) {
      setError(
        e instanceof Error ? e.message : "Save failed",
      );
    } finally {
      setSaving(false);
    }
  };

  return (
    <div
      className="fixed inset-0 z-[70] flex items-center
        justify-center bg-black/50 p-4"
      onClick={onClose}
    >
      <div
        className="w-full max-w-md rounded-2xl bg-white
          p-6 shadow-2xl dark:bg-gray-900"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="mb-4 flex items-center justify-between">
          <h3
            className="text-lg font-semibold
              text-gray-900 dark:text-gray-100"
          >
            Configure {meta.name} key
          </h3>
          <button
            type="button"
            onClick={onClose}
            className="text-gray-400 hover:text-gray-600
              dark:hover:text-gray-300"
          >
            ✕
          </button>
        </div>

        <p
          className="mb-4 text-sm text-gray-600
            dark:text-gray-400"
        >
          {meta.help}
        </p>

        <label
          className="mb-1 block text-xs font-medium
            text-gray-700 dark:text-gray-300"
        >
          API key
        </label>
        <div className="relative mb-1">
          <input
            type={showPlain ? "text" : "password"}
            autoComplete="off"
            spellCheck={false}
            placeholder={`${meta.prefix}…`}
            value={keyValue}
            onChange={(e) => setKeyValue(e.target.value)}
            className="w-full rounded-lg border
              border-gray-300 bg-white px-3 py-2
              pr-16 text-sm text-gray-900
              dark:border-gray-700 dark:bg-gray-800
              dark:text-gray-100"
          />
          <button
            type="button"
            onClick={() => setShowPlain((v) => !v)}
            className="absolute right-2 top-1/2
              -translate-y-1/2 text-xs font-medium
              text-indigo-600 hover:text-indigo-700
              dark:text-indigo-400"
          >
            {showPlain ? "Hide" : "Show"}
          </button>
        </div>
        {trimmed.length > 0 && !prefixOk && (
          <p className="mb-3 text-xs text-red-600">
            {meta.name} keys should start with{" "}
            <code>{meta.prefix}</code>.
          </p>
        )}

        <label
          className="mb-1 mt-3 block text-xs font-medium
            text-gray-700 dark:text-gray-300"
        >
          Label (optional)
        </label>
        <input
          type="text"
          placeholder="e.g. Personal, Work"
          maxLength={120}
          value={label}
          onChange={(e) => setLabel(e.target.value)}
          className="w-full rounded-lg border
            border-gray-300 bg-white px-3 py-2 text-sm
            text-gray-900 dark:border-gray-700
            dark:bg-gray-800 dark:text-gray-100"
        />

        {error && (
          <p className="mt-3 text-sm text-red-600">
            {error}
          </p>
        )}

        <div className="mt-5 flex justify-end gap-2">
          <button
            type="button"
            onClick={onClose}
            className="rounded-lg px-4 py-2 text-sm
              font-medium text-gray-600 hover:bg-gray-100
              dark:text-gray-300
              dark:hover:bg-gray-800"
          >
            Cancel
          </button>
          <button
            type="button"
            disabled={!canSave}
            onClick={handleSave}
            className="rounded-lg bg-indigo-600 px-4 py-2
              text-sm font-semibold text-white shadow-sm
              hover:bg-indigo-700 disabled:cursor-not-allowed
              disabled:opacity-50"
          >
            {saving ? "Saving…" : "Save key"}
          </button>
        </div>
      </div>
    </div>
  );
}
