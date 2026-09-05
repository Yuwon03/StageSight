/** @type {import('tailwindcss').Config} */
module.exports = {
  content: [
    "./src/pages/**/*.{js,ts,jsx,tsx,mdx}",
    "./src/components/**/*.{js,ts,jsx,tsx,mdx}",
    "./src/app/**/*.{js,ts,jsx,tsx,mdx}",
  ],
  theme: {
    extend: {
      colors: {
        paper: "var(--paper)",
        card: "var(--card)",
        ink: {
          DEFAULT: "var(--ink)",
          soft: "var(--ink-soft)",
          faint: "var(--ink-faint)",
        },
        line: {
          DEFAULT: "var(--line)",
          strong: "var(--line-strong)",
        },
        teal: {
          DEFAULT: "var(--teal)",
          deep: "var(--teal-deep)",
          tint: "var(--teal-tint)",
          ink: "var(--teal-ink)",
        },
        amber: {
          DEFAULT: "var(--amber)",
          bright: "var(--amber-bright)",
          tint: "var(--amber-tint)",
        },
        signal: {
          red: "var(--red)",
          redTint: "var(--red-tint)",
          calc: "var(--calc)",
          calcTint: "var(--calc-tint)",
        },
      },
      fontFamily: {
        sans: ["var(--font-sans)", "system-ui", "sans-serif"],
        mono: ["var(--font-mono)", "ui-monospace", "monospace"],
      },
      transitionTimingFunction: {
        stage: "cubic-bezier(0.2, 0.7, 0.3, 1)",
      },
      transitionDuration: {
        fast: "180ms",
        base: "220ms",
        slow: "300ms",
      },
    },
  },
  plugins: [],
};
