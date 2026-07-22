/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        // ── Neutrals: theme-aware via CSS vars (RGB triples in index.css). The
        //    rgb(var(--c-…) / <alpha-value>) form keeps Tailwind /opacity modifiers
        //    (e.g. bg-panel2/40, border-edge/60) working across light + dark. ──
        base: "#0b0e14",
        panel: "rgb(var(--c-panel) / <alpha-value>)",
        panel2: "rgb(var(--c-panel2) / <alpha-value>)",
        edge: "rgb(var(--c-edge) / <alpha-value>)", // hairline card + inner dividers
        muted: "rgb(var(--c-muted) / <alpha-value>)",
        sub: "rgb(var(--c-sub) / <alpha-value>)",
        ink: "rgb(var(--c-ink) / <alpha-value>)", // primary text

        // ── Brand + signal (semantic tokens; vivid on both themes → stay fixed) ──
        brand: "#7C5CFF", // vlayer purple — primary / headline accent
        up: "#16b981", // gain green
        down: "#ef4444", // loss red
        cool: "#3861fb", // cool secondary accent
        violet: "#8b9dff", // on-chain identity
        vlayer: "#7C5CFF", // vlayer brand — verifiable-data proof layer (== primary brand accent)

        // ── Legacy names kept ALIVE (re-pointed) so existing utilities resolve ──
        neon: "#16b981", // → up
        cyan: "#3861fb", // → cool
        amber: "#f59e0b", // gold — warn / attention
        danger: "#ef4444", // → down
      },
      fontFamily: {
        // Clean fintech: Inter for text + display headings; Space Mono for ALL data/numerics
        // (NAV, PnL, %, addresses, hashes, tickers) — the quant-terminal signature.
        display: ["Inter", "ui-sans-serif", "system-ui", "-apple-system", '"Segoe UI"', "sans-serif"],
        sans: ["Inter", "ui-sans-serif", "system-ui", "-apple-system", '"Segoe UI"', "Roboto", "sans-serif"],
        mono: ['"Space Mono"', "ui-monospace", "SFMono-Regular", "Menlo", "monospace"],
      },
      borderWidth: {
        3: "3px", // legacy token kept alive (a few components reference border-3)
      },
      borderRadius: {
        // Soft fintech corners.
        none: "0px",
        sm: "6px",
        DEFAULT: "8px",
        md: "8px",
        lg: "12px",
        xl: "16px",
        "2xl": "20px",
        "3xl": "24px",
        full: "9999px",
      },
      boxShadow: {
        // Soft, layered fintech shadows (theme-aware via --card-shadow / --card-shadow-lg).
        brut: "var(--card-shadow)",
        "brut-sm": "var(--card-shadow-sm)",
        "brut-lg": "var(--card-shadow-lg)",
        card: "var(--card-shadow)",
        "card-lg": "var(--card-shadow-lg)",
      },
      keyframes: {
        pulseDot: {
          "0%,100%": { opacity: "1", transform: "scale(1)" },
          "50%": { opacity: "0.4", transform: "scale(0.85)" },
        },
        blink: {
          "0%,49%": { opacity: "1" },
          "50%,100%": { opacity: "0" },
        },
      },
      animation: {
        pulseDot: "pulseDot 1.6s ease-in-out infinite",
        blink: "blink 1.05s step-end infinite",
      },
    },
  },
  plugins: [],
};
