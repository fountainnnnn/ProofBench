import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { getHealth, isLocalMode } from "../api.js";
import { PAGE_HEADER, PAGE_TITLE, PANEL, SHEEN_SWIPE, Skeleton, useSelectionSheen } from "../components/ui.jsx";
import HeaderActions from "../components/HeaderActions.jsx";
import { THEME_CHOICES, applyTheme, storedTheme } from "../theme.js";

/* Who this console is running as. In the local profile that is a deployment
   fact rather than an account: there is no user record, so this page states
   what the profile can do and where each part of it is configured, instead of
   inventing a person to display. */

function Field({ label, value, hint }) {
  return (
    <div className="flex flex-col gap-0.5 py-3">
      <dt className="text-[12px] text-[var(--ink-3)]">{label}</dt>
      <dd className="text-[13px] text-[var(--ink)]">{value}</dd>
      {hint && <dd className="text-[12px] leading-relaxed text-[var(--ink-2)]">{hint}</dd>}
    </div>
  );
}

export default function Profile() {
  const [health, setHealth] = useState(null);
  const [failed, setFailed] = useState(false);
  const [theme, setThemeState] = useState(storedTheme);
  const themeSheen = useSelectionSheen(theme);
  const local = isLocalMode();

  useEffect(() => {
    let alive = true;
    getHealth()
      .then((data) => { if (alive) setHealth(data); })
      .catch(() => { if (alive) setFailed(true); });
    const sync = () => setThemeState(storedTheme());
    window.addEventListener("pb-theme-change", sync);
    return () => {
      alive = false;
      window.removeEventListener("pb-theme-change", sync);
    };
  }, []);

  const setTheme = (value) => {
    setThemeState(value);
    applyTheme(value);
  };

  return (
    <div className="flex min-h-full flex-col">
      <header className={`${PAGE_HEADER} px-4 sm:px-8`}>
        <div className="mx-auto flex w-full max-w-canvas items-start justify-between gap-x-6 pb-3 pt-3.5">
          <div className="min-w-0">
            <span className="pb-eyebrow-glow">Account</span>
            <h1 className={`${PAGE_TITLE} mt-1`}>Profile</h1>
            <p className="mt-0.5 text-[13px] text-[var(--ink-2)]">
              Who this console runs as, and what that profile is allowed to do.
            </p>
          </div>
          <HeaderActions showReadiness={false} />
        </div>
      </header>

      <div className="mx-auto flex w-full max-w-[760px] flex-col gap-4 px-4 pb-12 pt-6 sm:px-8">
        <section className={`${PANEL} p-5`} aria-labelledby="identity-heading">
          <div className="flex items-center gap-3">
            <span
              aria-hidden="true"
              className="flex h-11 w-11 shrink-0 items-center justify-center rounded-[12px] bg-[var(--profile)] text-[16px] font-semibold text-[var(--profile-ink)]"
            >
              L
            </span>
            <div className="min-w-0">
              <h2 id="identity-heading" className="text-[16px] font-semibold text-[var(--ink)]">
                Local operator
              </h2>
              <p className="text-[13px] text-[var(--ink-2)]">
                {local ? "Local profile, no sign-in required" : "Authenticated profile"}
              </p>
            </div>
          </div>

          <dl className="mt-4 divide-y divide-[var(--line)] border-t border-[var(--line)]">
            <Field
              label="Access"
              value={local ? "Full read and write" : "Scoped to this deployment's token"}
              hint={
                local
                  ? "The local profile resolves every request to one deterministic tenant. It authenticates nothing, which is why it is supported only while the server stays bound to this machine."
                  : undefined
              }
            />
            <Field
              label="Deployment"
              value={
                failed ? (
                  <span className="text-[var(--ink-2)]">Version unavailable</span>
                ) : health ? (
                  <span className="pb-mono">{health.version || "unknown"}</span>
                ) : (
                  <Skeleton className="h-3 w-16" />
                )
              }
            />
            <Field
              label="Data"
              value="Stays on this deployment"
              hint="Datasets, run artifacts, and reports are kept until you delete them. Benchmark runs send documents to disposable sandboxes and to the providers you enable."
            />
          </dl>
        </section>

        <section className={`${PANEL} p-5`} aria-labelledby="appearance-heading">
          <h2 id="appearance-heading" className="text-[16px] font-semibold text-[var(--ink)]">
            Appearance
          </h2>
          <p className="mt-0.5 text-[13px] text-[var(--ink-2)]">
            Theme for this browser. System follows the operating system preference.
          </p>
          <div
            role="radiogroup"
            aria-labelledby="appearance-heading"
            className="mt-4 inline-flex rounded-full bg-[var(--surface-2)] p-1"
          >
            {THEME_CHOICES.map((choice) => (
              <button
                key={choice.value}
                type="button"
                role="radio"
                aria-checked={theme === choice.value}
                onClick={() => setTheme(choice.value)}
                /* Same selected treatment as the identical control in Settings
                   and as the Runs filters: one selection looks one way. */
                className={`min-h-8 rounded-full px-3.5 text-[13px] font-medium transition-colors duration-150 ${
                  theme === choice.value
                    ? `bg-[var(--ink)] text-[var(--surface)] ${themeSheen ? SHEEN_SWIPE : ""}`
                    : "text-[var(--ink-2)] hover:text-[var(--ink)]"
                }`}
              >
                {choice.label}
              </button>
            ))}
          </div>
        </section>

        {/* Stated plainly rather than shown as a disabled button. A sign-out
            control implies a credential exists to surrender; in the local
            profile none does, and pretending otherwise would be the same kind
            of false affordance this product refuses elsewhere. */}
        {local && (
          <section className={`${PANEL} flex items-start gap-3 p-5`} aria-labelledby="signout-heading">
            <span className="mt-0.5 shrink-0 text-[var(--ink-3)]">
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                <circle cx="8" cy="15" r="4" />
                <path d="M10.9 12.1 20 3" />
                <path d="M17 6l2.5 2.5" />
              </svg>
            </span>
            <div className="min-w-0">
              <h2 id="signout-heading" className="text-[14px] font-semibold text-[var(--ink)]">
                There is nothing to sign out of
              </h2>
              <p className="mt-1 max-w-[62ch] text-[13px] leading-relaxed text-[var(--ink-2)]">
                This deployment runs the tokenless local profile, so the browser holds no
                credential. To require one, set a password on the server and restart it; the
                console will then refuse to open without it. Provider credentials are separate and
                live in{" "}
                <Link to="/app/settings" className="font-medium text-[var(--accent)] hover:underline">
                  Settings
                </Link>
                .
              </p>
            </div>
          </section>
        )}
      </div>
    </div>
  );
}
