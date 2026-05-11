"use client";

interface Props {
  armed: boolean;
  onToggle: (next: boolean) => void;
}

export function DryRunArmBanner({ armed, onToggle }: Props) {
  return (
    <div
      className="rounded-md border border-amber-200 bg-amber-50
        dark:bg-amber-950/30 dark:border-amber-900/50 px-3 py-2
        flex items-center justify-between"
      data-testid="dryrun-arm-banner"
    >
      <div className="text-xs text-amber-800 dark:text-amber-200">
        {armed
          ? "Dry-run is ON — the live runtime accepts orders but Kite responses are synthesised."
          : "Dry-run is OFF — arm it before starting a live-runtime rehearsal."}
      </div>
      <button
        type="button"
        onClick={() => onToggle(!armed)}
        className={`rounded-md px-3 py-1 text-xs font-medium ${
          armed
            ? "bg-amber-600 text-white hover:bg-amber-700"
            : "bg-amber-100 text-amber-900 hover:bg-amber-200"
        }`}
        data-testid="dryrun-arm-button"
      >
        {armed ? "Disarm dry-run" : "Arm dry-run"}
      </button>
    </div>
  );
}
