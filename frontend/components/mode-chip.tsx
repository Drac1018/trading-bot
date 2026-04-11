import { formatDisplayValue } from "../lib/ui-copy";

export function ModeChip({ mode }: { mode: string }) {
  const color =
    mode === "paused" || mode === "hold"
      ? "border-risk/20 bg-risk/10 text-risk"
      : "border-gold/30 bg-gold/10 text-gold";

  return (
    <span
      className={`inline-flex items-center rounded-full border px-3 py-1.5 text-xs font-semibold tracking-[0.16em] ${color}`}
    >
      {formatDisplayValue(mode)}
    </span>
  );
}
