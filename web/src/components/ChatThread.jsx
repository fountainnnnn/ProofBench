import { useEffect, useRef } from "react";
import ReactMarkdown from "react-markdown";
import SpecCard from "./SpecCard.jsx";
import AgentTraceCard from "./AgentTraceCard.jsx";
import ResultsCard from "./ResultsCard.jsx";

function Bubble({ role, streaming, children }) {
  const isUser = role === "user";
  if (isUser) {
    return (
      <div className="flex justify-end">
        <div className="max-w-[65ch] rounded-[12px] bg-[color-mix(in_oklch,var(--accent-soft)_70%,transparent)] px-4 py-3 text-sm text-[var(--text)]">
          <span className="whitespace-pre-wrap">{children}</span>
        </div>
      </div>
    );
  }
  return (
    <div className="flex justify-start">
      <div className="max-w-[65ch] rounded-[12px] bg-[var(--surface)] px-4 py-3 text-sm leading-relaxed text-[var(--text)]">
        {streaming && !children ? (
          <div className="flex flex-col gap-2 py-1">
            <div className="h-3 w-56 animate-pulse rounded bg-[var(--surface-2)]" />
            <div className="h-3 w-40 animate-pulse rounded bg-[var(--surface-2)]" />
          </div>
        ) : (
          <div className="md">
            <ReactMarkdown>{children || ""}</ReactMarkdown>
          </div>
        )}
      </div>
    </div>
  );
}

function TypingIndicator({ phase }) {
  const label = phase?.phase
    ? `Agent is working: ${String(phase.phase).toLowerCase().replaceAll("_", " ")}`
    : "Agent is thinking";
  return (
    <div className="flex items-center gap-2 text-[12px] text-[var(--text-2)]" aria-live="polite">
      <span className="flex gap-1" aria-hidden="true">
        {[0, 1, 2].map((dot) => (
          <span
            key={dot}
            className="h-1.5 w-1.5 animate-pulse rounded-full bg-[var(--accent)]"
            style={{ animationDelay: `${dot * 120}ms`, animationDuration: "900ms" }}
          />
        ))}
      </span>
      {label}
    </div>
  );
}

export default function ChatThread({ messages, trace, sandboxLogs, phaseState, typing, spec, results, report, runId, onRun, onStop, running, stopping }) {
  const endRef = useRef(null);

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, trace, sandboxLogs, results, report]);

  const empty = messages.length === 0 && !spec && !results;

  return (
    <div className="flex-1 overflow-y-auto">
      <div className="mx-auto flex w-full max-w-3xl flex-col gap-4 px-6 py-8">
        {empty && (
          <div className="mt-24 px-8 py-10 text-center">
            <h1 className="text-[22px] font-semibold tracking-tight text-[var(--text)]">
              Benchmark tools against your own data
            </h1>
            <p className="mx-auto mt-3 max-w-[65ch] text-sm text-[var(--text-2)]">
              Describe the tools to compare, attach a labelled dataset or use the
              synthetic demo set below, then send.
            </p>
          </div>
        )}

        {messages.map((m, i) => (
          <Bubble key={i} role={m.role} streaming={m.streaming}>
            {m.text}
          </Bubble>
        ))}

        {typing && <TypingIndicator phase={phaseState} />}

        {(trace.length > 0 || Object.keys(sandboxLogs).length > 0 || phaseState) && (
          <AgentTraceCard trace={trace} sandboxLogs={sandboxLogs} phaseState={phaseState} />
        )}

        {spec && <SpecCard spec={spec} onRun={onRun} onStop={onStop} running={running} stopping={stopping} />}

        {(results || report || running) && <ResultsCard metrics={results} report={report} runId={runId} />}

        <div ref={endRef} />
      </div>
    </div>
  );
}
