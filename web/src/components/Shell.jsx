import { useEffect, useState } from "react";
import { NavLink, Outlet, useLocation } from "react-router-dom";
import { listSessions } from "../api.js";
import Logo from "./Logo.jsx";

const NAV = [
  { to: "/app/benchmark", label: "Benchmark" },
  { to: "/app/runs", label: "Runs" },
  { to: "/app/datasets", label: "Datasets" },
  { to: "/app/settings", label: "Settings" },
];

const TITLES = {
  "/app/benchmark": "Benchmark",
  "/app/runs": "Runs",
  "/app/datasets": "Datasets",
  "/app/settings": "Settings",
};

const SPONSORS = ["Daytona", "Kimi", "Nosana", "Doubleword", "Oxylabs"];

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

  const dot =
    up === null ? "bg-text-3" : up ? "bg-ok" : "bg-err";
  const hint =
    up === null ? "Checking server" : up ? "Server connected" : "Server unreachable";

  return (
    <div className="flex items-center gap-2" title={hint}>
      <span className="text-xs text-text-2">Server</span>
      <span className={`h-2 w-2 rounded-full ${dot}`} />
    </div>
  );
}

export default function Shell() {
  const { pathname } = useLocation();
  const title = TITLES[pathname] ?? "Console";

  return (
    <div className="pb-shell-bg flex h-screen w-screen overflow-hidden text-text">
      <aside className="pb-sidebar-shell flex w-[220px] shrink-0 flex-col border-r border-border">
        <div className="flex h-14 shrink-0 items-center border-b border-border px-4">
          <Logo />
        </div>
        <nav className="flex-1 overflow-y-auto px-2 py-3">
          <div className="px-2 pb-1.5 text-[11px] font-medium uppercase tracking-wide text-text-3">
            Console
          </div>
          <div className="space-y-0.5">
            {NAV.map((item) => (
              <NavLink
                key={item.to}
                to={item.to}
                className={({ isActive }) =>
                  `pb-nav-item flex items-center rounded-md px-2.5 py-1.5 text-[13px] transition-all duration-150 ease-out-quart ${
                    isActive
                      ? "pb-nav-item-active font-medium text-accent"
                      : "text-text-2 hover:bg-surface hover:text-text"
                  }`
                }
              >
                {item.label}
              </NavLink>
            ))}
          </div>
        </nav>
        <div className="border-t border-border px-4 py-3">
          <div className="flex flex-wrap gap-x-2 gap-y-1">
            {SPONSORS.map((s) => (
              <span key={s} className="text-[11px] leading-4 text-text-3">
                {s}
              </span>
            ))}
          </div>
        </div>
      </aside>
      <div className="flex min-w-0 flex-1 flex-col">
        <header className="pb-panel-soft flex h-14 shrink-0 items-center justify-between border-b border-border bg-surface px-5">
          <h1 className="text-base font-semibold text-text">{title}</h1>
          <ServerStatus />
        </header>
        <main className="flex min-h-0 min-w-0 flex-1 flex-col overflow-y-auto">
          <Outlet />
        </main>
      </div>
    </div>
  );
}
