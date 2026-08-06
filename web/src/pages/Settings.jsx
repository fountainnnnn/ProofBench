import { useEffect, useId, useState } from "react";
import {
  deleteProviderKey,
  fetchBrandLogos,
  getProviderReadiness,
  getScraperOrder,
  getSettingsDefaults,
  listProviderKeys,
  revealProviderKey,
  saveProviderKey,
  saveScraperOrder,
  saveSettingsDefaults,
} from "../api.js";
import { ensureBrandAssets, runtimeBrandAssetFor } from "../brandIcons.js";
import { BTN_DANGER, BTN_PRIMARY, BTN_SECONDARY, Collapse, INPUT, PAGE_HEADER, PAGE_TITLE, PANEL, SHEEN_SWIPE, Skeleton, useSelectionSheen } from "../components/ui.jsx";
import HeaderActions from "../components/HeaderActions.jsx";
import IntegrationAgentPanel from "../components/IntegrationAgentPanel.jsx";
import StatusIcon from "../components/StatusIcon.jsx";
import { THEME_CHOICES, applyTheme, storedTheme } from "../theme.js";
import daytonaLogo from "../assets/provider-logos/daytona.svg";
import deepseekLogo from "../assets/provider-logos/deepseek.svg";
import doublewordLogo from "../assets/provider-logos/doubleword.svg";
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
// promise above that rendering this list never contacts a provider. A provider
// with no mark here is not an error: the row falls back to a monogram.
const PROVIDER_LOGOS = {
  daytona: daytonaLogo,
  deepseek: deepseekLogo,
  doubleword: doublewordLogo,
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
  // Bundled marks first so the common providers render instantly and offline;
  // anything added later resolves through the same runtime brand cache the
  // Overview already uses, rather than shipping a blank tile.
  const logo = PROVIDER_LOGOS[item.provider] || runtimeBrandAssetFor(item.provider);
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

function EyeIcon({ off }) {
  return (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      {off ? (
        <>
          <path d="M10.7 5.1A9.8 9.8 0 0 1 12 5c5.5 0 9 6.5 9 6.5a15.6 15.6 0 0 1-2.7 3.6" />
          <path d="M6.3 6.7A15.7 15.7 0 0 0 3 11.5S6.5 18 12 18a9.6 9.6 0 0 0 4-.9" />
          <path d="m3 3 18 18" />
          <path d="M9.9 9.7a3 3 0 0 0 4.2 4.2" />
        </>
      ) : (
        <>
          <path d="M3 11.5S6.5 5 12 5s9 6.5 9 6.5-3.5 6.5-9 6.5-9-6.5-9-6.5Z" />
          <circle cx="12" cy="11.5" r="2.8" />
        </>
      )}
    </svg>
  );
}

function TrashIcon() {
  return (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M4 7h16" />
      <path d="M10 11v6M14 11v6" />
      <path d="M6 7l1 12a2 2 0 0 0 2 2h6a2 2 0 0 0 2-2l1-12" />
      <path d="M9 7V5a2 2 0 0 1 2-2h2a2 2 0 0 1 2 2v2" />
    </svg>
  );
}

/* One credential, in the place the operator already looks for it.
 *
 * The value is never held in the parent: it is fetched on demand by the reveal
 * action and dropped again on hide, so the page does not sit with every secret
 * in memory. A saved value is not echoed back either — after a write the row
 * re-reads its mask from the server rather than trusting what was typed.
 *
 * `missing` is authoritative from readiness, NOT derived from whether a masked
 * value is in the listing: a provisioning key like DAYTONA_API_KEY is set but
 * outside the sandbox env, so "no mask" must not read as "missing". */
function EnvRow({ env, stored, missing = false, canWrite, onSave, onRemove }) {
  const [editing, setEditing] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const [value, setValue] = useState("");
  const [revealed, setRevealed] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const id = useId();

  const configured = Boolean(stored);
  const secret = stored?.secret !== false;
  // Only a runtime override can be removed. A deployment value comes from the
  // environment, so "remove" would be a lie: it would come straight back.
  const removable = Boolean(onRemove) && stored?.source === "settings";

  const remove = async () => {
    setBusy(true);
    setError("");
    try {
      await onRemove(env);
      setConfirming(false);
      setRevealed("");
    } catch (failure) {
      setError(failure.message || "Could not remove this provider credential.");
    } finally {
      setBusy(false);
    }
  };

  const toggleReveal = async () => {
    if (revealed) {
      setRevealed("");
      return;
    }
    setBusy(true);
    setError("");
    try {
      const data = await revealProviderKey(env);
      setRevealed(String(data?.value || ""));
    } catch (failure) {
      setError(failure.message || "Could not read this provider credential.");
    } finally {
      setBusy(false);
    }
  };

  const submit = async (event) => {
    event.preventDefault();
    if (busy || !value) return;
    setBusy(true);
    setError("");
    try {
      await onSave(env, value);
      setValue("");
      setEditing(false);
      setRevealed("");
    } catch (failure) {
      setError(failure.message || "Could not save this provider credential.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <li className="px-3 py-2.5">
      <div className="flex flex-wrap items-center gap-x-2.5 gap-y-1">
        <code className="pb-mono pb-contain min-w-0 flex-1 truncate text-[12px] text-[var(--ink)]">
          {env}
        </code>
        {configured ? (
          <span className="pb-mono shrink-0 text-[12px] text-[var(--ink-3)]">
            {/* The mask stays inline; a revealed value wraps below, so this only
                ever holds a short token. "set" covers a non-secret or a secret
                the server chose not to mask. */}
            {revealed ? "shown below" : (secret ? stored.masked : "") || "set"}
          </span>
        ) : missing ? (
          <span className="shrink-0 text-[12px] font-medium text-[var(--danger)]">missing</span>
        ) : (
          <span className="shrink-0 text-[12px] text-[var(--ink-3)]">not set</span>
        )}
        {configured && secret && stored.revealable && (
          <button
            type="button"
            onClick={toggleReveal}
            disabled={busy}
            aria-label={revealed ? `Hide ${env}` : `Reveal ${env}`}
            className="flex h-7 w-7 shrink-0 items-center justify-center rounded-[8px] text-[var(--ink-3)] transition-colors duration-150 hover:bg-[var(--surface)] hover:text-[var(--ink)] focus-visible:outline-none focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[var(--accent)]"
          >
            <EyeIcon off={Boolean(revealed)} />
          </button>
        )}
        {canWrite && !editing && !confirming && (
          <button
            type="button"
            onClick={() => setEditing(true)}
            className={`${BTN_SECONDARY} shrink-0`}
          >
            {configured ? "Change" : "Add"}
          </button>
        )}
        {/* Confirmation is inline, where the act is, rather than in a dialog
            that hides which row is about to be cleared. */}
        {removable && !editing && (confirming ? (
          <span
            className="flex shrink-0 items-center gap-2"
            role="group"
            aria-label={`Confirm removal of ${env}`}
          >
            <button type="button" onClick={remove} disabled={busy || !canWrite} className={BTN_DANGER}>
              {busy ? "Removing" : "Confirm remove"}
            </button>
            <button
              type="button"
              onClick={() => setConfirming(false)}
              disabled={busy}
              className={BTN_SECONDARY}
            >
              Cancel
            </button>
          </span>
        ) : (
          <button
            type="button"
            onClick={() => setConfirming(true)}
            disabled={!canWrite}
            aria-label={`Remove ${env}`}
            title="Remove"
            className="flex h-7 w-7 shrink-0 items-center justify-center rounded-[8px] text-[var(--ink-3)] transition-colors duration-150 hover:bg-[var(--danger-tint)] hover:text-[var(--danger)] focus-visible:outline-none focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[var(--accent)]"
          >
            <TrashIcon />
          </button>
        ))}
      </div>

      {/* A revealed key can be very long, so it gets its own full-width block and
          breaks across lines rather than pushing the row's controls off-screen. */}
      {revealed && (
        <div className="mt-2 rounded-[8px] bg-[var(--surface)] px-3 py-2">
          <code className="pb-mono block break-all text-[12px] leading-relaxed text-[var(--ink)]">
            {revealed}
          </code>
        </div>
      )}

      {editing && canWrite && (
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
              disabled={busy}
              className={`${INPUT} text-[12px]`}
              autoFocus
              required
            />
          </div>
          <button type="submit" disabled={busy || !value} className={`${BTN_SECONDARY} shrink-0`}>
            {busy ? "Saving" : "Save"}
          </button>
          <button
            type="button"
            onClick={() => { setEditing(false); setValue(""); setError(""); }}
            disabled={busy}
            className={`${BTN_SECONDARY} shrink-0`}
          >
            Cancel
          </button>
        </form>
      )}
      {error && (
        <p id={`${id}-error`} role="alert" className="mt-1 text-[12px] text-[var(--danger)]">
          {error}
        </p>
      )}
    </li>
  );
}

function PlusIcon() {
  return (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" aria-hidden="true">
      <path d="M12 5v14M5 12h14" />
    </svg>
  );
}

/* Add a credential for a provider the built-in list does not cover.
 *
 * The env NAME is the identifier, because that is what the backend and the
 * sandbox key off; the value is write-only like every other secret here. The
 * server validates the name shape, so a typo comes back as an error rather
 * than being stored as an unusable key. */
function AddCredential({ onSave }) {
  const [open, setOpen] = useState(false);
  const [env, setEnv] = useState("");
  const [value, setValue] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const id = useId();

  const reset = () => { setEnv(""); setValue(""); setError(""); };

  const submit = async (event) => {
    event.preventDefault();
    if (busy || !env.trim() || !value) return;
    setBusy(true);
    setError("");
    try {
      await onSave(env.trim().toUpperCase(), value);
      reset();
      setOpen(false);
    } catch (failure) {
      setError(failure.message || "Could not add this credential.");
    } finally {
      setBusy(false);
    }
  };

  if (!open) {
    return (
      <button
        type="button"
        onClick={() => setOpen(true)}
        className="mt-4 flex items-center gap-1.5 text-[13px] font-medium text-[var(--accent)] hover:text-[var(--accent-hover)] focus-visible:outline-none focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[var(--accent)]"
      >
        <PlusIcon />
        Add a service
      </button>
    );
  }

  return (
    <form onSubmit={submit} className="mt-4 rounded-[12px] bg-[var(--surface-2)] p-3.5">
      <p className="text-[13px] font-medium text-[var(--ink)]">Add a service</p>
      <p className="pb-contain mt-0.5 max-w-[52ch] text-[12px] leading-relaxed text-[var(--ink-2)]">
        Name the environment variable and paste its value. Use the provider&apos;s exact key name,
        for example <code className="pb-mono text-[var(--ink)]">MISTRAL_API_KEY</code>.
      </p>
      <div className="mt-3 grid gap-2 sm:grid-cols-[minmax(0,1fr)_minmax(0,1.2fr)_auto]">
        <input
          value={env}
          onChange={(e) => setEnv(e.target.value)}
          placeholder="PROVIDER_API_KEY"
          aria-label="Environment variable name"
          autoComplete="off"
          spellCheck={false}
          disabled={busy}
          className={`pb-mono ${INPUT} text-[12px] uppercase placeholder:normal-case`}
          autoFocus
          required
        />
        <input
          type="password"
          value={value}
          onChange={(e) => setValue(e.target.value)}
          placeholder="Value"
          aria-label="Credential value"
          autoComplete="off"
          disabled={busy}
          className={`${INPUT} text-[12px]`}
          required
        />
        <div className="flex items-start gap-2">
          <button type="submit" disabled={busy || !env.trim() || !value} className={`${BTN_PRIMARY} shrink-0`}>
            {busy ? "Adding" : "Add"}
          </button>
          <button
            type="button"
            onClick={() => { reset(); setOpen(false); }}
            disabled={busy}
            className={`${BTN_SECONDARY} shrink-0`}
          >
            Cancel
          </button>
        </div>
      </div>
      {error && (
        <p id={`${id}-error`} role="alert" className="mt-2 text-[12px] text-[var(--danger)]">
          {error}
        </p>
      )}
    </form>
  );
}

/* Advisory, and separate from `essential`, which is what actually blocks a run.
   OpenRouter is the one provider worth suggesting without requiring: it covers
   every LLM capability on its own. */
const RECOMMENDED = new Set(["openrouter"]);

function ServiceRow({ item, canWrite, keysByEnv, onSaveKey, onRemoveKey }) {
  const [open, setOpen] = useState(false);
  const id = useId();
  const st = READINESS[item.status] || READINESS.missing;
  // Danger is reserved for a missing essential capability. An unconfigured
  // optional provider is not an error, so its "not configured" word stays quiet.
  const quiet = item.status === "missing" && !item.essential;
  const word = quiet ? "text-[var(--ink-3)]" : st.word;
  // Optional settings are shown alongside the required ones: a model override
  // is the second thing an operator comes here to change, and hiding it forced
  // them back to the env file.
  const envNames = [...(item.required || []), ...(item.optional || [])];
  // Authoritative missing set: readiness only lists a required env that is
  // actually absent, so an unset optional is never wrongly flagged, and a set
  // key outside the sandbox env (DAYTONA_API_KEY) is never flagged either.
  const missingSet = new Set(item.missing || []);
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
            {item.essential ? (
              <span className="shrink-0 rounded-full bg-[var(--warn-tint)] px-1.5 py-0.5 text-[11px] font-medium text-[var(--warn)]">
                required
              </span>
            ) : RECOMMENDED.has(item.provider) ? (
              <span className="shrink-0 rounded-full bg-[var(--suggest-tint)] px-1.5 py-0.5 text-[11px] font-medium text-[var(--suggest)]">
                recommended
              </span>
            ) : null}
            <span className={`ml-auto shrink-0 text-[12px] font-medium ${word}`}>{st.label}</span>
          </span>
          <span className="mt-0.5 block truncate text-[12px] text-[var(--ink-2)]">{item.capability}</span>
        </span>
        <Chevron open={open} />
      </button>
      {open && (
        <div id={`${id}-body`} className="pb-3 pl-10">
          {envNames.length > 0 && (
            <ul className="divide-y divide-[var(--line)] overflow-hidden rounded-[8px] bg-[var(--surface-2)]">
              {envNames.map((env) => (
                <EnvRow
                  key={env}
                  env={env}
                  stored={keysByEnv.get(env)}
                  missing={missingSet.has(env)}
                  canWrite={canWrite}
                  onSave={onSaveKey}
                  onRemove={onRemoveKey}
                />
              ))}
            </ul>
          )}
          <p className="pb-contain mt-2 text-[12px] leading-relaxed text-[var(--ink-2)]">{item.capability}</p>
        </div>
      )}
    </li>
  );
}

/* The model id for the provider that is pinned right now.
 *
 * Shown inline rather than sending the operator back to the service row: having
 * just chosen a gateway, naming its model is the very next thing they need, and
 * the built-in default is a guess. */
function ModelField({ option, canWrite, onSave, onSaved }) {
  const [value, setValue] = useState(option.model || "");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const id = useId();

  // A different provider becomes pinned, so the field must follow it.
  useEffect(() => { setValue(option.model || ""); setError(""); }, [option.name, option.model]);

  const dirty = value.trim() && value.trim() !== (option.model || "");

  const submit = async (event) => {
    event.preventDefault();
    if (!dirty || busy) return;
    setBusy(true);
    setError("");
    try {
      await onSave(option.model_env, value.trim());
      onSaved?.();
    } catch (failure) {
      setError(failure.message || "Could not save that model.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <form onSubmit={submit} className="mt-2.5">
      <label htmlFor={id} className="block text-[12px] text-[var(--ink-2)]">
        Model for {option.label}
      </label>
      <div className="mt-1 flex items-start gap-2">
        <input
          id={id}
          value={value}
          onChange={(event) => setValue(event.target.value)}
          disabled={!canWrite || busy}
          spellCheck={false}
          autoComplete="off"
          placeholder={option.model || "provider/model-name"}
          className={`pb-mono ${INPUT} max-w-[24rem] text-[12px]`}
        />
        {dirty && canWrite && (
          <button type="submit" disabled={busy} className={`${BTN_SECONDARY} shrink-0`}>
            {busy ? "Saving" : "Save"}
          </button>
        )}
      </div>
      {option.model_is_default && !dirty && (
        <p className="mt-1 text-[12px] text-[var(--ink-3)]">
          Built-in default. Type a model id to override it.
        </p>
      )}
      {error && (
        <p role="alert" className="mt-1 text-[12px] text-[var(--danger)]">{error}</p>
      )}
    </form>
  );
}

/* Which model answers for each kind of work.
 *
 * Only providers that actually hold a key are offered: a choice that cannot
 * take effect is not a choice, and listing every possible vendor turned this
 * into a catalogue of things the deployment does not have.
 *
 * "Auto" is not the same as picking the first provider: it follows the
 * configured order, so adding a key changes the answer without an edit here. */
function DefaultModels({ canWrite, onSaveKey }) {
  const [state, setState] = useState(null);
  const [saving, setSaving] = useState("");
  const [error, setError] = useState("");

  useEffect(() => {
    let alive = true;
    getSettingsDefaults()
      .then((data) => { if (alive) setState(data); })
      .catch(() => { if (alive) setError("Could not load the default providers."); });
    return () => { alive = false; };
  }, []);

  const choose = async (capability, provider) => {
    setSaving(capability);
    setError("");
    try {
      const saved = await saveSettingsDefaults({ [capability]: provider || "" });
      setState(saved);
    } catch {
      setError("Could not save that default.");
    } finally {
      setSaving("");
    }
  };

  if (error && !state) {
    return <p className="mt-3 text-[13px] text-[var(--danger)]" role="status">{error}</p>;
  }
  if (!state) return <Skeleton className="mt-4 h-28 w-full" />;

  return (
    <div className="mt-4 space-y-5">
      {error && (
        <p className="text-[12px] text-[var(--danger)]" role="alert">{error}</p>
      )}
      {state.llm.map((row) => {
        const available = row.options.filter((option) => option.configured);
        const pinnedOption = available.find((option) => option.name === row.pinned);
        return (
        <div key={row.capability}>
          <p className="text-[13px] font-medium text-[var(--ink)]">{row.label}</p>
          <p className="pb-contain mt-0.5 max-w-[62ch] text-[12px] leading-relaxed text-[var(--ink-2)]">
            {row.detail}
          </p>
          {available.length === 0 ? (
            <p className="mt-2 text-[12px] text-[var(--ink-3)]">
              Add a key for one of {row.options.map((o) => o.label).join(", ")} to choose a default.
            </p>
          ) : (
          <div
            role="radiogroup"
            aria-label={`Default provider for ${row.label}`}
            className="mt-2 flex flex-wrap gap-1.5"
          >
            {[{ name: "", label: "Auto" }, ...available].map((option) => {
              const active = (row.pinned || "") === option.name;
              return (
                <button
                  key={option.name || "auto"}
                  type="button"
                  role="radio"
                  aria-checked={active}
                  disabled={saving === row.capability}
                  onClick={() => choose(row.capability, option.name)}
                  className={`min-h-8 rounded-full px-3 text-[12px] font-medium transition-colors duration-150 disabled:opacity-60 ${
                    active
                      ? "bg-[var(--ink)] text-[var(--surface)]"
                      : "bg-[var(--surface-2)] text-[var(--ink-2)] hover:text-[var(--ink)]"
                  }`}
                >
                  {option.label}
                </button>
              );
            })}
          </div>
          )}
          {/* A pinned provider still needs a model named: a gateway serves many,
              and its built-in default is rarely the one an operator wants. */}
          {pinnedOption && (
            <ModelField
              option={pinnedOption}
              canWrite={canWrite}
              onSave={onSaveKey}
              onSaved={() => getSettingsDefaults().then(setState).catch(() => {})}
            />
          )}
          {/* Auto hides which provider actually won, so name it. */}
          {!row.pinned && row.selected && (
            <p className="mt-1.5 text-[12px] text-[var(--ink-3)]">
              Currently resolving to {row.selected}.
            </p>
          )}
        </div>
        );
      })}
    </div>
  );
}

function GripIcon() {
  return (
    <svg width="12" height="12" viewBox="0 0 16 16" aria-hidden="true" fill="currentColor">
      <circle cx="6" cy="4" r="1.2" /><circle cx="10" cy="4" r="1.2" />
      <circle cx="6" cy="8" r="1.2" /><circle cx="10" cy="8" r="1.2" />
      <circle cx="6" cy="12" r="1.2" /><circle cx="10" cy="12" r="1.2" />
    </svg>
  );
}

/* An (i) that reveals its note on hover or keyboard focus. A real popover, not
   a native title, so the copy can breathe and explain the SearXNG/Crawl4AI
   pairing without being clipped to a one-line tooltip. */
function InfoDot({ label, text }) {
  if (!text) return null;
  return (
    <span className="group relative inline-flex shrink-0">
      <button
        type="button"
        aria-label={label}
        className="flex h-4 w-4 items-center justify-center rounded-full border border-[var(--line)] text-[10px] font-semibold text-[var(--ink-3)] transition-colors duration-150 hover:border-[var(--accent)] hover:text-[var(--accent)] focus-visible:outline-none focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[var(--accent)]"
      >
        i
      </button>
      <span
        role="tooltip"
        className="pointer-events-none absolute bottom-full left-1/2 z-10 mb-1.5 w-[240px] -translate-x-1/2 rounded-[10px] bg-[var(--ink)] px-3 py-2 text-[11.5px] leading-relaxed text-[var(--surface)] opacity-0 shadow-[var(--shadow-card)] transition-opacity duration-150 group-hover:opacity-100 group-focus-within:opacity-100"
      >
        {text}
      </span>
    </span>
  );
}

// The short role line under a provider name, so the split between the free pair
// is legible without opening the tooltip. Paid providers do both, so stay quiet.
const SCRAPER_ROLE_LABEL = {
  search: "Search only — finds pages",
  read: "Read only — fetches page content",
};

/* Which scraping provider answers first.
 *
 * A list rather than a single choice, because the others are not disabled by
 * demoting them — a search that returns nothing ends an intake turn, so every
 * provider holding credentials stays in the chain as a fallback.
 *
 * Dragging is the direct way to express "this one first". The arrow buttons stay
 * because drag alone is unreachable by keyboard, and a reorder nobody can do
 * without a mouse is not an accessible control. */
function ScraperOrder() {
  const [state, setState] = useState(null);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [dragging, setDragging] = useState(null);
  const [over, setOver] = useState(null);

  useEffect(() => {
    let alive = true;
    getScraperOrder()
      .then((data) => { if (alive) setState(data); })
      .catch(() => { if (alive) setError("Could not load the scraping order."); });
    return () => { alive = false; };
  }, []);

  const commit = async (next) => {
    /* Optimistic, then reconciled with what the server normalized: the order is
       a preference, so waiting on a round trip to redraw makes it feel broken. */
    setState((current) => ({
      ...current, providers: next, order: next.map((row) => row.name),
    }));
    setSaving(true);
    setError("");
    try {
      const saved = await saveScraperOrder(next.map((row) => row.name));
      setState((current) => ({
        ...current,
        order: saved.order,
        providers: saved.order.map(
          (name) => next.find((row) => row.name === name) || { name, label: name },
        ),
      }));
    } catch {
      setError("Could not save the order.");
      getScraperOrder().then(setState).catch(() => {});
    } finally {
      setSaving(false);
    }
  };

  const drop = (target) => {
    const rows = state?.providers || [];
    setOver(null);
    const from = dragging;
    setDragging(null);
    if (from === null || target === null || from === target) return;
    const next = [...rows];
    const [moved] = next.splice(from, 1);
    next.splice(target, 0, moved);
    commit(next);
  };

  const move = async (index, delta) => {
    const target = index + delta;
    const rows = state?.providers || [];
    if (target < 0 || target >= rows.length) return;
    const next = [...rows];
    [next[index], next[target]] = [next[target], next[index]];
    await commit(next);
  };

  return (
    <div>
      <h3 className="text-[13px] font-medium text-[var(--ink)]">Documentation sources</h3>
      <p className="pb-contain mt-0.5 max-w-[62ch] text-[12px] leading-relaxed text-[var(--ink-2)]">
        The order these providers are tried when searching for tools and reading their
        documentation. The first one that answers is used; the rest stay as fallbacks, so a
        provider being slow or down never ends a benchmark without candidates.
      </p>

      {!state && !error && <Skeleton className="mt-4 h-24 w-full" />}
      {error && (
        <p className="mt-3 text-[13px] text-[var(--danger)]" role="status">{error}</p>
      )}

      {state && (
        /* No overflow-hidden: it would clip an info tooltip that reaches past a
           row. The rows carry no background fill, so the rounded border needs no
           clip to look right. */
        <ol className="mt-4 divide-y divide-[var(--line)] rounded-[16px] border border-[var(--line)]">
          {state.providers.map((row, index) => {
            /* Dragging down lands the row after the one hovered, dragging up
               lands it before, so the indicator sits on the matching edge. */
            const active = dragging !== null && over === index && dragging !== index;
            const lineBelow = active && dragging < index;
            const showLine = active;
            return (
            <li
              key={row.name}
              draggable={!saving}
              onDragStart={(event) => {
                setDragging(index);
                // Without a payload Firefox refuses to start the drag at all.
                event.dataTransfer.effectAllowed = "move";
                try { event.dataTransfer.setData("text/plain", row.name); } catch { /* older DnD */ }
              }}
              onDragEnd={() => { setDragging(null); setOver(null); }}
              onDragOver={(event) => {
                event.preventDefault();
                event.dataTransfer.dropEffect = "move";
                setOver(index);
              }}
              onDrop={(event) => { event.preventDefault(); drop(index); }}
              className={`relative flex items-center gap-3 px-3.5 py-2.5 ${
                dragging === index ? "opacity-40" : ""
              } ${saving ? "" : "cursor-grab active:cursor-grabbing"}`}
            >
              {/* A line at the edge the row would land on, rather than a filled
                  highlight: the fill covered two rows at once and left the
                  actual insertion point ambiguous. */}
              {showLine && (
                /* An insertion caret, not a hairline: a 2px rule at a row edge
                   sat on top of the divider and read as part of the frame. The
                   knob at the leading end is what makes it unmistakable.
                   Inset inside the row because the list clips its overflow. */
                <span
                  aria-hidden="true"
                  className={`pointer-events-none absolute inset-x-2 flex items-center ${
                    lineBelow ? "bottom-px" : "top-px"
                  }`}
                >
                  <span className="h-[7px] w-[7px] shrink-0 rounded-full bg-[var(--accent)]" />
                  <span className="h-[3px] flex-1 rounded-full bg-[var(--accent)]" />
                </span>
              )}
              <span
                aria-hidden="true"
                title="Drag to reorder"
                className="shrink-0 text-[var(--ink-3)]"
              >
                <GripIcon />
              </span>
              <span className="pb-mono w-4 shrink-0 text-[12px] text-[var(--ink-3)]">
                {index + 1}
              </span>
              <span className="min-w-0 flex-1">
                <span className="flex items-center gap-1.5">
                  <span className="truncate text-[14px] text-[var(--ink)]">{row.label}</span>
                  {row.free && (
                    <span className="shrink-0 rounded-full bg-[var(--ok-tint)] px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-[var(--ok)]">
                      Free
                    </span>
                  )}
                  <InfoDot label={`About ${row.label}`} text={row.hint} />
                </span>
                {/* A self-hosted provider is available exactly when its services
                    answer, so it reports each one by name and URL instead of
                    leaving the operator to guess what "not configured" meant. */}
                {row.status ? (
                  <span className="mt-0.5 flex flex-wrap items-center gap-x-3 gap-y-0.5">
                    {row.status.services.map((service) => (
                      <span key={service.name} className="flex items-center gap-1.5">
                        <span
                          aria-hidden="true"
                          className={`h-1.5 w-1.5 shrink-0 rounded-full ${
                            service.running ? "bg-[var(--ok)]" : "bg-[var(--ink-3)]"
                          }`}
                        />
                        <span className="text-[12px] text-[var(--ink-2)]">{service.name}</span>
                        <span className="pb-mono text-[11px] text-[var(--ink-3)]">
                          {service.running ? "running" : "not running"}
                        </span>
                      </span>
                    ))}
                  </span>
                ) : !row.configured ? (
                  /* First in line but unable to answer is worth saying out loud;
                     it is otherwise invisible until a benchmark is slow. */
                  <span className="block text-[12px] text-[var(--ink-3)]">
                    No credentials configured — skipped
                  </span>
                ) : SCRAPER_ROLE_LABEL[row.role] ? (
                  <span className="block text-[12px] text-[var(--ink-3)]">
                    {SCRAPER_ROLE_LABEL[row.role]}
                  </span>
                ) : null}
              </span>
              <span className="flex shrink-0 items-center gap-1">
                <button
                  type="button"
                  onClick={() => move(index, -1)}
                  disabled={index === 0 || saving}
                  aria-label={`Move ${row.label} earlier`}
                  className="flex h-7 w-7 items-center justify-center rounded-[8px] text-[var(--ink-2)] transition-colors duration-150 hover:bg-[var(--surface-2)] hover:text-[var(--ink)] disabled:opacity-30 disabled:hover:bg-transparent"
                >
                  <svg width="14" height="14" viewBox="0 0 16 16" fill="none" aria-hidden="true">
                    <path d="M4 10 8 6l4 4" stroke="currentColor" strokeWidth="1.5"
                          strokeLinecap="round" strokeLinejoin="round" />
                  </svg>
                </button>
                <button
                  type="button"
                  onClick={() => move(index, 1)}
                  disabled={index === state.providers.length - 1 || saving}
                  aria-label={`Move ${row.label} later`}
                  className="flex h-7 w-7 items-center justify-center rounded-[8px] text-[var(--ink-2)] transition-colors duration-150 hover:bg-[var(--surface-2)] hover:text-[var(--ink)] disabled:opacity-30 disabled:hover:bg-transparent"
                >
                  <svg width="14" height="14" viewBox="0 0 16 16" fill="none" aria-hidden="true">
                    <path d="M4 6l4 4 4-4" stroke="currentColor" strokeWidth="1.5"
                          strokeLinecap="round" strokeLinejoin="round" />
                  </svg>
                </button>
              </span>
            </li>
            );
          })}
        </ol>
      )}
    </div>
  );
}

/* One place for "which provider does what", rather than a card for the scrapers
   and nothing at all for the models. */
function Defaults({ canWrite, onSaveKey }) {
  return (
    <section className={`${PANEL} mt-8 p-5`} aria-labelledby="defaults-heading">
      <h2 id="defaults-heading" className="text-[16px] font-semibold text-[var(--ink)]">
        Defaults
      </h2>
      <p className="mt-1 max-w-[65ch] text-[13px] leading-relaxed text-[var(--ink-2)]">
        Which provider serves each part of a run. Keys are set on the service itself, above.
      </p>
      <DefaultModels canWrite={canWrite} onSaveKey={onSaveKey} />
      <div className="mt-6 border-t border-[var(--line)] pt-5">
        <ScraperOrder />
      </div>
    </section>
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
  const [readiness, setReadiness] = useState(null);
  const [readinessFailed, setReadinessFailed] = useState(false);
  const [agentStatusVersion, setAgentStatusVersion] = useState(0);
  const [logoVersion, setLogoVersion] = useState(0);

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
    setAgentStatusVersion((value) => value + 1);
  };

  useEffect(() => {
    let alive = true;
    getProviderReadiness()
      .then((data) => { if (alive) setReadiness(data); })
      .catch(() => { if (alive) setReadinessFailed(true); });
    refreshProviderKeys().catch(() => {});
    return () => { alive = false; };
  }, []);

  /* Only providers with no bundled mark are looked up, so the usual list costs
     nothing and a newly added backend provider still gets a logo. The cache is
     module-level, so a resolved mark needs a nudge to repaint. */
  const unbrandedProviders = (readiness?.providers || [])
    .map((item) => item.provider)
    .filter((name) => !PROVIDER_LOGOS[name] && !runtimeBrandAssetFor(name))
    .join(",");
  useEffect(() => {
    if (!unbrandedProviders) return;
    let alive = true;
    ensureBrandAssets(unbrandedProviders.split(","), fetchBrandLogos)
      .then((found) => { if (alive && found) setLogoVersion((value) => value + 1); })
      .catch(() => {});
    return () => { alive = false; };
  }, [unbrandedProviders]);

  const saveProviderKeyValue = async (env, value) => {
    if (!providerPolicy.runtimeWritesEnabled) {
      throw new Error("Runtime credential changes are disabled in this environment.");
    }
    await saveProviderKey(env, value);
    await Promise.all([refreshProviderKeys(), refreshReadiness()]);
  };

  const removeProviderKeyValue = async (env) => {
    if (!providerPolicy.runtimeWritesEnabled) {
      throw new Error("Runtime credential changes are disabled in this environment.");
    }
    await deleteProviderKey(env);
    await Promise.all([refreshProviderKeys(), refreshReadiness()]);
  };

  // Keyed lookup so a row finds its own credential without scanning the list.
  const keysByEnv = new Map((providerKeys || []).map((key) => [key.env, key]));

  const providers = readiness?.providers || [];
  // Daytona first, then ready services, then partial, then unconfigured.
  const rank = (p) => {
    if (p.provider === "daytona") return 0;
    return { ready: 1, partial: 2, missing: 3 }[p.status] ?? 3;
  };
  const ordered = [...providers].sort((a, b) => rank(a) - rank(b));

  /* A credential can be stored under a name no provider entry declares, and
     folding the credential list into the service rows would otherwise make it
     invisible and impossible to remove. Anything unclaimed gets its own row. */
  const claimed = new Set(
    providers.flatMap((item) => [...(item.required || []), ...(item.optional || [])]),
  );
  const unclaimed = (providerKeys || []).filter(
    (key) => key.source === "settings" && !claimed.has(key.env),
  );

  return (
    <div className="flex min-h-full flex-col xl:h-full xl:overflow-hidden">
      <header className={`${PAGE_HEADER} shrink-0 px-4 sm:px-8`}>
        <div className="mx-auto flex w-full max-w-canvas items-start justify-between gap-x-6 pb-3 pt-3.5">
          <div className="min-w-0">
            <span className="pb-eyebrow-glow">Configuration</span>
            <h1 className={`${PAGE_TITLE} mt-1`}>Settings</h1>
            <p className="mt-0.5 max-w-[70ch] text-[13px] text-[var(--ink-2)]">
              What this deployment can currently prove, and which credentials it holds.
            </p>
          </div>
          <HeaderActions showReadiness={false} />
        </div>
      </header>

      {/* Tight to the header on a wide screen. The columns share this padding,
          so it is not a place to buy margin for one of them. */}
      <div className="mx-auto w-full max-w-[1640px] px-4 pb-10 pt-8 sm:px-8 xl:flex xl:min-h-0 xl:flex-1 xl:flex-col xl:overflow-hidden xl:pb-0 xl:pt-4">
        {/* Two columns on a wide screen: configuration on the left, the agent
            that acts on it on the right. They stack below `lg`, so the settings
            keep their single-column reading order on a phone. */}
        {/* The row is pinned to minmax(0,1fr) on xl: an auto row would size to
            its tallest child, so a long agent thread grew the card past the
            viewport instead of scrolling inside it. */}
        <div className="grid items-start gap-6 xl:min-h-0 xl:flex-1 xl:grid-cols-[minmax(0,1.7fr)_minmax(360px,1fr)] xl:grid-rows-[minmax(0,1fr)] xl:gap-8">
          <div
            className="min-w-0 xl:h-full xl:overflow-y-auto xl:pb-10 xl:pr-3"
            data-settings-scroll-region
            aria-label="Settings configuration"
          >
            {readiness && !readinessFailed && (
              <div
                className={`mb-6 flex items-start gap-2.5 rounded-[12px] px-4 py-3 ${
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
                Readiness is a configuration check, so building this list never calls a provider.
                Expand a service to read or replace its keys.
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
                      keysByEnv={keysByEnv}
                      onSaveKey={saveProviderKeyValue}
                      onRemoveKey={removeProviderKeyValue}
                    />
                  ))}
                </ul>
              )}

              {unclaimed.length > 0 && (
                <div className="mt-5">
                  <h3 className="text-[13px] font-medium text-[var(--ink)]">Other credentials</h3>
                  <p className="pb-contain mt-0.5 max-w-[62ch] text-[12px] leading-relaxed text-[var(--ink-2)]">
                    Stored here but not claimed by any service above.
                  </p>
                  <ul
                    aria-label="Other credentials"
                    className="mt-2 divide-y divide-[var(--line)] overflow-hidden rounded-[8px] bg-[var(--surface-2)]"
                  >
                    {unclaimed.map((key) => (
                      <EnvRow
                        key={key.env}
                        env={key.env}
                        stored={key}
                        canWrite={providerPolicy.runtimeWritesEnabled}
                        onSave={saveProviderKeyValue}
                        onRemove={removeProviderKeyValue}
                      />
                    ))}
                  </ul>
                </div>
              )}

              {providerPolicy.runtimeWritesEnabled && (
                <AddCredential onSave={saveProviderKeyValue} />
              )}
            </section>

            <Defaults
              canWrite={providerPolicy.runtimeWritesEnabled}
              onSaveKey={saveProviderKeyValue}
            />

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
                in isolated sandboxes, and scores results deterministically against ground truth.
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

          {/* self-stretch overrides the grid's items-start for this column only:
              without it the panel is sized by its content, so the thread has no
              ceiling to scroll against and the whole page scrolls instead. */}
          <IntegrationAgentPanel
            refreshKey={agentStatusVersion}
            className="h-[80vh] xl:h-full xl:self-stretch xl:pb-6"
          />
        </div>
      </div>
    </div>
  );
}
