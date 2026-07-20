import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { listSessions } from "../api.js";

const PANEL =
  "rounded-[14px] border border-[color:var(--border)] bg-[color:var(--surface)] shadow-card";
const SKEL = "animate-pulse rounded-md bg-[color:var(--surface-2)]";
const BTN_GHOST =
  "inline-flex h-9 items-center justify-center gap-1.5 rounded-md px-3 text-[13px] font-medium text-[color:var(--text-2)] transition-colors duration-150 hover:bg-[color:var(--surface-2)] hover:text-[color:var(--text)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[color:color-mix(in_oklab,var(--accent)_40%,transparent)]";

const INFO_PHASES = [
  "INTAKE",
  "DOCS_INTEL",
  "ADAPTER_GEN",
  "PROVISIONING",
  "BUILDING",
  "VALIDATING",
  "RUNNING",
];
const WARN_PHASES = ["COLLATING", "EVALUATING", "REPORTING"];

const PHASE_BADGE = {
  info: "bg-[color:color-mix(in_oklab,var(--info)_10%,transparent)] text-[color:var(--info)]",
  warn: "bg-[color:color-mix(in_oklab,var(--warn)_10%,transparent)] text-[color:var(--warn)]",
  ok: "bg-[color:color-mix(in_oklab,var(--ok)_10%,transparent)] text-[color:var(--ok)]",
  err: "bg-[color:color-mix(in_oklab,var(--err)_10%,transparent)] text-[color:var(--err)]",
  neutral: "bg-[color:var(--surface-2)] text-[color:var(--text-3)]",
};

function phaseTone(phase) {
  const p = (phase || "").toUpperCase();
  if (p.includes("FAIL")) return "err";
  if (p === "DONE") return "ok";
  if (WARN_PHASES.includes(p)) return "warn";
  if (INFO_PHASES.includes(p)) return "info";
  return "neutral";
}

function PhaseBadge({ phase }) {
  const label = (phase || "unknown").toLowerCase().replace(/_/g, " ");
  return (
    <span
      className={`inline-flex items-center rounded-full px-2 py-0.5 text-[11px] font-medium ${PHASE_BADGE[phaseTone(phase)]}`}
    >
      {label}
    </span>
  );
}

function formatTime(value) {
  const d = new Date(value);
  if (!value || Number.isNaN(d.getTime())) return "unknown";
  return d.toLocaleString();
}

function ChevronIcon() {
  return (
    <svg
      width="14"
      height="14"
      viewBox="0 0 16 16"
      fill="none"
      aria-hidden="true"
    >
      <path
        d="M6 3.5 10.5 8 6 12.5"
        stroke="currentColor"
        strokeWidth="1.5"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

export default function Runs() {
  const [sessions, setSessions] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  const load = async () => {
    try {
      const list = await listSessions();
      setSessions(Array.isArray(list) ? list : []);
      setError(null);
    } catch (e) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    load();
    const t = setInterval(load, 5000);
    return () => clearInterval(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const total = sessions.length;
  const done = sessions.filter(
    (s) => (s.phase || "").toUpperCase() === "DONE"
  ).length;
  const failed = sessions.filter((s) =>
    (s.phase || "").toUpperCase().includes("FAIL")
  ).length;
  const inProgress = total - done - failed;

  return (
    <div className="mx-auto w-full max-w-5xl px-8 py-8">
      <h1 className="text-[22px] font-semibold tracking-tight text-[color:var(--text)]">
        Runs
      </h1>

      <div className="mt-3 flex flex-wrap items-center gap-x-6 gap-y-1 text-[13px] text-[color:var(--text-2)]">
        <span>
          <span className="font-mono font-medium text-[color:var(--text)]">
            {total}
          </span>{" "}
          total
        </span>
        <span>
          <span className="font-mono font-medium text-[color:var(--text)]">
            {done}
          </span>{" "}
          done
        </span>
        <span>
          <span className="font-mono font-medium text-[color:var(--text)]">
            {inProgress}
          </span>{" "}
          in progress
        </span>
      </div>

      {error && (
        <div className="mt-6 flex items-center gap-3 rounded-[12px] border border-[color:color-mix(in_oklab,var(--err)_35%,transparent)] bg-[color:color-mix(in_oklab,var(--err)_10%,transparent)] px-4 py-3">
          <span className="h-2 w-2 shrink-0 rounded-full bg-[color:var(--err)]" />
          <p className="text-[13px] text-[color:var(--text)]">
            Could not load sessions: {error}
          </p>
          <button onClick={load} className={`${BTN_GHOST} ml-auto`}>
            Retry
          </button>
        </div>
      )}

      <div className={`mt-6 ${PANEL} overflow-hidden`}>
        {loading ? (
          <div className="flex flex-col gap-3 p-4">
            {[...Array(5)].map((_, i) => (
              <div key={i} className="flex items-center gap-4">
                <div className={`h-4 w-1/3 ${SKEL}`} />
                <div className={`h-4 w-16 ${SKEL}`} />
                <div className={`h-4 w-28 ${SKEL}`} />
              </div>
            ))}
          </div>
        ) : sessions.length === 0 ? (
          <div className="flex flex-col items-center gap-3 py-16">
            <p className="text-[13px] text-[color:var(--text-2)]">
              No runs yet. Start a benchmark to see results here.
            </p>
            <Link to="/app/benchmark" className={BTN_GHOST}>
              New benchmark
            </Link>
          </div>
        ) : (
          <table className="w-full border-collapse text-left text-[13px]">
            <thead>
              <tr className="bg-[color:var(--surface-2)]">
                <th className="px-4 py-2.5 text-[12px] font-semibold text-[color:var(--text-2)]">
                  Title
                </th>
                <th className="px-4 py-2.5 text-[12px] font-semibold text-[color:var(--text-2)]">
                  Phase
                </th>
                <th className="px-4 py-2.5 text-[12px] font-semibold text-[color:var(--text-2)]">
                  Created
                </th>
                <th className="w-12 px-4 py-2.5" aria-label="Open" />
              </tr>
            </thead>
            <tbody className="divide-y divide-[color:var(--border)]">
              {sessions.map((s) => (
                <tr
                  key={s.id}
                  className="transition-colors duration-150 hover:bg-[color:color-mix(in_oklab,var(--accent-soft)_50%,transparent)]"
                >
                  <td className="max-w-0 truncate px-4 py-2.5 text-[color:var(--text)]">
                    {s.title || "Untitled"}
                  </td>
                  <td className="px-4 py-2.5">
                    <PhaseBadge phase={s.phase} />
                  </td>
                  <td className="px-4 py-2.5 font-mono text-[12px] text-[color:var(--text-2)]">
                    {formatTime(s.created_at)}
                  </td>
                  <td className="px-4 py-2.5 text-right">
                    <Link
                      to="/app/benchmark"
                      title="Open in benchmark console"
                      aria-label="Open in benchmark console"
                      className="inline-flex h-7 w-7 items-center justify-center rounded-md text-[color:var(--text-3)] transition-colors duration-150 hover:bg-[color:var(--surface-2)] hover:text-[color:var(--accent)]"
                    >
                      <ChevronIcon />
                    </Link>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}
