import { useCallback, useEffect, useState } from "react";

export type Theme = "dark" | "light";
const KEY = "mc.theme";

/** Resolve the boot theme: stored choice → system preference → dark default.
 * Mirrors the inline <head> script in index.html (which sets it before paint). */
export function initialTheme(): Theme {
  try {
    // ?theme=light|dark deep-link override (one-shot for this load; persisted only on toggle).
    const q = new URLSearchParams(location.search).get("theme");
    if (q === "light" || q === "dark") return q;
    const saved = localStorage.getItem(KEY);
    if (saved === "light" || saved === "dark") return saved;
  } catch {
    /* storage blocked — fall through */
  }
  if (typeof matchMedia !== "undefined" && matchMedia("(prefers-color-scheme: light)").matches) {
    return "light";
  }
  return "dark";
}

function apply(theme: Theme) {
  document.documentElement.dataset.theme = theme;
}

/** Dark/light theme state, persisted to localStorage and reflected on <html data-theme>. */
export function useTheme(): { theme: Theme; toggle: () => void } {
  const [theme, setTheme] = useState<Theme>(initialTheme);

  useEffect(() => {
    apply(theme);
    try {
      localStorage.setItem(KEY, theme);
    } catch {
      /* ignore */
    }
  }, [theme]);

  const toggle = useCallback(() => setTheme((t) => (t === "dark" ? "light" : "dark")), []);
  return { theme, toggle };
}
