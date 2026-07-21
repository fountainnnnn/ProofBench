import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { applyTheme, storedTheme } from "../theme.js";
import { useProviderReadiness } from "../useProviderReadiness.js";

/* The shared right-hand cluster of every console page header: live run
   readiness (deep-linking to Settings) and the theme toggle. Page-specific
   primary actions render beside it, passed as children. */

function effectiveTheme() {
  const pinned = storedTheme();
  if (pinned === "dark" || pinned === "light") return pinned;
  return typeof window.matchMedia === "function" &&
    window.matchMedia("(prefers-color-scheme: dark)").matches
    ? "dark"
    : "light";
}

function ThemeToggle() {
  const [theme, setTheme] = useState(effectiveTheme);

  useEffect(() => {
    const sync = () => setTheme(effectiveTheme());
    window.addEventListener("pb-theme-change", sync);
    let mql;
    if (typeof window.matchMedia === "function") {
      mql = window.matchMedia("(prefers-color-scheme: dark)");
      mql.addEventListener("change", sync);
    }
    return () => {
      window.removeEventListener("pb-theme-change", sync);
      mql?.removeEventListener("change", sync);
    };
  }, []);

  const dark = theme === "dark";
  return (
    <button
      type="button"
      onClick={() => applyTheme(dark ? "light" : "dark")}
      aria-label={dark ? "Switch to light theme" : "Switch to dark theme"}
      title={dark ? "Switch to light theme" : "Switch to dark theme"}
      className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full text-[var(--ink-2)] transition-colors duration-150 hover:bg-[var(--surface-2)] hover:text-[var(--ink)] focus-visible:outline-none focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[var(--accent)]"
    >
      {dark ? (
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
          <circle cx="12" cy="12" r="4" />
          <path d="M12 2v2" />
          <path d="M12 20v2" />
          <path d="m4.9 4.9 1.4 1.4" />
          <path d="m17.7 17.7 1.4 1.4" />
          <path d="M2 12h2" />
          <path d="M20 12h2" />
          <path d="m4.9 19.1 1.4-1.4" />
          <path d="m17.7 6.3 1.4-1.4" />
        </svg>
      ) : (
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
          <path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8Z" />
        </svg>
      )}
    </button>
  );
}

function ReadinessChip() {
  const { readiness } = useProviderReadiness();
  if (!readiness) return null;
  const ready = readiness.run_ready === true;
  return (
    <Link
      to="/app/settings"
      title={
        ready
          ? "All essential providers are configured. Open Settings for details."
          : "A required provider is not configured. Open Settings to fix it."
      }
      className={`hidden shrink-0 items-center gap-1.5 rounded-full px-3 py-1.5 text-[12px] font-medium transition-colors duration-150 sm:inline-flex ${
        ready
          ? "bg-[var(--ok-tint)] text-[var(--ok)] hover:bg-[color-mix(in_oklab,var(--ok)_10%,var(--ok-tint))]"
          : "bg-[var(--warn-tint)] text-[var(--warn)] hover:bg-[color-mix(in_oklab,var(--warn)_10%,var(--warn-tint))]"
      }`}
    >
      <span aria-hidden="true" className="h-1.5 w-1.5 rounded-full bg-current" />
      {ready ? "Ready to run" : "Setup needed"}
    </Link>
  );
}

export default function HeaderActions({ children, showReadiness = true }) {
  return (
    <div className="flex shrink-0 items-center gap-2">
      {showReadiness && <ReadinessChip />}
      <ThemeToggle />
      {children}
    </div>
  );
}
