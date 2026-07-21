import { useEffect, useState } from "react";
import { NavLink, Outlet } from "react-router-dom";
import { AUTH_CHANGE_EVENT, bootstrapAuthSession, listSessions } from "../api.js";
import Logo from "./Logo.jsx";
import { BTN_SECONDARY } from "./ui.jsx";

/* One consistent stroke family for navigation icons: 24px box, 1.5 stroke,
   round caps, currentColor. */
function NavIcon({ children }) {
  return (
    <svg
      width="16"
      height="16"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.5"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      className="shrink-0"
    >
      {children}
    </svg>
  );
}

const NAV = [
  {
    to: "/app/benchmark",
    label: "Benchmark",
    icon: (
      <NavIcon>
        <path d="M9 3h6" />
        <path d="M10 3v5.3L4.9 16.9A2 2 0 0 0 6.7 20h10.6a2 2 0 0 0 1.8-3.1L14 8.3V3" />
        <path d="M8.5 14h7" />
      </NavIcon>
    ),
  },
  {
    to: "/app/runs",
    label: "Runs",
    icon: (
      <NavIcon>
        <circle cx="12" cy="12" r="9" />
        <path d="M12 7v5l3.5 2" />
      </NavIcon>
    ),
  },
  {
    to: "/app/datasets",
    label: "Datasets",
    icon: (
      <NavIcon>
        <ellipse cx="12" cy="5.5" rx="8" ry="2.5" />
        <path d="M4 5.5v13c0 1.4 3.6 2.5 8 2.5s8-1.1 8-2.5v-13" />
        <path d="M4 12c0 1.4 3.6 2.5 8 2.5s8-1.1 8-2.5" />
      </NavIcon>
    ),
  },
  {
    to: "/app/settings",
    label: "Settings",
    icon: (
      <NavIcon>
        <path d="M3 7.5h11" />
        <circle cx="16.5" cy="7.5" r="2" />
        <path d="M18.5 7.5H21" />
        <path d="M3 16.5h3" />
        <circle cx="10.5" cy="16.5" r="2" />
        <path d="M12.5 16.5H21" />
      </NavIcon>
    ),
  },
];

const SKIP_LINK =
  "sr-only z-50 rounded-[12px] bg-[var(--surface)] px-3 py-2 text-[13px] " +
  "font-medium text-[var(--accent)] shadow-lift focus:not-sr-only focus:fixed focus:left-4 focus:top-4";

/* Healthy deployments say nothing. Only an outage earns chrome. */
function ServerStatus() {
  const [up, setUp] = useState(null);

  useEffect(() => {
    let cancelled = false;
    const check = async () => {
      try {
        await listSessions();
        if (!cancelled) setUp(true);
      } catch {
        if (!cancelled) setUp(false);
      }
    };
    check();
    const t = setInterval(check, 10000);
    return () => {
      cancelled = true;
      clearInterval(t);
    };
  }, []);

  if (up !== false) return null;

  return (
    <span
      className="fixed right-4 top-4 z-40 inline-flex items-center gap-1.5 rounded-full bg-[var(--danger-tint)] px-3 py-1.5 text-[12px] font-medium text-[var(--danger)] shadow-[var(--shadow-lift)]"
      role="status"
    >
      <span className="h-1.5 w-1.5 shrink-0 rounded-full bg-current" aria-hidden="true" />
      Server unreachable
    </span>
  );
}

/* The console is local-profile only. There is no credential the browser could
   collect, so an authenticated or unreachable deployment gets a static,
   honest notice — never an input that implies a token would help. */
function ConsoleUnavailable({ onRetry, busy }) {
  return (
    <main className="pb-shell-bg pb-viewport-min pb-safe-screen pb-safe-inline-shell flex items-center justify-center px-4 text-[var(--ink)]">
      <section
        className="w-full max-w-[26rem] rounded-[24px] bg-[var(--surface)] p-8 shadow-lift"
        aria-labelledby="console-unavailable-title"
      >
        <Logo />
        <h1 id="console-unavailable-title" className="mt-6 text-[24px] font-semibold tracking-[-0.01em]">
          Console unavailable
        </h1>
        <p className="mt-2 text-[13px] leading-relaxed text-[var(--ink-2)]">
          The browser console runs only against a local ProofBench profile. This
          deployment either requires API authentication or could not be reached,
          and neither is something a browser sign-in can resolve. Use the API
          directly with a bearer token, or start a local profile.
        </p>
        <button type="button" onClick={onRetry} disabled={busy} className={`${BTN_SECONDARY} mt-6 h-10 w-full`}>
          {busy ? "Checking" : "Retry"}
        </button>
      </section>
    </main>
  );
}

export default function Shell() {
  const [authState, setAuthState] = useState("checking");

  useEffect(() => {
    let active = true;
    bootstrapAuthSession()
      .then((session) => { if (active) setAuthState(session?.localMode ? "local" : "unavailable"); })
      .catch(() => { if (active) setAuthState("unavailable"); });
    const onAuthChange = (event) => {
      setAuthState(event.detail?.localMode ? "local" : "unavailable");
    };
    window.addEventListener(AUTH_CHANGE_EVENT, onAuthChange);
    return () => {
      active = false;
      window.removeEventListener(AUTH_CHANGE_EVENT, onAuthChange);
    };
  }, []);

  if (authState === "checking") {
    return (
      <div
        className="pb-shell-bg pb-viewport-min pb-safe-screen pb-safe-inline-shell flex items-center justify-center text-[13px] text-[var(--ink-2)]"
        role="status"
      >
        Checking access
      </div>
    );
  }
  if (authState !== "local") {
    return (
      <ConsoleUnavailable
        busy={authState === "retrying"}
        onRetry={() => {
          setAuthState("retrying");
          bootstrapAuthSession()
            .then((session) => setAuthState(session?.localMode ? "local" : "unavailable"))
            .catch(() => setAuthState("unavailable"));
        }}
      />
    );
  }

  const navItem = ({ isActive }) =>
    `pb-nav-item flex min-h-11 items-center gap-2 px-2.5 text-[13px] ${
      isActive
        ? "pb-nav-item-active font-medium"
        : "text-[var(--ink-2)] hover:bg-[var(--surface-2)] hover:text-[var(--ink)]"
    }`;

  return (
    <div className="pb-shell-bg pb-viewport-height pb-safe-inline-shell flex w-full overflow-hidden text-[var(--ink)]">
      <a href="#proofbench-main" className={SKIP_LINK}>
        Skip to main content
      </a>

      <aside aria-label="Primary navigation" className="pb-sidebar-shell hidden w-[248px] shrink-0 flex-col border-r border-[var(--line)] md:flex">
        <div className="flex h-14 shrink-0 items-center px-5">
          <Logo />
        </div>
        <nav className="flex-1 overflow-y-auto px-3" aria-label="Console">
          <div className="flex flex-col gap-1">
            {NAV.map((item) => (
              <NavLink key={item.to} to={item.to} className={navItem}>
                {item.icon}
                {item.label}
              </NavLink>
            ))}
          </div>
        </nav>
        <div className="border-t border-[var(--line)] p-3">
          <div className="flex items-center gap-2.5 px-2.5 py-1.5">
            <span
              aria-hidden="true"
              className="flex h-7 w-7 shrink-0 items-center justify-center rounded-[8px] bg-[var(--surface-2)] text-[12px] font-medium text-[var(--ink-2)]"
            >
              L
            </span>
            <span className="min-w-0">
              <span className="block truncate text-[13px] font-medium text-[var(--ink)]">
                Local operator
              </span>
              <span className="block truncate text-[12px] text-[var(--ink-3)]">
                Local mode
              </span>
            </span>
          </div>
        </div>
      </aside>

      <div className="flex min-w-0 flex-1 flex-col">
        <header className="pb-safe-top flex h-14 shrink-0 items-center border-b border-[var(--line)] bg-[var(--surface)] px-4 md:hidden">
          <Logo />
        </header>

        {/* Scrollable region must be keyboard-reachable: pages whose content is
            entirely non-focusable (e.g. Settings with runtime writes disabled)
            would otherwise be unscrollable by keyboard (axe
            scrollable-region-focusable). */}
        <main id="proofbench-main" tabIndex={0} className="flex min-h-0 min-w-0 flex-1 flex-col overflow-y-auto">
          <Outlet />
        </main>

        <nav
          className="pb-safe-bottom grid shrink-0 grid-cols-4 gap-1 bg-[var(--surface)] p-2 md:hidden"
          aria-label="Console navigation"
        >
          {NAV.map((item) => (
            <NavLink
              key={item.to}
              to={item.to}
              className={({ isActive }) =>
                `flex min-h-12 flex-col items-center justify-center gap-0.5 rounded-[12px] px-1 text-[11px] font-medium ${
                  isActive
                    ? "bg-[var(--ink)] text-[var(--surface)]"
                    : "text-[var(--ink-2)]"
                }`
              }
            >
              {item.icon}
              {item.label}
            </NavLink>
          ))}
        </nav>
      </div>

      <ServerStatus />
    </div>
  );
}
