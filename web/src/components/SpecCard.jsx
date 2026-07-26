import { useEffect, useMemo, useState } from "react";
import { safeHttpUrl } from "../linkSafety.js";
import { safeVisibleText, sanitizeForDisplay } from "../displaySafety.js";
import { BTN_DANGER, BTN_PRIMARY, PANEL } from "./ui.jsx";

export default function SpecCard({ spec, datasetId, onRun, onStop, running, stopping, interactionDisabled = false }) {
  const safeSpec = useMemo(() => sanitizeForDisplay(spec || {}), [spec]);
  const [candidates, setCandidates] = useState([]);

  useEffect(() => {
    setCandidates(safeSpec.candidates || []);
  }, [safeSpec]);

  const removeCandidate = (name) => {
    setCandidates((current) => current.filter((candidate) => candidate.name !== name));
  };

  const dataset = datasetId || safeSpec?.dataset?.dataset_id ||
    (safeSpec?.dataset?.path ? "Attached dataset" : "None selected");
  const isAssessment = safeSpec?.benchmark_type === "tool_assessment";

  return (
    <section className="pb-glass min-w-0 rounded-[24px] p-5 shadow-[var(--shadow-card)]" aria-label="Benchmark spec">
      <div className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1">
        <h2 className="text-[16px] font-semibold tracking-[-0.01em] text-[var(--ink)]">Benchmark spec</h2>
        {safeSpec?.category && (
          <span className="pb-contain text-[12px] text-[var(--ink-2)]">
            {safeVisibleText(safeSpec.category)}
          </span>
        )}
      </div>

      <div className="mt-4">
        <div className="pb-eyebrow">Candidates</div>
        {candidates.length === 0 ? (
          <p className="mt-1.5 text-[13px] text-[var(--ink-2)]">
            No candidates left. Keep at least one to run.
          </p>
        ) : (
          /* Chips, not full-width rows: five candidates as stacked rows ran
             taller than the reply they belong to. Wrapped chips fit the same
             five on two lines. */
          <ul className="mt-2 flex flex-wrap gap-1.5">
            {candidates.map((candidate, index) => {
              const docsUrl = safeHttpUrl(candidate.docs_url);
              const name = safeVisibleText(candidate.display_name || candidate.name || "Unnamed candidate");
              return (
                <li
                  key={`${safeVisibleText(candidate.name)}-${index}`}
                  className="group inline-flex min-w-0 max-w-full items-center gap-1.5 rounded-full bg-[var(--surface-2)] py-1 pl-3 pr-1"
                >
                  <span className="pb-contain min-w-0 truncate text-[13px] text-[var(--ink)]">
                    {isAssessment ? name : safeVisibleText(candidate.name)}
                  </span>
                  {isAssessment && docsUrl && (
                    <a
                      href={docsUrl}
                      target="_blank"
                      rel="noreferrer"
                      className="shrink-0 text-[12px] text-[var(--accent)] hover:underline"
                    >
                      docs
                    </a>
                  )}
                  <button
                    type="button"
                    onClick={() => removeCandidate(candidate.name)}
                    disabled={interactionDisabled}
                    aria-label={`Remove ${name} from benchmark`}
                    className="inline-flex h-6 w-6 shrink-0 items-center justify-center rounded-full text-[var(--ink-3)] transition-colors duration-150 ease-out-quart hover:bg-[var(--danger-tint)] hover:text-[var(--danger)] disabled:opacity-40"
                  >
                    <span aria-hidden="true">×</span>
                  </button>
                </li>
              );
            })}
          </ul>
        )}

        {isAssessment && safeSpec?.objective && (
          <div className="mt-4">
            <div className="pb-eyebrow">Company objective</div>
            <p className="pb-contain mt-1 max-w-[65ch] text-[13px] leading-relaxed text-[var(--ink-2)]">
              {safeVisibleText(safeSpec.objective)}
            </p>
          </div>
        )}

        {!isAssessment && safeSpec?.fields && (
          <div className="mt-4">
            <div className="pb-eyebrow">Fields</div>
            <div className="mt-1 flex flex-wrap gap-x-3 gap-y-1">
              {safeSpec.fields.map((field, index) => (
                <span key={`${safeVisibleText(field)}-${index}`} className="pb-mono pb-contain text-[12px] text-[var(--ink-2)]">
                  {safeVisibleText(field)}
                </span>
              ))}
            </div>
          </div>
        )}

        {!isAssessment && (
          <div className="mt-4 text-[12px] text-[var(--ink-3)]">
            Dataset <span className="pb-mono pb-contain text-[var(--ink-2)]">{safeVisibleText(dataset)}</span>
          </div>
        )}
      </div>

      <div className="mt-5 flex flex-wrap items-center gap-2">
        <button
          type="button"
          onClick={() => onRun({ ...safeSpec, candidates })}
          disabled={running || interactionDisabled || candidates.length === 0}
          className={BTN_PRIMARY}
        >
          {running ? "Running..." : "Run benchmark"}
        </button>
        {running && (
          <button type="button" onClick={onStop} disabled={stopping} className={BTN_DANGER}>
            {stopping ? "Stopping..." : "Stop"}
          </button>
        )}
        {!running && (
          <p className="text-[12px] text-[var(--ink-3)]">
            Nothing executes until you confirm this specification.
          </p>
        )}
      </div>
    </section>
  );
}
