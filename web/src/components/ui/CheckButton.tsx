import { useEffect, useRef, useState } from "react";

export interface CheckResult {
  ok: boolean;
  detail?: string;
  /** Optional 3-state verdict: distinguishes a benign config "off" (warn/amber) from a real
   * error (fail/red). Defaults to ok→ok / !ok→fail when omitted (backward-compatible). */
  level?: "ok" | "warn" | "fail";
}

const LEVEL_STYLE: Record<"ok" | "warn" | "fail", { cls: string; glyph: string }> = {
  ok: { cls: "border-neon/50 bg-neon/10 text-neon", glyph: "✓" },
  warn: { cls: "border-amber/50 bg-amber/10 text-amber", glyph: "!" },
  fail: { cls: "border-danger/50 bg-danger/10 text-danger", glyph: "✗" },
};

/**
 * A per-panel self-test affordance: click → runs a real API probe → shows a
 * transient ✓/✗ verdict with a one-line detail, then fades back to idle.
 * Compact ControlPanel visual language; never throws (failures become ✗).
 */
export default function CheckButton({
  label,
  run,
  disabled = false,
  className = "",
}: {
  label: string;
  run: () => Promise<CheckResult>;
  disabled?: boolean;
  className?: string;
}) {
  const [state, setState] = useState<"idle" | "busy" | "done">("idle");
  const [result, setResult] = useState<CheckResult | null>(null);
  const timer = useRef<number | undefined>(undefined);

  useEffect(() => () => window.clearTimeout(timer.current), []);

  async function onClick() {
    if (state === "busy") return;
    window.clearTimeout(timer.current);
    setState("busy");
    setResult(null);
    let r: CheckResult;
    try {
      r = await run();
    } catch (e) {
      r = { ok: false, detail: e instanceof Error ? e.message : "request failed" };
    }
    setResult(r);
    setState("done");
    timer.current = window.setTimeout(() => {
      setState("idle");
      setResult(null);
    }, 4000);
  }

  if (state === "done" && result) {
    const lvl = result.level ?? (result.ok ? "ok" : "fail");
    const s = LEVEL_STYLE[lvl];
    return (
      <span
        className={`inline-flex max-w-full items-center gap-1 rounded-sm border px-2 py-0.5 font-mono text-[10px] ${s.cls} ${className}`}
        role="status"
      >
        <span className="font-bold">{s.glyph}</span>
        <span className="truncate">{result.detail ?? (result.ok ? "ok" : lvl === "warn" ? "off" : "failed")}</span>
      </span>
    );
  }

  return (
    <button
      onClick={onClick}
      disabled={disabled || state === "busy"}
      aria-busy={state === "busy"}
      className={`inline-flex items-center gap-1 rounded-sm border border-edge bg-panel2 px-2 py-0.5 font-display text-[10px] font-bold uppercase tracking-wide text-sub transition hover:border-cyan/60 hover:text-cyan focus:outline-none focus-visible:ring-2 focus-visible:ring-brand/60 disabled:cursor-not-allowed disabled:opacity-40 ${className}`}
    >
      {state === "busy" ? (
        <span className="animate-pulse">checking…</span>
      ) : (
        <>
          <span aria-hidden>⟳</span> {label}
        </>
      )}
    </button>
  );
}
