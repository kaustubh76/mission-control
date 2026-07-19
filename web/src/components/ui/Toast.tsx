import { AnimatePresence, motion, useReducedMotion } from "framer-motion";
import { createContext, useCallback, useContext, useMemo, useRef, useState } from "react";
import type { ReactNode } from "react";

export type ToastSeverity = "error" | "warn" | "success" | "info";

interface ToastOpts {
  /** Stable id — pushing the same key replaces the existing toast (no stacking). */
  key?: string;
  title?: string;
  ttl?: number; // ms before auto-dismiss
}

interface Toast {
  id: string;
  severity: ToastSeverity;
  message: string;
  title?: string;
  ttl: number;
}

interface ToastApi {
  error: (message: string, opts?: ToastOpts) => void;
  warn: (message: string, opts?: ToastOpts) => void;
  success: (message: string, opts?: ToastOpts) => void;
  info: (message: string, opts?: ToastOpts) => void;
  dismiss: (id: string) => void;
}

const DEFAULT_TTL: Record<ToastSeverity, number> = {
  error: 8000,
  warn: 6000,
  success: 3500,
  info: 4500,
};

const STYLE: Record<ToastSeverity, { bar: string; icon: string; glyph: string }> = {
  error: { bar: "#ea3943", icon: "#ea3943", glyph: "✕" },
  warn: { bar: "#f0b90b", icon: "#f0b90b", glyph: "⚠" },
  success: { bar: "#16c784", icon: "#16c784", glyph: "✓" },
  info: { bar: "#3861fb", icon: "#6e8bff", glyph: "ℹ" },
};

const MAX_VISIBLE = 4;

const ToastContext = createContext<ToastApi | null>(null);

/** Imperative toast API. Throws if used outside <ToastProvider> (a wiring bug, not a runtime path). */
export function useToast(): { toast: ToastApi } {
  const ctx = useContext(ToastContext);
  if (!ctx) throw new Error("useToast must be used within <ToastProvider>");
  return { toast: ctx };
}

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([]);
  const seq = useRef(0);

  const dismiss = useCallback((id: string) => {
    setToasts((prev) => prev.filter((t) => t.id !== id));
  }, []);

  const push = useCallback((severity: ToastSeverity, message: string, opts?: ToastOpts) => {
    const id = opts?.key ?? `t${(seq.current += 1)}`;
    const toast: Toast = {
      id,
      severity,
      message,
      title: opts?.title,
      ttl: opts?.ttl ?? DEFAULT_TTL[severity],
    };
    setToasts((prev) => {
      // Replace a same-key toast (dedupe); else append and cap the stack.
      const without = prev.filter((t) => t.id !== id);
      const next = [...without, toast];
      return next.length > MAX_VISIBLE ? next.slice(next.length - MAX_VISIBLE) : next;
    });
  }, []);

  const api = useMemo<ToastApi>(
    () => ({
      error: (m, o) => push("error", m, o),
      warn: (m, o) => push("warn", m, o),
      success: (m, o) => push("success", m, o),
      info: (m, o) => push("info", m, o),
      dismiss,
    }),
    [push, dismiss],
  );

  return (
    <ToastContext.Provider value={api}>
      {children}
      <ToastViewport toasts={toasts} onDismiss={dismiss} />
    </ToastContext.Provider>
  );
}

function ToastViewport({ toasts, onDismiss }: { toasts: Toast[]; onDismiss: (id: string) => void }) {
  const reduce = useReducedMotion();
  return (
    <div
      aria-live="polite"
      aria-atomic="false"
      className="pointer-events-none fixed right-3 top-3 z-50 flex w-[min(92vw,360px)] flex-col gap-2"
    >
      <AnimatePresence initial={false}>
        {toasts.map((t) => (
          <ToastRow key={t.id} toast={t} reduce={!!reduce} onDismiss={onDismiss} />
        ))}
      </AnimatePresence>
    </div>
  );
}

function ToastRow({ toast, reduce, onDismiss }: { toast: Toast; reduce: boolean; onDismiss: (id: string) => void }) {
  const s = STYLE[toast.severity];
  const timer = useRef<number | undefined>(undefined);

  const arm = useCallback(() => {
    window.clearTimeout(timer.current);
    timer.current = window.setTimeout(() => onDismiss(toast.id), toast.ttl);
  }, [onDismiss, toast.id, toast.ttl]);

  // Auto-dismiss; pause while hovered. The ref callback arms on mount.
  const onMount = useCallback(
    (el: HTMLDivElement | null) => {
      if (el) arm();
      else window.clearTimeout(timer.current);
    },
    [arm],
  );

  return (
    <motion.div
      ref={onMount}
      layout={!reduce}
      initial={reduce ? false : { opacity: 0, x: 24, scale: 0.98 }}
      animate={{ opacity: 1, x: 0, scale: 1 }}
      exit={reduce ? { opacity: 0 } : { opacity: 0, x: 24, scale: 0.98 }}
      transition={{ duration: 0.22, ease: "easeOut" }}
      onMouseEnter={() => window.clearTimeout(timer.current)}
      onMouseLeave={arm}
      role={toast.severity === "error" || toast.severity === "warn" ? "alert" : "status"}
      className="pointer-events-auto flex items-start gap-2.5 rounded-sm border-3 bg-panel2 py-2 pl-2.5 pr-2 shadow-brut-sm"
      style={{ borderColor: "var(--thick-line)", borderLeft: `4px solid ${s.bar}` }}
    >
      <span className="mt-px font-mono text-sm font-bold leading-none" style={{ color: s.icon }} aria-hidden>
        {s.glyph}
      </span>
      <div className="min-w-0 flex-1">
        {toast.title && <div className="text-[11px] font-bold uppercase tracking-wide text-ink">{toast.title}</div>}
        <div className="text-[12px] leading-snug text-sub">{toast.message}</div>
      </div>
      <button
        onClick={() => onDismiss(toast.id)}
        aria-label="dismiss"
        className="shrink-0 rounded-sm px-1 text-muted transition hover:text-ink focus:outline-none focus-visible:ring-2 focus-visible:ring-brand/60"
      >
        ✕
      </button>
    </motion.div>
  );
}
