import { useState } from "react";

/* Phase -> state color, per DESIGN.md: building (info), validating (warn),
   running (info), done (ok), failed (err). */
function phaseColor(phase) {
  const p = String(phase || "").toLowerCase();
  if (p === "done") return "var(--ok)";
  if (p === "failed") return "var(--err)";
  if (p === "validating") return "var(--warn)";
  if (p === "building" || p === "running") return "var(--info)";
  return "var(--text-3)";
}

const TRACE_STATUS = {
  start: { color: "var(--info)", pulse: true },
  ok: { color: "var(--ok)", pulse: false },
  error: { color: "var(--err)", pulse: false },
};

function PhaseBadge({ label }) {
  const c = phaseColor(label);
  return (
    <span
      className="inline-flex items-center gap-1.5 rounded-full px-2 py-0.5 text-[11px]"
      style={{
        backgroundColor: `color-mix(in oklab, ${c} 10%, transparent)`,
        color: c,
      }}
    >
      <span
        className="h-1.5 w-1.5 rounded-full"
        style={{ backgroundColor: c }}
      />
      {String(label).toLowerCase()}
    </span>
  );
}

function SandboxPanel({ name, lines }) {
  const [open, setOpen] = useState(true);
  const phase = lines.length ? lines[lines.length - 1].phase : "building";
  return (
      <div className="pb-card pb-hover-lift overflow-hidden rounded-[10px] border border-[var(--border)]">
      <button
        onClick={() => setOpen((o) => !o)}
        className="flex w-full items-center justify-between bg-[var(--surface-2)] px-3 py-2 text-left transition-colors hover:bg-[var(--accent-soft)]"
      >
        <span className="flex items-center gap-2 font-mono text-[12px] text-[var(--text)]">
          <span className="text-[var(--text-3)]">{open ? "▾" : "▸"}</span>
          {name}
        </span>
        <PhaseBadge label={phase} />
      </button>
      {open && (
        <div className="max-h-56 overflow-y-auto bg-[var(--code-bg)] px-3 py-2 font-mono text-[12px] leading-relaxed text-[oklch(0.85_0.02_160)]">
          {lines.length === 0 && (
            <div className="h-3 w-32 animate-pulse rounded bg-[color-mix(in_oklab,oklch(0.85_0.02_160)_15%,transparent)]" />
          )}
          {lines.map((l, i) => {
            const failed = l.phase === "failed" || /error|fail|traceback/i.test(l.line);
            const repair = !failed && /repair|retry|attempt/i.test(l.line);
            return (
              <div
                key={i}
                className={
                  failed
                    ? "text-[color-mix(in_oklab,var(--err)_55%,oklch(0.92_0.01_160))]"
                    : repair
                    ? "text-[color-mix(in_oklab,var(--warn)_55%,oklch(0.92_0.01_160))]"
                    : undefined
                }
              >
                <span className="mr-2 select-none text-[color-mix(in_oklab,oklch(0.85_0.02_160)_35%,transparent)]">
                  $
                </span>
                {l.line}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

function TraceDetail({ children }) {
  const parts = String(children).split(/(https?:\/\/[^\s]+)/g);
  return (
    <div className="text-[12px] text-[var(--text-2)]">
      {parts.map((part, index) =>
        /^https?:\/\//.test(part) ? (
          <a
            key={index}
            href={part}
            target="_blank"
            rel="noreferrer"
            className="break-all text-[var(--accent)] hover:underline"
          >
            {part}
          </a>
        ) : (
          part
        )
      )}
    </div>
  );
}

export default function AgentTraceCard({ trace, sandboxLogs, phaseState }) {
  const sandboxes = Object.keys(sandboxLogs || {});
  return (
    <div className="pb-card pb-card-hover pb-hover-lift p-4">
      <div className="mb-3 flex items-center gap-2">
        <span className="text-[16px] font-semibold text-[var(--text)]">
          Agent trace
        </span>
        {phaseState?.phase && <PhaseBadge label={phaseState.phase} />}
      </div>

      {phaseState?.candidates && (
        <div className="mb-3 flex flex-wrap gap-2">
          {Object.entries(phaseState.candidates).map(([name, st]) => (
            <span
              key={name}
              className="inline-flex items-center gap-1.5 rounded-full bg-[var(--surface-2)] px-2 py-0.5 text-[11px] text-[var(--text-2)]"
            >
              <span
                className="h-1.5 w-1.5 rounded-full"
                style={{ backgroundColor: phaseColor(st) }}
              />
              {name}: {st}
            </span>
          ))}
        </div>
      )}

      {trace.length > 0 && (
        <div className="mb-3 flex flex-col gap-1">
          {trace.map((t, i) => {
            const st = TRACE_STATUS[t.status] || TRACE_STATUS.start;
            return (
              <div key={i} className="flex items-start gap-2">
                <span
                  className={`mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full ${
                    st.pulse ? "animate-pulse" : ""
                  }`}
                  style={{ backgroundColor: st.color }}
                />
                <div className="min-w-0">
                  <span className="font-mono text-[12px] text-[var(--text)]">
                    {t.tool}
                  </span>
                  {t.args_summary && (
                    <span className="ml-1.5 text-[12px] text-[var(--text-2)]">
                      {t.args_summary}
                    </span>
                  )}
                  {t.detail && (
                    <TraceDetail>{t.detail}</TraceDetail>
                  )}
                </div>
              </div>
            );
          })}
        </div>
      )}

      {sandboxes.length > 0 && (
        <div className="flex flex-col gap-2">
          {sandboxes.map((name) => (
            <SandboxPanel key={name} name={name} lines={sandboxLogs[name]} />
          ))}
        </div>
      )}
    </div>
  );
}
