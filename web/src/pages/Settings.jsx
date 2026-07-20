import { useEffect, useState } from "react";
import { deleteProviderKey, getHealth, listProviderKeys, saveProviderKey } from "../api.js";

const PANEL =
  "rounded-[14px] border border-[color:var(--border)] bg-[color:var(--surface)] shadow-card";
const SKEL = "animate-pulse rounded-md bg-[color:var(--surface-2)]";
const CHIP =
  "pb-pill inline-flex max-w-full items-center gap-1.5 px-2 py-1 font-mono text-[12px] text-[color:var(--text)] transition-colors duration-150 hover:border-[color:var(--border-strong)] hover:text-[color:var(--accent)]";

const KEYS = [
  {
    env: "DAYTONA_API_KEY",
    caption: "Daytona: sandbox fleet",
    aliases: ["daytona"],
  },
  {
    env: "MOONSHOT_API_KEY",
    caption: "Moonshot: orchestrator model",
    aliases: ["moonshot", "kimi"],
  },
  {
    env: "NOSANA_API_KEY",
    caption: "Nosana: GPU candidate",
    aliases: ["nosana"],
  },
  {
    env: "DOUBLEWORD_API_KEY",
    caption: "Doubleword: Real-mode batch assessment + candidate",
    aliases: ["doubleword"],
  },
  {
    env: "OXYLABS_USERNAME",
    caption: "Oxylabs: docs search",
    aliases: ["oxylabs"],
  },
  {
    env: "OPENAI_API_KEY",
    caption: "OpenAI: vision candidate",
    aliases: ["openai"],
  },
];

// Health payload shape is not frozen; walk it recursively and match aliases.
function flattenLeaves(value, path, out) {
  if (value && typeof value === "object") {
    for (const [k, v] of Object.entries(value)) {
      flattenLeaves(v, path ? `${path}.${k}` : k, out);
    }
  } else {
    out.push([path, value]);
  }
}

function toBool(v) {
  if (typeof v === "boolean") return v;
  if (typeof v === "number") return v > 0;
  if (typeof v === "string") {
    const s = v.trim().toLowerCase();
    return !["", "missing", "false", "no", "unset", "null", "none"].includes(
      s
    );
  }
  return v != null;
}

function resolveKey(health, aliases) {
  const leaves = [];
  flattenLeaves(health, "", leaves);
  const norm = (s) => s.toLowerCase().replace(/[^a-z0-9]/g, "");
  for (const alias of aliases) {
    const a = norm(alias);
    const hit = leaves.find(([path]) => norm(path).includes(a));
    if (hit) return toBool(hit[1]);
  }
  return null; // not reported by the endpoint
}

const STATUS = {
  set: {
    dot: "bg-[color:var(--ok)]",
    word: "text-[color:var(--ok)]",
    label: "set",
  },
  missing: {
    dot: "bg-[color:var(--err)]",
    word: "text-[color:var(--err)]",
    label: "missing",
  },
  unknown: {
    dot: "bg-[color:var(--text-3)]",
    word: "text-[color:var(--text-3)]",
    label: "unknown",
  },
};

export default function Settings() {
  const [health, setHealth] = useState(null);
  const [loading, setLoading] = useState(true);
  const [failed, setFailed] = useState(false);
  const [providerKeys, setProviderKeys] = useState([]);
  const [envName, setEnvName] = useState("");
  const [secretValue, setSecretValue] = useState("");
  const [keyError, setKeyError] = useState("");
  const [saving, setSaving] = useState(false);

  const refreshProviderKeys = async () => {
    const data = await listProviderKeys();
    setProviderKeys(data.keys || []);
  };

  useEffect(() => {
    let alive = true;
    getHealth()
      .then((data) => {
        if (alive) setHealth(data);
      })
      .catch(() => {
        if (alive) setFailed(true);
      })
      .finally(() => {
        if (alive) setLoading(false);
      });
    refreshProviderKeys().catch(() => {});
    return () => {
      alive = false;
    };
  }, []);

  const statusFor = (key) => {
    if (failed) return "unknown";
    const v = resolveKey(health, key.aliases);
    if (v === null) return "unknown";
    return v ? "set" : "missing";
  };

  const addProviderKey = async (event) => {
    event.preventDefault();
    setSaving(true);
    setKeyError("");
    try {
      await saveProviderKey(envName.trim().toUpperCase(), secretValue);
      setEnvName("");
      setSecretValue("");
      await refreshProviderKeys();
    } catch (error) {
      setKeyError(error.message);
    } finally {
      setSaving(false);
    }
  };

  const removeProviderKey = async (env) => {
    try {
      await deleteProviderKey(env);
      await refreshProviderKeys();
    } catch (error) {
      setKeyError(error.message);
    }
  };

  return (
    <div className="mx-auto w-full max-w-3xl px-8 py-8">
      <h1 className="text-[22px] font-semibold tracking-tight text-[color:var(--text)]">
        Settings
      </h1>

      <section className="mt-8">
        <h2 className="text-[16px] font-semibold text-[color:var(--text)]">
          API keys
        </h2>
        <p className="mt-1 text-[12px] text-[color:var(--text-3)]">
          Keys are read from .env on the server. Nothing secret is shown here.
        </p>

        {failed && (
          <div className="mt-4 flex items-center gap-2.5 rounded-[10px] border border-[color:color-mix(in_oklab,var(--warn)_35%,transparent)] bg-[color:color-mix(in_oklab,var(--warn)_10%,transparent)] px-4 py-3">
            <span className="h-2 w-2 shrink-0 rounded-full bg-[color:var(--warn)]" />
            <p className="text-[13px] text-[color:var(--text)]">
              Server health endpoint unavailable, showing static checklist
            </p>
          </div>
        )}

        <div className={`mt-4 ${PANEL} overflow-hidden`}>
          {loading ? (
            <div className="flex flex-col gap-4 p-4">
              {[...Array(6)].map((_, i) => (
                <div key={i} className="flex items-center gap-3">
                  <div className={`h-2 w-2 rounded-full ${SKEL}`} />
                  <div className={`h-3.5 w-44 ${SKEL}`} />
                  <div className={`ml-auto h-3 w-40 ${SKEL}`} />
                </div>
              ))}
            </div>
          ) : (
            <ul className="divide-y divide-[color:var(--border)]">
              {KEYS.map((key) => {
                const st = STATUS[statusFor(key)];
                return (
                  <li key={key.env} className="flex items-center gap-3 px-4 py-3">
                    <span
                      className={`h-2 w-2 shrink-0 rounded-full ${st.dot}`}
                      title={st.label}
                    />
                    <code className="font-mono text-[12px] text-[color:var(--text)]">
                      {key.env}
                    </code>
                    <span className={`text-[11px] font-medium ${st.word}`}>
                      {st.label}
                    </span>
                    <span className="ml-auto text-right text-[12px] text-[color:var(--text-3)]">
                      {key.caption}
                    </span>
                  </li>
                );
              })}
            </ul>
          )}
        </div>

        <div className={`mt-6 ${PANEL} p-4`}>
          <h3 className="text-[14px] font-semibold text-[color:var(--text)]">
            Add a provider credential
          </h3>
          <p className="mt-1 max-w-[65ch] text-[12px] leading-relaxed text-[color:var(--text-3)]">
            Use the environment variable required by the provider, for example ANTHROPIC_API_KEY or MISTRAL_API_KEY. Values are held only in this running server, never shown in the app, logs, or reports.
          </p>
          <form onSubmit={addProviderKey} className="mt-4 grid gap-3 sm:grid-cols-[minmax(0,1fr)_minmax(0,1.4fr)_auto]">
            <input
              value={envName}
              onChange={(event) => setEnvName(event.target.value)}
              placeholder="PROVIDER_API_KEY"
              aria-label="Provider environment variable"
              className="h-9 rounded-md border border-[color:var(--border-strong)] bg-[color:var(--surface)] px-3 font-mono text-[12px] text-[color:var(--text)] outline-none focus:ring-2 focus:ring-[color:color-mix(in_oklab,var(--accent)_40%,transparent)]"
              required
            />
            <input
              type="password"
              value={secretValue}
              onChange={(event) => setSecretValue(event.target.value)}
              placeholder="API key or provider value"
              aria-label="Provider credential value"
              autoComplete="off"
              className="h-9 rounded-md border border-[color:var(--border-strong)] bg-[color:var(--surface)] px-3 text-[13px] text-[color:var(--text)] outline-none focus:ring-2 focus:ring-[color:color-mix(in_oklab,var(--accent)_40%,transparent)]"
              required
            />
            <button
              type="submit"
              disabled={saving}
              className="h-9 rounded-md bg-[color:var(--accent)] px-3 text-[13px] font-medium text-[color:var(--surface)] transition-colors hover:bg-[color:var(--accent-hover)] disabled:opacity-50"
            >
              {saving ? "Saving" : "Add key"}
            </button>
          </form>
          {keyError && <p className="mt-3 text-[12px] text-[color:var(--err)]">{keyError}</p>}
          {providerKeys.length > 0 && (
            <ul className="mt-4 divide-y divide-[color:var(--border)] rounded-md border border-[color:var(--border)]">
              {providerKeys.map((key) => (
                <li key={key.env} className="flex items-center gap-3 px-3 py-2.5">
                  <span className="h-2 w-2 rounded-full bg-[color:var(--ok)]" />
                  <code className="font-mono text-[12px] text-[color:var(--text)]">{key.env}</code>
                  <span className="text-[12px] text-[color:var(--text-3)]">{key.source}</span>
                  {key.source === "settings" && (
                    <button onClick={() => removeProviderKey(key.env)} className="ml-auto text-[12px] text-[color:var(--err)] hover:underline">
                      Remove
                    </button>
                  )}
                </li>
              ))}
            </ul>
          )}
        </div>
      </section>

      <section className="mt-10">
        <h2 className="text-[16px] font-semibold text-[color:var(--text)]">
          About
        </h2>
        <div className={`mt-3 ${PANEL} p-6`}>
          <p className="text-[14px] font-semibold text-[color:var(--text)]">
            ProofBench
          </p>
          <p className="mt-1 max-w-[65ch] text-[13px] leading-relaxed text-[color:var(--text-2)]">
            Benchmarks invoice-extraction tools against your own labelled
            data, runs them in isolated Daytona sandboxes, and scores results
            deterministically against ground truth.
          </p>
          <a
            href="/CONTRACTS.md"
            target="_blank"
            rel="noreferrer"
            className={`${CHIP} mt-4`}
          >
            CONTRACTS.md
            <svg
              width="11"
              height="11"
              viewBox="0 0 16 16"
              fill="none"
              aria-hidden="true"
            >
              <path
                d="M5 11 11 5M6.5 5H11v4.5"
                stroke="currentColor"
                strokeWidth="1.5"
                strokeLinecap="round"
                strokeLinejoin="round"
              />
            </svg>
          </a>
        </div>
      </section>
    </div>
  );
}
