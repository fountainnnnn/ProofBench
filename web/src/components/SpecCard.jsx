import { useState, useEffect } from "react";

export default function SpecCard({ spec, onRun, onStop, running, stopping }) {
  const [candidates, setCandidates] = useState([]);

  useEffect(() => {
    setCandidates(spec?.candidates || []);
  }, [spec]);

  const removeCandidate = (name) => {
    setCandidates((cs) => cs.filter((c) => c.name !== name));
  };

  const dataset = spec?.dataset?.path || "(none)";
  const isAssessment = spec?.benchmark_type === "tool_assessment";

  return (
    <div className="pb-card pb-card-hover pb-hover-lift p-4">
      <div className="mb-3 flex items-center gap-2">
        <span className="text-[16px] font-semibold text-[var(--text)]">
          Benchmark spec
        </span>
        {spec?.category && (
          <span className="text-[13px] text-[var(--text-2)]">{spec.category}</span>
        )}
      </div>

      <div className="mb-3">
        <div className="mb-1.5 text-[11px] font-medium uppercase tracking-wide text-[var(--text-3)]">
          Candidates
        </div>
        <div className="flex flex-wrap gap-2">
          {candidates.length === 0 && (
            <span className="text-[12px] text-[var(--text-3)]">
              No candidates left. Keep at least one to run.
            </span>
          )}
          {candidates.map((c) => (
            <span
              key={c.name}
              className="flex items-center gap-1.5 rounded-full bg-[var(--surface-2)] px-3 py-1 text-[12px] text-[var(--text)]"
            >
              {isAssessment ? (c.display_name || c.name) : c.name}
              {isAssessment && c.docs_url && (
                <a
                  href={c.docs_url}
                  target="_blank"
                  rel="noreferrer"
                  className="text-[11px] text-[var(--accent)] hover:underline"
                >
                  docs
                </a>
              )}
              <button
                onClick={() => removeCandidate(c.name)}
                className="text-[var(--text-3)] transition-colors hover:text-[var(--err)]"
                title="Remove"
              >
                ×
              </button>
            </span>
          ))}
        </div>
      </div>

      {isAssessment && spec?.objective && (
        <div className="mb-3">
          <div className="mb-1.5 text-[11px] font-medium uppercase tracking-wide text-[var(--text-3)]">
            Company objective
          </div>
          <p className="max-w-[65ch] text-[12px] leading-relaxed text-[var(--text-2)]">
            {spec.objective}
          </p>
        </div>
      )}

      {!isAssessment && spec?.fields && (
        <div className="mb-3">
          <div className="mb-1.5 text-[11px] font-medium uppercase tracking-wide text-[var(--text-3)]">
            Fields
          </div>
          <div className="flex flex-wrap gap-x-3 gap-y-1">
            {spec.fields.map((f) => (
              <span key={f} className="font-mono text-[12px] text-[var(--text-2)]">
                {f}
              </span>
            ))}
          </div>
        </div>
      )}

      {!isAssessment && (
        <div className="mb-4 text-[12px] text-[var(--text-3)]">
          Dataset: <span className="font-mono text-[var(--text-2)]">{dataset}</span>
        </div>
      )}

      <div className="flex gap-2">
        <button onClick={() => onRun({ ...spec, candidates })} disabled={running || candidates.length === 0}
          className="h-9 rounded-md bg-[var(--accent)] px-4 text-[13px] font-medium text-[var(--surface)] transition-colors hover:bg-[var(--accent-hover)] disabled:opacity-40">
          {running ? "Running..." : "Run benchmark"}
        </button>
        {running && <button onClick={onStop} disabled={stopping}
          className="h-9 rounded-md border border-[color-mix(in_oklab,var(--err)_45%,transparent)] px-4 text-[13px] font-medium text-[var(--err)] transition-colors hover:bg-[color-mix(in_oklab,var(--err)_8%,transparent)] disabled:opacity-50">
          {stopping ? "Stopping..." : "Stop"}
        </button>}
      </div>
    </div>
  );
}
