/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        // ── Neutrals: theme-aware via CSS vars (RGB triples in index.css). The
        //    rgb(var(--c-…) / <alpha-value>) form keeps Tailwind /opacity modifiers
        //    (e.g. bg-panel2/40, border-edge/60) working across light + dark. ──
        base: "#0b0c10",
        panel: "rgb(var(--c-panel) / <alpha-value>)",
        panel2: "rgb(var(--c-panel2) / <alpha-value>)",
        edge: "rgb(var(--c-edge) / <alpha-value>)", // hairline / inner dividers (thick card border is var-driven)
        muted: "rgb(var(--c-muted) / <alpha-value>)",
        sub: "rgb(var(--c-sub) / <alpha-value>)",
        ink: "rgb(var(--c-ink) / <alpha-value>)", // primary text (replaces hardcoded text-slate-*)

        // ── Brand + signal (semantic tokens; vivid on both themes → stay fixed) ──
        brand: "#7C5CFF", // vlayer purple — primary / headline accent
        up: "#16c784", // gain green
        down: "#ea3943", // loss red
        cool: "#3861fb", // cool secondary accent
        violet: "#8b9dff", // on-chain identity
        vlayer: "#7C5CFF", // vlayer brand — verifiable-data proof layer (== primary brand accent)

        // ── Legacy names kept ALIVE (re-pointed) so existing utilities resolve ──
        neon: "#16c784", // → up   (NavCard/WeightsDonut text-neon, RationaleTicker border-neon …)
        cyan: "#3861fb", // → cool
        amber: "#f0b90b", // gold — warn / attention (semantic utility, not brand)
        danger: "#ea3943", // → down
      },
      fontFamily: {
        // Distinctive grotesque for headline numbers + labels; system fallbacks keep it offline-safe.
        display: ['"Space Grotesk"', "Inter", "ui-sans-serif", "system-ui", "-apple-system", '"Segoe UI"', "sans-serif"],
        sans: ["Inter", "ui-sans-serif", "system-ui", "-apple-system", '"Segoe UI"', "Roboto", "sans-serif"],
        mono: ['"Space Mono"', "ui-monospace", "SFMono-Regular", "Menlo", "monospace"],
      },
      borderWidth: {
        3: "3px", // the thick signature card border
      },
      borderRadius: {
        // Brutalist = near-zero corners. `full` kept so dots / gauges / progress bars stay round.
        none: "0px",
        sm: "2px",
        DEFAULT: "2px",
        md: "2px",
        lg: "3px",
        xl: "3px",
        "2xl": "4px",
        "3xl": "4px",
        full: "9999px",
      },
      boxShadow: {
        // Hard OFFSET shadows — the brutalist signature (colour is var-driven, theme-aware).
        brut: "4px 4px 0 0 var(--brut-shadow)",
        "brut-sm": "2px 2px 0 0 var(--brut-shadow)",
        "brut-lg": "6px 6px 0 0 var(--brut-shadow)",
      },
      keyframes: {
        pulseDot: {
          "0%,100%": { opacity: "1", transform: "scale(1)" },
          "50%": { opacity: "0.4", transform: "scale(0.85)" },
        },
      },
      animation: {
        pulseDot: "pulseDot 1.6s ease-in-out infinite",
      },
    },
  },
  plugins: [],
};
