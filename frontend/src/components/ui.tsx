import type { ReactNode } from "react";
import type { Status } from "../types";

export function ProgressBar({ value, className = "" }: { value: number; className?: string }) {
  return (
    <div className={`h-1.5 w-full overflow-hidden rounded-full bg-line ${className}`}>
      <div
        className="h-full rounded-full bg-accent transition-[width] duration-500"
        style={{ width: `${Math.round(Math.min(1, Math.max(0, value)) * 100)}%` }}
      />
    </div>
  );
}

const STATUS_STYLE: Record<Status, string> = {
  queued: "bg-panel-2 text-muted",
  processing: "bg-accent/15 text-accent",
  done: "bg-ok/15 text-ok",
  error: "bg-danger/15 text-danger",
};
const STATUS_LABEL: Record<Status, string> = {
  queued: "Queued",
  processing: "Processing",
  done: "Ready",
  error: "Failed",
};

export function StatusBadge({ status }: { status: Status }) {
  return (
    <span className={`inline-flex items-center gap-1.5 rounded-full px-2.5 py-0.5 text-xs font-medium ${STATUS_STYLE[status]}`}>
      {status === "processing" && <span className="size-1.5 animate-pulse rounded-full bg-current" />}
      {STATUS_LABEL[status]}
    </span>
  );
}

export function scoreColor(score: number) {
  if (score >= 80) return "bg-accent text-accent-ink";
  if (score >= 65) return "bg-ok/90 text-accent-ink";
  if (score >= 50) return "bg-warn/90 text-accent-ink";
  return "bg-panel-2 text-text";
}

export function Field({ label, hint, children }: { label: string; hint?: string; children: ReactNode }) {
  return (
    <div className="space-y-2">
      <div className="flex items-baseline justify-between gap-2">
        <span className="text-sm font-medium">{label}</span>
        {hint && <span className="text-xs text-muted">{hint}</span>}
      </div>
      {children}
    </div>
  );
}

export function Segmented<T extends string | number>({
  value,
  options,
  onChange,
}: {
  value: T;
  options: { value: T; label: string }[];
  onChange: (v: T) => void;
}) {
  return (
    <div className="flex rounded-lg border border-line bg-bg p-1">
      {options.map((o) => (
        <button
          key={String(o.value)}
          type="button"
          onClick={() => onChange(o.value)}
          className={`flex-1 rounded-md px-2 py-1.5 text-sm transition ${
            value === o.value ? "bg-panel-2 font-medium text-text shadow" : "text-muted hover:text-text"
          }`}
        >
          {o.label}
        </button>
      ))}
    </div>
  );
}

export const inputClass =
  "w-full rounded-lg border border-line bg-bg px-3 py-2.5 text-sm outline-none transition placeholder:text-muted/60 focus:border-accent/60";
