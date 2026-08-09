import { useCallback, useEffect, useRef, useState } from "react";
import { Link, NavLink, Outlet, useLocation, useNavigate, useSearchParams } from "react-router-dom";
import {
  AUTH_CHANGE_EVENT,
  bootstrapAuthSession,
  createAuthSession,
  deleteSession,
  listSessions,
  logoutAuthSession,
} from "../api.js";
import Logo from "./Logo.jsx";
import { SessionList } from "./Sidebar.jsx";
import StatusIcon from "./StatusIcon.jsx";
import { BTN_PRIMARY, BTN_SECONDARY, FlowHighlight, INPUT, useFlowHighlight } from "./ui.jsx";

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
    to: "/app/overview",
    label: "Overview",
    icon: (
      <NavIcon>
        <rect x="3.5" y="3.5" width="7" height="7" rx="1.5" />
        <rect x="13.5" y="3.5" width="7" height="7" rx="1.5" />
        <rect x="3.5" y="13.5" width="7" height="7" rx="1.5" />
        <rect x="13.5" y="13.5" width="7" height="7" rx="1.5" />
      </NavIcon>
    ),
  },
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
    icon: <SettingsGlyph />,
  },
];

/* Menu glyphs. A menu item names a destination or an action, so it takes a
   glyph for THAT — not one borrowed from the status vocabulary, where a clock
   means "pending" and a tick means "ok". Settings reuses the nav's own mark so
   one destination has one icon everywhere. */
function UserGlyph({ size = 15 }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" className="shrink-0">
      <circle cx="12" cy="8" r="3.6" />
      <path d="M4.8 20a7.2 7.2 0 0 1 14.4 0" />
    </svg>
  );
}

function KeyGlyph({ size = 16 }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" className="shrink-0">
      <circle cx="8" cy="15" r="4" />
      <path d="M10.9 12.1 20 3" />
      <path d="M17 6l2.5 2.5" />
    </svg>
  );
}

function SettingsGlyph() {
  return (
    <NavIcon>
      <path d="M3 7.5h11" />
      <circle cx="16.5" cy="7.5" r="2" />
      <path d="M18.5 7.5H21" />
      <path d="M3 16.5h3" />
      <circle cx="10.5" cy="16.5" r="2" />
      <path d="M12.5 16.5H21" />
    </NavIcon>
  );
}

const SKIP_LINK =
  "sr-only z-50 rounded-[12px] bg-[var(--surface)] px-3 py-2 text-[13px] " +
  "font-medium text-[var(--accent)] shadow-lift focus:not-sr-only focus:fixed focus:left-4 focus:top-4";

/* Sidebar geometry, remembered per browser. Width is a drag target and the
   collapsed state is a toggle, so both are user decisions worth persisting. */
const SIDEBAR_MIN = 208;
const SIDEBAR_MAX = 420;
const SIDEBAR_DEFAULT = 248;
const SIDEBAR_RAIL = 60;

function clampWidth(value) {
  return Math.min(SIDEBAR_MAX, Math.max(SIDEBAR_MIN, Math.round(value)));
}

function useSidebarGeometry() {
  const [width, setWidth] = useState(() => {
    try {
      const stored = Number(localStorage.getItem("pb-sidebar-width"));
      return Number.isFinite(stored) && stored > 0 ? clampWidth(stored) : SIDEBAR_DEFAULT;
    } catch {
      return SIDEBAR_DEFAULT;
    }
  });
  const [collapsed, setCollapsed] = useState(() => {
    try {
      return localStorage.getItem("pb-sidebar-collapsed") === "1";
    } catch {
      return false;
    }
  });

  const persistWidth = useCallback((next) => {
    const value = clampWidth(next);
    setWidth(value);
    try { localStorage.setItem("pb-sidebar-width", String(value)); } catch { /* private mode */ }
  }, []);

  const toggle = useCallback(() => {
    setCollapsed((current) => {
      const next = !current;
      try { localStorage.setItem("pb-sidebar-collapsed", next ? "1" : "0"); } catch { /* private mode */ }
      return next;
    });
  }, []);

  return { width, collapsed, setWidth: persistWidth, toggle };
}

/* The drag handle exposes the sidebar width as a slider: pointer drag for the
   mouse, arrow keys for the keyboard, so width is not a mouse-only affordance. */
function SidebarResizer({ width, onWidth }) {
  const onPointerDown = (event) => {
    event.preventDefault();
    const startX = event.clientX;
    const startWidth = width;
    const target = event.currentTarget;
    target.setPointerCapture?.(event.pointerId);
    const onMove = (move) => onWidth(startWidth + (move.clientX - startX));
    const onUp = () => {
      target.releasePointerCapture?.(event.pointerId);
      target.removeEventListener("pointermove", onMove);
      target.removeEventListener("pointerup", onUp);
      document.body.style.cursor = "";
      document.body.style.userSelect = "";
    };
    target.addEventListener("pointermove", onMove);
    target.addEventListener("pointerup", onUp);
    document.body.style.cursor = "col-resize";
    document.body.style.userSelect = "none";
  };

  const onKeyDown = (event) => {
    if (event.key === "ArrowLeft") { event.preventDefault(); onWidth(width - 16); }
    if (event.key === "ArrowRight") { event.preventDefault(); onWidth(width + 16); }
    if (event.key === "Home") { event.preventDefault(); onWidth(SIDEBAR_MIN); }
    if (event.key === "End") { event.preventDefault(); onWidth(SIDEBAR_MAX); }
  };

  return (
    <div
      role="slider"
      aria-orientation="horizontal"
      aria-label="Resize sidebar"
      aria-valuenow={width}
      aria-valuemin={SIDEBAR_MIN}
      aria-valuemax={SIDEBAR_MAX}
      tabIndex={0}
      onPointerDown={onPointerDown}
      onKeyDown={onKeyDown}
      onDoubleClick={() => onWidth(SIDEBAR_DEFAULT)}
      title="Drag to resize, double-click to reset"
      /* The grab area is wide enough to hit; the mark it shows is a hairline.
         Painting the whole target made a 6px sage bar down the viewport. */
      className="group absolute inset-y-0 right-0 z-10 w-3 translate-x-1/2 cursor-col-resize focus-visible:outline-none"
    >
      <span
        aria-hidden="true"
        className="absolute inset-y-0 left-1/2 w-px -translate-x-1/2 bg-transparent transition-colors duration-150 group-hover:bg-[var(--line-strong)] group-focus-visible:w-0.5 group-focus-visible:bg-[var(--accent)]"
      />
    </div>
  );
}

function PanelIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <rect x="3" y="4" width="18" height="16" rx="2.5" />
      <path d="M9.5 4v16" />
    </svg>
  );
}

function HomeLogoLink() {
  return (
    <Link
      to="/"
      aria-label="ProofBench home"
      className="inline-flex rounded-[8px] focus-visible:outline-none focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[var(--accent)]"
    >
      <Logo />
    </Link>
  );
}

/* The profile block is one button, and the menu it opens is a real menu:
   Escape closes it and returns focus, a click outside dismisses it, and arrow
   keys walk the items. It opens upward because the block sits at the bottom of
   the sidebar. */
function ProfileMenu({ authenticated, collapsed, onSignOut }) {
  const [open, setOpen] = useState(false);
  const rootRef = useRef(null);
  const triggerRef = useRef(null);
  const menuRef = useRef(null);

  useEffect(() => {
    if (!open) return undefined;
    const onKey = (event) => {
      if (event.key === "Escape") {
        setOpen(false);
        triggerRef.current?.focus();
      }
    };
    const onPointer = (event) => {
      if (!rootRef.current?.contains(event.target)) setOpen(false);
    };
    document.addEventListener("keydown", onKey);
    document.addEventListener("pointerdown", onPointer);
    // First item takes focus so the menu is usable without a mouse.
    menuRef.current?.querySelector("[role='menuitem']")?.focus();
    return () => {
      document.removeEventListener("keydown", onKey);
      document.removeEventListener("pointerdown", onPointer);
    };
  }, [open]);

  const onMenuKeyDown = (event) => {
    if (event.key !== "ArrowDown" && event.key !== "ArrowUp") return;
    event.preventDefault();
    const items = [...(menuRef.current?.querySelectorAll("[role='menuitem']") || [])];
    const at = items.indexOf(document.activeElement);
    const next = event.key === "ArrowDown" ? at + 1 : at - 1;
    items[(next + items.length) % items.length]?.focus();
  };

  const item =
    "flex min-h-10 w-full items-center gap-2.5 rounded-[10px] px-2.5 text-left text-[13px] " +
    "text-[var(--ink-2)] transition-colors duration-150 hover:bg-[var(--surface-2)] hover:text-[var(--ink)] " +
    "focus-visible:bg-[var(--surface-2)] focus-visible:text-[var(--ink)] focus-visible:outline-none";

  return (
    <div ref={rootRef} className="relative">
      {open && (
        <div
          ref={menuRef}
          role="menu"
          aria-label="Profile"
          tabIndex={-1}
          onKeyDown={onMenuKeyDown}
          className="pb-glass-float absolute bottom-full left-0 right-0 z-20 mb-2 rounded-[14px] p-1.5 shadow-[var(--shadow-lift)]"
        >
          <Link role="menuitem" to="/app/profile" onClick={() => setOpen(false)} className={item}>
            <span className="text-[var(--ink-3)]"><UserGlyph /></span>
            View profile
          </Link>
          <Link role="menuitem" to="/app/settings" onClick={() => setOpen(false)} className={item}>
            <span className="text-[var(--ink-3)]"><SettingsGlyph /></span>
            Settings
          </Link>
          {authenticated ? (
            <button
              role="menuitem"
              type="button"
              onClick={() => { setOpen(false); onSignOut(); }}
              className={item}
            >
              Sign out
            </button>
          ) : (
            <p className="px-2.5 pb-1 pt-2 text-[11px] leading-relaxed text-[var(--ink-3)]">
              Local profile. No sign-in is held by this browser.
            </p>
          )}
        </div>
      )}

      <button
        ref={triggerRef}
        type="button"
        aria-haspopup="menu"
        aria-expanded={open}
        onClick={() => setOpen((value) => !value)}
        title={collapsed ? (authenticated ? "Client operator" : "Local operator") : undefined}
        className={`flex w-full items-center gap-2.5 rounded-[12px] py-2 transition-colors duration-150 hover:bg-[var(--profile-tint)] focus-visible:outline-none focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[var(--accent)] ${
          collapsed ? "justify-center px-0" : "px-2.5"
        } ${open ? "bg-[var(--profile-tint)]" : ""}`}
      >
        <span
          aria-hidden="true"
          className="flex h-7 w-7 shrink-0 items-center justify-center rounded-[8px] bg-[var(--profile)] text-[12px] font-semibold text-[var(--profile-ink)]"
        >
          {authenticated ? "C" : "L"}
        </span>
        <span className={`min-w-0 flex-1 text-left ${collapsed ? "sr-only" : ""}`}>
          <span className="block truncate text-[13px] font-medium text-[var(--ink)]">
            {authenticated ? "Client operator" : "Local operator"}
          </span>
          <span className="block truncate text-[12px] text-[var(--ink-3)]">
            {authenticated ? "Authenticated" : "Local mode"}
          </span>
        </span>
        {!collapsed && (
          <svg
            width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor"
            strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round"
            aria-hidden="true"
            className={`shrink-0 text-[var(--ink-3)] transition-transform duration-150 ${open ? "rotate-180" : ""}`}
          >
            <path d="m6 15 6-6 6 6" />
          </svg>
        )}
      </button>
    </div>
  );
}

/* One poll serves both jobs: it is the console's session history and, by
   whether it answers at all, the reachability check. */
function useSessionHistory(enabled) {
  const [sessions, setSessions] = useState([]);
  const [up, setUp] = useState(null);

  const refresh = useCallback(async () => {
    try {
      const list = await listSessions();
      setSessions(Array.isArray(list) ? list : []);
      setUp(true);
      return true;
    } catch {
      setUp(false);
      return false;
    }
  }, []);

  useEffect(() => {
    if (!enabled) return undefined;
    let cancelled = false;
    const tick = () => { if (!cancelled) refresh(); };
    tick();
    const t = setInterval(tick, 10000);
    return () => { cancelled = true; clearInterval(t); };
  }, [enabled, refresh]);

  return { sessions, up, refresh };
}

/* Healthy deployments say nothing. Only an outage earns chrome. */
function ServerStatus({ up }) {
  if (up !== false) return null;

  return (
    <span
      className="fixed right-4 top-4 z-40 inline-flex items-center gap-1.5 rounded-full bg-[var(--danger-tint)] px-3 py-1.5 text-[12px] font-medium text-[var(--danger)] shadow-[var(--shadow-lift)]"
      role="status"
    >
      <StatusIcon tone="danger" size={13} />
      Server unreachable
    </span>
  );
}

function AccessGate({ restore, unavailable, onSubmit, busy }) {
  const [token, setToken] = useState("");
  const [error, setError] = useState("");

  const submit = async (event) => {
    event.preventDefault();
    setError("");
    try {
      await onSubmit(token);
      setToken("");
    } catch (cause) {
      setError(cause?.message || "Sign-in failed.");
    }
  };

  return (
    <main className="pb-shell-bg pb-viewport-min pb-safe-screen pb-safe-inline-shell flex items-center justify-center px-4 text-[var(--ink)]">
      <section
        className="w-full max-w-[26rem] rounded-[24px] bg-[var(--surface)] p-8 shadow-lift"
        aria-labelledby="proofbench-access-title"
      >
        <Logo />
        <h1 id="proofbench-access-title" className="mt-6 text-[24px] font-semibold tracking-[-0.01em]">
          {unavailable ? "Console unavailable" : restore ? "Re-enter your password" : "Sign in to ProofBench"}
        </h1>
        <p className="mt-2 text-[13px] leading-relaxed text-[var(--ink-2)]">
          {unavailable
            ? "ProofBench could not verify this deployment's access settings. Check the service and try again."
            : restore
              ? "Your read-only session is still active. Re-enter the password to restore write access; it is kept in memory only."
              : "Enter the password for this deployment. It is kept in memory only and is never saved by the browser."}
        </p>
        {unavailable ? (
          <button type="button" onClick={() => onSubmit("")} disabled={busy} className={`${BTN_SECONDARY} mt-6 h-10 w-full`}>
            {busy ? "Checking" : "Retry"}
          </button>
        ) : (
          <form onSubmit={submit} className="mt-6">
            <label htmlFor="proofbench-token" className="mb-1.5 block text-[12px] font-medium text-[var(--ink-2)]">
              Password
            </label>
            <input
              id="proofbench-token"
              type="password"
              autoComplete="current-password"
              spellCheck="false"
              value={token}
              onChange={(event) => setToken(event.target.value)}
              className={INPUT}
              required
            />
            {error ? <p role="alert" className="mt-2 text-[12px] text-[var(--danger)]">{error}</p> : null}
            <button type="submit" disabled={busy || !token.trim()} className={`${BTN_PRIMARY} mt-4 h-10 w-full`}>
              {busy ? "Signing in" : restore ? "Restore write access" : "Sign in"}
            </button>
          </form>
        )}
      </section>
    </main>
  );
}

function stateFromContext(context) {
  if (context?.localMode || context?.authMode === "local") return "local";
  if (context?.writeAuthenticated) return "authenticated";
  if (context?.authMode === "authenticated" && context?.cookieAuthenticated) return "restore";
  if (context?.authMode === "authenticated") return "signed-out";
  return "unavailable";
}

export default function Shell() {
  const [authState, setAuthState] = useState("checking");
  const [searchParams] = useSearchParams();
  const navigate = useNavigate();
  const consoleEnabled = authState === "local" || authState === "authenticated";
  const { sessions, up, refresh } = useSessionHistory(consoleEnabled);
  const { width, collapsed, setWidth, toggle } = useSidebarGeometry();
  const activeSessionId = searchParams.get("session");
  const { pathname } = useLocation();
  /* Keyed on the collapsed state too: collapsing changes every row's width and
     padding, so the pill has to re-measure or it keeps the expanded geometry. */
  const navFlow = useFlowHighlight(`${pathname}:${collapsed}`);

  const openSession = (id) => {
    localStorage.setItem("proofbench.activeSessionId", id);
    navigate(`/app/benchmark?session=${encodeURIComponent(id)}`);
  };

  const removeSession = async (session) => {
    try {
      await deleteSession(session.id);
    } catch {
      /* the list refresh below reports the true state either way */
    }
    await refresh();
    if (session.id === activeSessionId) navigate("/app/benchmark");
  };

  useEffect(() => {
    let active = true;
    bootstrapAuthSession()
      .then((session) => { if (active) setAuthState(stateFromContext(session)); })
      .catch(() => { if (active) setAuthState("unavailable"); });
    const onAuthChange = (event) => {
      setAuthState(stateFromContext(event.detail));
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
  if (!consoleEnabled) {
    return (
      <AccessGate
        restore={authState === "restore"}
        unavailable={authState === "unavailable" || authState === "retrying"}
        busy={authState === "retrying" || authState === "signing-in"}
        onSubmit={async (token) => {
          if (authState === "unavailable" || authState === "retrying") {
            setAuthState("retrying");
            try {
              setAuthState(stateFromContext(await bootstrapAuthSession()));
            } catch {
              setAuthState("unavailable");
            }
            return;
          }
          const prior = authState;
          setAuthState("signing-in");
          try {
            setAuthState(stateFromContext(await createAuthSession(token)));
          } catch (cause) {
            setAuthState(prior);
            throw cause;
          }
        }}
      />
    );
  }

  const signOut = async () => {
    await logoutAuthSession().catch(() => {});
    setAuthState("signed-out");
  };

  const navItem = ({ isActive }) =>
    `pb-nav-item pb-flow-row flex min-h-11 items-center gap-2 text-[13px] ${collapsed ? "justify-center px-0" : "px-2.5"} ${
      isActive
        ? "pb-nav-item-active font-medium"
        : "text-[var(--ink-2)] hover:bg-[var(--surface-2)] hover:text-[var(--ink)]"
    }`;

  return (
    <div className="pb-shell-bg pb-viewport-height pb-safe-inline-shell flex w-full overflow-hidden text-[var(--ink)]">
      <a href="#proofbench-main" className={SKIP_LINK}>
        Skip to main content
      </a>

      {/* One atmosphere for the whole console, not per-route: the translucent
          panels need something behind them on every page, and a fixed layer
          means cards visibly change tint as they scroll across it. The sidebar
          paints over it (opaque by design); only the canvas shows it. */}
      <div aria-hidden="true" className="pb-atmosphere" />

      <aside
        aria-label="Primary navigation"
        style={{ width: collapsed ? SIDEBAR_RAIL : width }}
        className={`pb-sidebar-shell relative hidden shrink-0 flex-col border-r border-[var(--line)] md:flex ${collapsed ? "px-2" : ""}`}
      >
        <div className={`flex h-14 shrink-0 items-center ${collapsed ? "justify-center" : "justify-between px-5"}`}>
          {collapsed ? null : <HomeLogoLink />}
          <button
            type="button"
            onClick={toggle}
            aria-expanded={!collapsed}
            aria-controls="pb-sidebar-body"
            title={collapsed ? "Expand sidebar" : "Collapse sidebar"}
            aria-label={collapsed ? "Expand sidebar" : "Collapse sidebar"}
            className="inline-flex h-9 w-9 shrink-0 items-center justify-center rounded-[8px] text-[var(--ink-3)] transition-colors duration-150 hover:bg-[var(--surface-2)] hover:text-[var(--ink)] focus-visible:outline-none focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-[var(--accent)]"
          >
            <PanelIcon />
          </button>
        </div>
        <nav id="pb-sidebar-body" className={`shrink-0 ${collapsed ? "px-0" : "px-3"}`} aria-label="Console">
          <div ref={navFlow.containerRef} className="relative flex flex-col gap-1">
            <FlowHighlight box={navFlow.box} />
            {NAV.map((item) => (
              <NavLink
                key={item.to}
                to={item.to}
                className={navItem}
                title={collapsed ? item.label : undefined}
                data-flow-active={pathname.startsWith(item.to) ? "true" : undefined}
              >
                {item.icon}
                {collapsed ? <span className="sr-only">{item.label}</span> : item.label}
              </NavLink>
            ))}
          </div>
        </nav>

        {/* Session history lives with the navigation, the way every chat
            product puts its conversation list: one place to see what exists
            and jump between them. Runs remains the page for filtering and
            judging them. */}
        {collapsed ? <div className="flex-1" /> : (
        <div className="mt-4 flex min-h-0 flex-1 flex-col px-3" aria-label="Sessions">
          <div className="flex shrink-0 items-baseline justify-between gap-2 px-2.5 pb-1.5">
            <h2 className="pb-eyebrow">Sessions</h2>
            {sessions.length > 0 && (
              <span className="pb-mono text-[11px] text-[var(--ink-3)]">{sessions.length}</span>
            )}
          </div>
          <div className="min-h-0 flex-1 overflow-y-auto pb-2">
            {sessions.length === 0 ? (
              <p className="px-2.5 py-1 text-[12px] leading-relaxed text-[var(--ink-2)]">
                Benchmarks you start appear here.
              </p>
            ) : (
              <SessionList
                sessions={sessions}
                activeId={activeSessionId}
                onSelect={openSession}
                onDelete={removeSession}
                compact
              />
            )}
          </div>
        </div>
        )}
        {/* Translucent, not solid: an opaque band at the foot of a glass rail
            reads as a slab bolted on. It still holds its own tint, just as a
            veil over the same material. */}
        <div className={`border-t border-[var(--line)] bg-[color-mix(in_oklab,var(--profile-tint)_55%,transparent)] ${collapsed ? "p-2" : "p-3"}`}>
          <ProfileMenu authenticated={authState === "authenticated"} collapsed={collapsed} onSignOut={signOut} />
        </div>

        {!collapsed && <SidebarResizer width={width} onWidth={setWidth} />}
      </aside>

      <div className="flex min-w-0 flex-1 flex-col">
        <header className="pb-safe-top flex h-14 shrink-0 items-center border-b border-[var(--line)] bg-[var(--surface)] px-4 md:hidden">
          <HomeLogoLink />
          {authState === "authenticated" ? (
            <button type="button" onClick={signOut} className={`${BTN_SECONDARY} ml-auto min-h-9 px-4`}>
              Sign out
            </button>
          ) : null}
        </header>

        {/* Scrollable region must be keyboard-reachable: pages whose content is
            entirely non-focusable (e.g. Settings with runtime writes disabled)
            would otherwise be unscrollable by keyboard (axe
            scrollable-region-focusable). */}
        {/* `relative` is load-bearing: it makes this scroll container the
            containing block for absolutely positioned descendants. Without it
            an `sr-only` label (Tailwind's sr-only is position:absolute) inside
            an otherwise unpositioned chain resolves against the initial
            containing block, escaping the shell's overflow-hidden and growing
            the document so the whole app scrolls behind its own chrome. */}
        <main id="proofbench-main" tabIndex={0} className="relative flex min-h-0 min-w-0 flex-1 flex-col overflow-y-auto">
          <Outlet />
        </main>

        <nav
          className="pb-safe-bottom grid shrink-0 grid-cols-5 gap-1 bg-[var(--surface)] p-2 md:hidden"
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

      <ServerStatus up={up} />
    </div>
  );
}
