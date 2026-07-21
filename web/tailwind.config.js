/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{js,jsx}"],
  theme: {
    extend: {
      colors: {
        paper: "var(--paper)",
        bg: "var(--bg)",
        surface: "var(--surface)",
        "surface-2": "var(--surface-2)",
        line: "var(--line)",
        border: "var(--border)",
        "border-strong": "var(--border-strong)",
        ink: "var(--ink)",
        "ink-2": "var(--ink-2)",
        "ink-3": "var(--ink-3)",
        text: "var(--text)",
        "text-2": "var(--text-2)",
        "text-3": "var(--text-3)",
        accent: "var(--accent)",
        "accent-ink": "var(--accent-ink)",
        "accent-hover": "var(--accent-hover)",
        "accent-tint": "var(--accent-tint)",
        "accent-soft": "var(--accent-soft)",
        ok: "var(--ok)",
        "ok-tint": "var(--ok-tint)",
        warn: "var(--warn)",
        "warn-tint": "var(--warn-tint)",
        danger: "var(--danger)",
        "danger-tint": "var(--danger-tint)",
        err: "var(--err)",
        info: "var(--info)",
        "code-bg": "var(--code-bg)",
        "code-text": "var(--code-text)",
      },
      fontFamily: {
        sans: ["-apple-system", "BlinkMacSystemFont", "system-ui", "Inter Variable", "Inter", "Segoe UI", "sans-serif"],
        mono: ["JetBrains Mono Variable", "ui-monospace", "SFMono-Regular", "Menlo", "monospace"],
      },
      // Fixed instrument scale: 12, 13, 14, 16, 20, 24, 32, 40.
      fontSize: {
        xs: ["12px", "16px"],
        sm: ["13px", "18px"],
        base: ["14px", "20px"],
        lg: ["16px", "24px"],
        xl: ["20px", "28px"],
        "2xl": ["24px", "32px"],
        "3xl": ["32px", "40px"],
        "4xl": ["40px", "48px"],
      },
      borderRadius: {
        control: "12px",
        card: "20px",
        dialog: "24px",
      },
      spacing: {
        sidebar: "240px",
      },
      maxWidth: {
        canvas: "1280px",
      },
      boxShadow: {
        // Soft diffuse layers over a 1px border: cards rest, overlays float.
        card: "var(--shadow-card)",
        lift: "var(--shadow-lift)",
        popover: "var(--shadow-lift)",
        btn: "var(--shadow-btn)",
      },
      transitionTimingFunction: {
        "out-quart": "cubic-bezier(0.2, 0, 0, 1)",
      },
    },
  },
  plugins: [],
};
