import { useEffect, useRef, useState } from "react";

/** Copy-to-clipboard with a transient "copied" confirmation. Renders nothing
 * when the Clipboard API is unavailable (http / old browsers) — the value it
 * mirrors is still visible as text, so nothing is lost. */
export default function CopyButton({ text, label = "copy" }: { text: string; label?: string }) {
  const [copied, setCopied] = useState(false);
  const timer = useRef<number | undefined>(undefined);

  useEffect(() => () => window.clearTimeout(timer.current), []);

  if (typeof navigator === "undefined" || !navigator.clipboard) return null;

  async function onCopy() {
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
      window.clearTimeout(timer.current);
      timer.current = window.setTimeout(() => setCopied(false), 1500);
    } catch {
      /* clipboard refused (permissions) — stay silent, the text is on screen */
    }
  }

  return (
    <button
      onClick={onCopy}
      aria-label={`${label}: ${text}`}
      title={copied ? "copied" : label}
      className={`rounded-sm border px-1 py-px font-mono text-[9px] uppercase tracking-wide transition focus:outline-none focus-visible:ring-1 focus-visible:ring-cyan/60 ${
        copied
          ? "border-neon/60 text-neon"
          : "border-edge text-muted hover:border-cyan/60 hover:text-cyan"
      }`}
    >
      {copied ? "✓ copied" : label}
    </button>
  );
}
