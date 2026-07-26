import { useEffect, useId, useState } from "react";
import {
  deleteProviderKey,
  getProviderReadiness,
  listProviderKeys,
  saveProviderKey,
} from "../api.js";
import { BTN_DANGER, BTN_PRIMARY, BTN_SECONDARY, Collapse, INPUT, PAGE_HEADER, PAGE_TITLE, PANEL, SHEEN_SWIPE, Skeleton, useSelectionSheen } from "../components/ui.jsx";
import HeaderActions from "../components/HeaderActions.jsx";
import StatusIcon from "../components/StatusIcon.jsx";
import { THEME_CHOICES, applyTheme, storedTheme } from "../theme.js";
import daytonaLogo from "../assets/provider-logos/daytona.svg";
import deepseekLogo from "../assets/provider-logos/deepseek.svg";
import doublewordLogo from "../assets/provider-logos/doubleword.svg";
import kimiLogo from "../assets/provider-logos/kimi.svg";
import nosanaLogo from "../assets/provider-logos/nosana.svg";
import openaiLogo from "../assets/provider-logos/openai.svg";
import openrouterLogo from "../assets/provider-logos/openrouter.svg";
import oxylabsLogo from "../assets/provider-logos/oxylabs.svg";

/* Banner glyphs. Same 16px box and 1.7 stroke as the console's nav icons, so
   the icon family stays one family. */
function ReadyIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <circle cx="12" cy="12" r="9" />
      <path d="m8.5 12.2 2.4 2.4 4.6-5.2" />
    </svg>
  );
}

function BlockedIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M10.6 3.9 2.5 18a1.6 1.6 0 0 0 1.4 2.4h16.2a1.6 1.6 0 0 0 1.4-2.4L13.4 3.9a1.6 1.6 0 0 0-2.8 0Z" />
      <path d="M12 9.5v4" />
      <path d="M12 17.1h.01" />
    </svg>
  );
}

// Readiness is a configuration check the server performs without contacting a
// provider, so opening Settings never issues a billable request.
const READINESS = {
  ready: { word: "text-[var(--ok)]", label: "ready" },
  partial: { word: "text-[var(--warn)]", label: "partly configured" },
  missing: { word: "text-[var(--danger)]", label: "not configured" },
};

// Local copies of provider marks keep Settings usable offline and preserve the
// promise above that rendering this list never contacts a provider. `nosana_vlm`
// is retained as an alias for older readiness fixtures and persisted responses.
const PROVIDER_LOGOS = {
  daytona: daytonaLogo,
  deepseek: deepseekLogo,
  doubleword: doublewordLogo,
  moonshot: kimiLogo,
  nosana: nosanaLogo,
  nosana_vlm: nosanaLogo,
  openai: openaiLogo,
  openrouter: openrouterLogo,
  oxylabs: oxylabsLogo,
};

function Chevron({ open }) {
  return (
    <svg
      aria-hidden="true"
      viewBox="0 0 12 12"
      className={`h-3 w-3 shrink-0 text-[var(--ink-3)] transition-transform duration-150 ${open ? "rotate-90" : ""}`}
      fill="none"
      stroke="currentColor"
      strokeWidth="1.6"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <path d="M4.5 2.5 8 6l-3.5 3.5" />
    </svg>
  );
}

// One service = one row. The header line is always visible (icon, name, status);
// its env-var breakdown and full capability sentence fold away behind the row's
// own disclosure so the list reads as a short scannable index.
/* Unknown future providers fall back to a two-character monogram, so a newly
   added backend entry never leaves an empty tile while its logo is sourced. */
function monogram(label) {
  const name = String(label || "?").trim();
  const capitals = name.replace(/[^A-Za-z]/g, "").match(/[A-Z]/g) || [];
  if (capitals.length >= 2) return capitals.slice(0, 2).join("");
  const letters = name.replace(/[^A-Za-z]/g, "");
  if (letters.length >= 2) return letters[0].toUpperCase() + letters[1].toLowerCase();
  return (letters[0] || "?").toUpperCase();
}

function ServiceLogo({ item }) {
  const logo = PROVIDER_LOGOS[item.provider];
  return (
    <span
      className={`grid h-7 w-7 shrink-0 place-items-center overflow-hidden rounded-[8px] ${
        logo
          ? "bg-[oklch(0.985_0.004_210)] p-1"
          : "bg-[var(--surface-2)] text-[12px] font-semibold text-[var(--ink-2)]"
      }`}
      aria-hidden="true"
    >
      {logo ? (
        <img
          src={logo}
          alt=""
          className="h-full w-full object-contain"
          data-provider-logo={item.provider}
        />
      ) : (
        monogram(item.label)
      )}
    </span>
  );
}

/* A missing required variable is the one thing an operator can act on from this
   list, so the action sits on that row rather than in a form further down the
   page. The value lives in this component's state only: it is posted once, is
   never written to storage, and is never read back — the row's word flips from
   "missing" to "set" instead. */
function MissingEnvField({ env, onSave }) {
  const [value, setValue] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const id = useId();

  const submit = async (event) => {
    event.preventDefault();
    if (saving || !value) return;
    setSaving(true);
    setError("");
    try {
      await onSave(env, value);
      setValue("");
    } catch (failure) {
      setError(failure.message || "Could not save this provider credential.");
    } finally {
      setSaving(false);
    }
  };

  return (
    <form onSubmit={submit} className="mt-2 flex items-start gap-2">
      <div className="min-w-0 flex-1">
        <input
          type="password"
          value={value}
          onChange={(event) => setValue(event.target.value)}
          placeholder="Paste value"
          aria-label={`Value for ${env}`}
          aria-invalid={error ? "true" : undefined}
          aria-describedby={error ? `${id}-error` : undefined}
          autoComplete="off"
          disabled={saving}
          className={`${INPUT} text-[12px]`}
          required
        />
        {error && (
          <p id={`${id}-error`} role="alert" className="mt-1 text-[12px] text-[var(--danger)]">
            {error}
          </p>
        )}
      </div>
      <button type="submit" disabled={saving || !value} className={`${BTN_SECONDARY} shrink-0`}>
        {saving ? "Saving" : "Save"}
      </button>
    </form>
  );
}

function ServiceRow({ item, canWrite, onSaveMissing }) {
  const [open, setOpen] = useState(false);
  const id = useId();
  const st = READINESS[item.status] || READINESS.missing;
  // Danger is reserved for a missing essential capability. An unconfigured
  // optional provider is not an error, so its "not configured" word stays quiet.
  const quiet = item.status === "missing" && !item.essential;
  const word = quiet ? "text-[var(--ink-3)]" : st.word;
  const missing = new Set(item.missing || []);
  return (
    <li>
      <button
        type="button"
        aria-expanded={open}
        aria-controls={open ? `${id}-body` : undefined}
        onClick={() => setOpen((value) => !value)}
        className="flex w-full items-center gap-3 py-3 text-left focus-visible:outline-none focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[var(--accent)]"
      >
        <ServiceLogo item={item} />
        <span className="min-w-0 flex-1">
          <span className="flex items-center gap-2">
            <span className="truncate text-[13px] font-medium text-[var(--ink)]">{item.label}</span>
            {item.essential && (
              <span className="shrink-0 rounded-full bg-[var(--surface-2)] px-1.5 py-0.5 text-[11px] font-medium text-[var(--ink-2)]">
                required
              </span>
            )}
            <span className={`ml-auto shrink-0 text-[12px] font-medium ${word}`}>{st.label}</span>
          </span>
          <span className="mt-0.5 block truncate text-[12px] text-[var(--ink-2)]">{item.capability}</span>
        </span>
        <Chevron open={open} />
      </button>
      {open && (
        <div id={`${id}-body`} className="pb-3 pl-10">
          {item.required?.length > 0 && (
            <ul className="divide-y divide-[var(--line)] overflow-hidden rounded-[8px] bg-[var(--surface-2)]">
              {item.required.map((env) => {
                const isMissing = missing.has(env);
                return (
                  <li key={env} className="px-3 py-2">
                    <div className="flex items-center gap-x-2.5">
                      <code className="pb-mono pb-contain flex-1 text-[12px] text-[var(--ink)]">{env}</code>
                      <span
                        className={`shrink-0 text-[12px] font-medium ${isMissing ? "text-[var(--danger)]" : "text-[var(--ink-3)]"}`}
                      >
                        {isMissing ? "missing" : "set"}
                      </span>
                    </div>
                    {isMissing && canWrite && (
                      <MissingEnvField env={env} onSave={onSaveMissing} />
                    )}
                  </li>
                );
              })}
            </ul>
          )}
          <p className="pb-contain mt-2 text-[12px] leading-relaxed text-[var(--ink-2)]">{item.capability}</p>
        </div>
      )}
    </li>
  );
}

export default function Settings() {
  const [theme, setThemeState] = useState(storedTheme);
  const themeSheen = useSelectionSheen(theme);
  const setTheme = (value) => {
    setThemeState(value);
    applyTheme(value);
  };
  useEffect(() => {
    const sync = () => setThemeState(storedTheme());
    window.addEventListener("pb-theme-change", sync);
    return () => window.removeEventListener("pb-theme-change", sync);
  }, []);
  const [providerKeys, setProviderKeys] = useState([]);
  const [providerPolicy, setProviderPolicy] = useState({
    loaded: false,
    runtimeWritesEnabled: false,
    managedBy: "deployment",
  });
  const [envName, setEnvName] = useState("");
  const [secretValue, setSecretValue] = useState("");
  const [keyError, setKeyError] = useState("");
  const [saving, setSaving] = useState(false);
  const [confirmingRemoval, setConfirmingRemoval] = useState(null);
  const [removing, setRemoving] = useState(null);
  const [readiness, setReadiness] = useState(null);
  const [readinessFailed, setReadinessFailed] = useState(false);

  const refreshProviderKeys = async () => {
    const data = await listProviderKeys();
    setProviderKeys(data.keys || []);
    setProviderPolicy({
      loaded: true,
      runtimeWritesEnabled: data.runtime_writes_enabled === true,
      managedBy: data.managed_by || "deployment",
    });
  };

  const refreshReadiness = async () => {
    const data = await getProviderReadiness();
    setReadiness(data);
    setReadinessFailed(false);
  };

  useEffect(() => {
    let alive = true;
    getProviderReadiness()
      .then((data) => { if (alive) setReadiness(data); })
      .catch(() => { if (alive) setReadinessFailed(true); });
    refreshProviderKeys().catch(() => {});
    return () => { alive = false; };
  }, []);

  const addProviderKey = async (event) => {
    event.preventDefault();
    if (!providerPolicy.runtimeWritesEnabled) return;
    setSaving(true);
    setKeyError("");
    try {
      await saveProviderKey(envName.trim().toUpperCase(), secretValue);
      setEnvName("");
      setSecretValue("");
      await Promise.all([refreshProviderKeys(), refreshReadiness()]);
    } catch (error) {
      setKeyError(error.message);
    } finally {
      setSaving(false);
    }
  };

  const saveMissingProviderKey = async (env, value) => {
    if (!providerPolicy.runtimeWritesEnabled) {
      throw new Error("Runtime credential changes are disabled in this environment.");
    }
    await saveProviderKey(env, value);
    await Promise.all([refreshProviderKeys(), refreshReadiness()]);
  };

  const removeProviderKey = async (env) => {
    if (!providerPolicy.runtimeWritesEnabled) return;
    setRemoving(env);
    setKeyError("");
    try {
      await deleteProviderKey(env);
      await Promise.all([refreshProviderKeys(), refreshReadiness()]);
      setConfirmingRemoval(null);
    } catch (error) {
      setKeyError(error.message);
    } finally {
      setRemoving(null);
    }
  };

  const providers = readiness?.providers || [];
  // Daytona first, then ready services, then partial, then unconfigured.
  const rank = (p) => {
    if (p.provider === "daytona") return 0;
    return { ready: 1, partial: 2, missing: 3 }[p.status] ?? 3;
  };
  const ordered = [...providers].sort((a, b) => rank(a) - rank(b));

  // The runtime credential controls are spelled once and placed into whichever
  // branch is live, so the enabled form and the collapsed dead form never drift
  // apart. When writes are disabled every control renders in its disabled state.
  const addKeyForm = (
    <form onSubmit={addProviderKey} className="grid gap-3 sm:grid-cols-[minmax(0,1fr)_auto]">
      <div className="grid gap-3">
        <input
          value={envName}
          onChange={(event) => setEnvName(event.target.value)}
          placeholder="PROVIDER_API_KEY"
          aria-label="Provider environment variable"
          disabled={!providerPolicy.runtimeWritesEnabled}
          className={`pb-mono ${INPUT} text-[12px] disabled:opacity-100 disabled:text-[var(--ink-3)]`}
          required
        />
        <input
          type="password"
          value={secretValue}
          onChange={(event) => setSecretValue(event.target.value)}
          placeholder="API key or provider value"
          aria-label="Provider credential value"
          autoComplete="off"
          disabled={!providerPolicy.runtimeWritesEnabled}
          className={`${INPUT} disabled:opacity-100 disabled:text-[var(--ink-3)]`}
          required
        />
      </div>
      <button
        type="submit"
        disabled={saving || !providerPolicy.runtimeWritesEnabled}
        className={`${BTN_PRIMARY} sm:self-start`}
      >
        {saving ? "Saving" : "Add key"}
      </button>
    </form>
  );

  const keyErrorNode = keyError ? (
    <p className="mt-3 text-[12px] text-[var(--danger)]" role="alert">
      {keyError}
    </p>
  ) : null;

  /* Every key in this list is present by definition, so a status dot on each
     row marks nothing: it is the same signal repeated for every entry. The
     row's presence is the signal. */
  const managedKeyList = providerKeys.length > 0 ? (
    <ul className="mt-4 divide-y divide-[var(--line)] overflow-hidden rounded-[12px] bg-[var(--surface-2)]">
      {providerKeys.map((key) => (
        <li key={key.env} className="flex flex-wrap items-center gap-x-2.5 gap-y-1 px-4 py-3">
          <code className="pb-mono pb-contain text-[12px] text-[var(--ink)]">{key.env}</code>
          <span className="text-[12px] text-[var(--ink-3)]">{key.source}</span>
          {key.source === "settings" &&
            (confirmingRemoval === key.env ? (
              <span
                className="ml-auto flex items-center gap-2"
                role="group"
                aria-label={`Confirm removal of ${key.env}`}
              >
                <button
                  type="button"
                  onClick={() => removeProviderKey(key.env)}
                  disabled={removing === key.env || !providerPolicy.runtimeWritesEnabled}
                  className={BTN_DANGER}
                >
                  {removing === key.env ? "Removing" : "Confirm remove"}
                </button>
                <button
                  type="button"
                  onClick={() => setConfirmingRemoval(null)}
                  disabled={removing === key.env}
                  className={BTN_SECONDARY}
                >
                  Cancel
                </button>
              </span>
            ) : (
              <button
                type="button"
                onClick={() => setConfirmingRemoval(key.env)}
                disabled={!providerPolicy.runtimeWritesEnabled}
                title={!providerPolicy.runtimeWritesEnabled ? "Managed by deployment" : undefined}
                className={`${BTN_DANGER} ml-auto`}
              >
                Remove
              </button>
            ))}
        </li>
      ))}
    </ul>
  ) : null;

  return (
    <div className="flex min-h-full flex-col">
      <header className={`${PAGE_HEADER} px-4 sm:px-8`}>
        <div className="mx-auto flex w-full max-w-canvas items-start justify-between gap-x-6 pb-3 pt-3.5">
          <div className="min-w-0">
            <h1 className={PAGE_TITLE}>Settings</h1>
            <p className="mt-0.5 max-w-[70ch] text-[13px] text-[var(--ink-2)]">
              What this deployment can currently prove, and which credentials it holds.
            </p>
          </div>
          <HeaderActions showReadiness={false} />
        </div>
      </header>

      <div className="mx-auto w-full max-w-[760px] px-4 pb-10 pt-8 sm:px-8">
        {readiness && !readinessFailed && (
          <div
            className={`mb-8 flex items-start gap-2.5 rounded-[12px] px-4 py-3 ${
              readiness.run_ready ? "bg-[var(--ok-tint)]" : "bg-[var(--danger-tint)]"
            }`}
            role="status"
          >
            {/* A banner states an outcome, so it carries the glyph for that
                outcome. A bare dot is a status colour with no meaning of its
                own, which is fine at row density and weak at this size. */}
            <span
              className={`mt-px shrink-0 ${
                readiness.run_ready ? "text-[var(--ok)]" : "text-[var(--danger)]"
              }`}
              aria-hidden="true"
            >
              {readiness.run_ready ? <ReadyIcon /> : <BlockedIcon />}
            </span>
            <p className="pb-contain text-[13px] leading-relaxed text-[var(--ink)]">
              {readiness.run_ready
                ? "Ready to run real benchmarks."
                : `Real benchmarks are blocked until these are configured: ${
                    (readiness.blocked_by || []).join(", ")
                  }.`}
            </p>
          </div>
        )}

        <section aria-labelledby="services-heading" className={`${PANEL} p-5`}>
          <h2 id="services-heading" className="text-[16px] font-semibold text-[var(--ink)]">
            Services
          </h2>
          <p className="mt-1 max-w-[62ch] text-[12px] leading-relaxed text-[var(--ink-2)]">
            Configuration check only. ProofBench does not contact any provider to build this list.
            Expand a service to see its environment variables.
          </p>

          {readinessFailed ? (
            <div className="mt-4 flex items-center gap-2.5 rounded-[12px] bg-[var(--warn-tint)] px-4 py-3">
              <StatusIcon tone="warn" size={13} className="mt-px text-[var(--warn)]" />
              <p className="text-[13px] text-[var(--ink)]">Provider readiness is unavailable right now.</p>
            </div>
          ) : !readiness ? (
            <div className="mt-4 flex flex-col gap-4">
              {[...Array(4)].map((_, i) => (
                <div key={i} className="flex items-center gap-3">
                  <Skeleton className="h-7 w-7 rounded-[8px]" />
                  <Skeleton className="h-3.5 w-44" />
                  <Skeleton className="ml-auto h-3 w-24" />
                </div>
              ))}
            </div>
          ) : (
            <ul aria-label="Services" className="mt-2 divide-y divide-[var(--line)]">
              {ordered.map((item) => (
                <ServiceRow
                  key={item.provider}
                  item={item}
                  canWrite={providerPolicy.runtimeWritesEnabled}
                  onSaveMissing={saveMissingProviderKey}
                />
              ))}
            </ul>
          )}
        </section>

        <section aria-labelledby="runtime-heading" className={`${PANEL} mt-8 p-5`}>
          <h2 id="runtime-heading" className="text-[16px] font-semibold text-[var(--ink)]">
            Runtime credentials
          </h2>
          <p className="mt-1 max-w-[62ch] text-[12px] leading-relaxed text-[var(--ink-2)]">
            Injected into sandboxes so a candidate tool can read its own key. Write only, never read
            back, never included in a report.
          </p>

          {providerPolicy.runtimeWritesEnabled ? (
            <div className="mt-4">
              {addKeyForm}
              {keyErrorNode}
              {managedKeyList}
            </div>
          ) : (
            <div className="mt-4">
              {providerPolicy.loaded && (
                <p
                  className="rounded-[12px] bg-[var(--surface-2)] px-3.5 py-2.5 text-[12px] text-[var(--ink-2)]"
                  role="status"
                >
                  Managed by deployment. Runtime credential changes are disabled in this environment.
                </p>
              )}
              <div className="mt-3">
                <Collapse
                  title="System-managed keys"
                  summary={
                    providerKeys.length === 0
                      ? "None stored"
                      : `${providerKeys.length} ${providerKeys.length === 1 ? "key" : "keys"}`
                  }
                  defaultOpen
                >
                  {addKeyForm}
                  {keyErrorNode}
                  {managedKeyList}
                </Collapse>
              </div>
            </div>
          )}
        </section>

        <section className={`${PANEL} mt-8 p-5`} aria-labelledby="appearance-heading">
          <h2 id="appearance-heading" className="text-[16px] font-semibold text-[var(--ink)]">
            Appearance
          </h2>
          <p className="mt-1 max-w-[65ch] text-[13px] leading-relaxed text-[var(--ink-2)]">
            Theme for this browser. System follows the operating system preference.
          </p>
          <div role="radiogroup" aria-labelledby="appearance-heading" className="mt-4 inline-flex rounded-full bg-[var(--surface-2)] p-1">
            {THEME_CHOICES.map((choice) => (
              <button
                key={choice.value}
                type="button"
                role="radio"
                aria-checked={theme === choice.value}
                onClick={() => setTheme(choice.value)}
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

        <section className={`${PANEL} mt-8 p-5`} aria-labelledby="about-heading">
          <h2 id="about-heading" className="text-[16px] font-semibold text-[var(--ink)]">
            About this deployment
          </h2>
          <p className="mt-1.5 max-w-[65ch] text-[13px] leading-relaxed text-[var(--ink-2)]">
            ProofBench benchmarks invoice-extraction tools against your own labelled data, runs them
            in isolated Daytona sandboxes, and scores results deterministically against ground truth.
          </p>

          <dl className="mt-6 grid grid-cols-1 gap-x-[24px] gap-y-8 md:grid-cols-2">
            <div>
              <dt className="pb-eyebrow">Status</dt>
              <dd className="mt-1.5 max-w-[65ch] text-[13px] leading-relaxed text-[var(--ink-2)]">
                Proprietary, pre-release software run locally by a single operator. It is not a
                hosted or supported product. The source may be publicly visible, which is not
                permission to use it.
              </dd>
            </div>
            <div>
              <dt className="pb-eyebrow">Licensing</dt>
              <dd className="mt-1.5 max-w-[65ch] text-[13px] leading-relaxed text-[var(--ink-2)]">
                Proprietary software. All rights are reserved by its copyright holder, an individual
                developer. Using, copying, modifying, or distributing it requires prior written
                permission. See the LICENSE file in the source repository. Third-party dependencies
                are not covered by that license and remain under their own terms.
              </dd>
            </div>
            <div>
              <dt className="pb-eyebrow">Deployment</dt>
              <dd className="mt-1.5 max-w-[65ch] text-[13px] leading-relaxed text-[var(--ink-2)]">
                This is a local instance, run by whoever operates this server. There is no public or
                hosted ProofBench service. Uploaded datasets, run artifacts, and reports stay on this
                deployment and are kept until you delete them, unless the operator has set
                PROOFBENCH_RETENTION_DAYS to a positive number. Benchmark runs send documents to
                disposable sandboxes and to whichever third-party providers are enabled above, under
                those providers' own terms.
              </dd>
            </div>
            <div>
              <dt className="pb-eyebrow">No service commitment</dt>
              <dd className="mt-1.5 max-w-[65ch] text-[13px] leading-relaxed text-[var(--ink-2)]">
                No availability, support, or response commitment is offered for this software, and no
                privacy notice or terms of service have been published for it.
              </dd>
            </div>
          </dl>

          <p className="mt-6 max-w-[65ch] text-[12px] leading-relaxed text-[var(--ink-3)]">
            CONTRACTS.md, LICENSE, and the operations and data-handling docs live in the source
            repository. They are not served by this app.
          </p>
        </section>
      </div>
    </div>
  );
}
