import { useEffect, useId, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import SpecCard from "./SpecCard.jsx";
import AgentActivity from "./AgentActivity.jsx";
import TracePanel from "./TracePanel.jsx";
import ResultsCard from "./ResultsCard.jsx";
import { MARKDOWN_HEADINGS_IN_THREAD, PANEL } from "./ui.jsx";
import { safeVisibleText } from "../displaySafety.js";
import { phaseLabel } from "../phaseLabel.js";
import StatusIcon from "./StatusIcon.jsx";
import { safeHttpUrl } from "../linkSafety.js";
import {
  canRenderMetrics,
  isHistoricalSynthetic,
  provenanceLabel,
  provenanceMatches,
} from "../provenance.js";

const TERMINAL_PHASES = ["DONE", "FAILED", "STOPPED"];

function SafeMarkdownLink({ href, children }) {
  const safeHref = safeHttpUrl(href);
  return safeHref ? <a href={safeHref} target="_blank" rel="noreferrer">{children}</a> : <span>{children}</span>;
}

function Bubble({ role, streaming, children }) {
  const isUser = role === "user";
  if (isUser) {
    return (
      <div className="flex justify-end">
        <div className="pb-contain max-w-[62ch] rounded-[12px] bg-[var(--surface-2)] px-3.5 py-2.5 text-[13px] text-[var(--ink)]">
          <span className="whitespace-pre-wrap">{safeVisibleText(children)}</span>
        </div>
      </div>
    );
  }
  return (
    <div className="flex justify-start">
      <div className="pb-msg-body pb-contain min-w-0 max-w-[68ch] text-[14px] leading-relaxed text-[var(--ink)]">
        {streaming && !children ? (
          <div className="flex flex-col gap-2 pt-1">
            <div className="pb-skeleton h-3 w-56" />
            <div className="pb-skeleton h-3 w-40" />
          </div>
        ) : (
          /* GFM, same as the generated report already used: the agent replies
             in tables often enough that without it a comparison arrived as a
             wall of pipe characters. Tables scroll inside the message rather
             than widening it, so a wide one cannot stretch the thread. */
          <div className="md overflow-x-auto">
            <ReactMarkdown
              remarkPlugins={[remarkGfm]}
              components={{ a: SafeMarkdownLink, ...MARKDOWN_HEADINGS_IN_THREAD }}
            >
              {safeVisibleText(children)}
            </ReactMarkdown>
          </div>
        )}
      </div>
    </div>
  );
}
function TypingIndicator({ phase }) {
  const label = phase?.phase
    ? `Agent is working: ${phaseLabel(safeVisibleText(phase.phase))}`
    : "Agent is thinking";
  return (
    <div className="flex justify-start" aria-live="polite">
      <div className="flex min-h-6 items-center gap-2 text-[12px] text-[var(--ink-2)]">
        <StatusIcon tone="running" size={13} pulse className="text-[var(--accent)]" />
        {label}
      </div>
    </div>
  );
}

/* A completed run is read in the order a decision is made, so anything that is
   supporting material starts folded away behind a labelled control. */
function Disclosure({ title, summary, defaultOpen = false, children }) {
  const [open, setOpen] = useState(defaultOpen);
  const id = useId();
  return (
    <section className="pb-glass rounded-[24px] p-4 shadow-[var(--shadow-card)]" aria-labelledby={`${id}-title`}>
      <button
        type="button"
        aria-expanded={open}
        // Only reference the body while it exists: the collapsed content is
        // unmounted rather than hidden, so a run's scraped detail is not in the
        // document at all until it is asked for.
        aria-controls={open ? `${id}-body` : undefined}
        onClick={() => setOpen((value) => !value)}
        className="flex min-h-10 w-full flex-wrap items-center justify-between gap-2 rounded-[8px] text-left"
      >
        <span className="flex items-center gap-2">
          <span className="select-none text-[var(--ink-3)]" aria-hidden="true">{open ? "▾" : "▸"}</span>
          <span id={`${id}-title`} className="text-[14px] font-semibold text-[var(--ink)]">{title}</span>
        </span>
        {summary && <span className="text-[12px] text-[var(--ink-2)]">{summary}</span>}
      </button>
      {open && <div id={`${id}-body`} className="mt-3">{children}</div>}
    </section>
  );
}

const EXAMPLE_PROMPTS = [
  "Compare Tesseract, PaddleOCR, and EasyOCR on my invoices",
  "Which document extraction API should we adopt, given this labelled set",
  "Rank open source OCR tools by exact match accuracy and latency",
];

export default function ChatThread({ statusMessage = "", statusFailed = false, messages, trace, sandboxLogs, phaseState, typing, spec, results, report, runId, onRun, onStop, running, stopping, mode, datasetId, provenance, specProvenance, resultsProvenance, executionMode, interactionDisabled = false, onPickPrompt, conversationLive = false }) {

  /* Holds the slice of trace the reader asked to see, so opening the log from a
     turn shows THAT turn's calls rather than every call of the session. */
  const [logTrace, setLogTrace] = useState(null);
  const empty = messages.length === 0 && !spec && !results;
  const latestTraceProvenance = [...trace].reverse().find((item) => item?.provenance)?.provenance ||
    Object.values(sandboxLogs || {}).flat().slice().reverse().find((line) => line?.provenance)?.provenance;
  const simulatedTrace = isHistoricalSynthetic(latestTraceProvenance || provenance);
  const provenanceMismatch = !provenanceMatches(specProvenance, resultsProvenance);
  const reportProvenanceMismatch = !provenanceMatches(resultsProvenance, report?.provenance);
  // Metrics need conclusive evidence. A pending or unverified run renders its
  // withheld-evidence notice instead of numbers, so nothing unproven is ever
  // presented as a measurement.
  const evidenceWithheld = Boolean(results) && !canRenderMetrics(resultsProvenance);
  const withheldLabel = provenanceLabel(resultsProvenance) || "Unverified. Results withheld";
  const phase = String(phaseState?.phase || "").toUpperCase();
  const terminal = TERMINAL_PHASES.includes(phase);
  const hasTrace = trace.length > 0 || Object.keys(sandboxLogs).length > 0 || Boolean(phaseState);
  // Decision first: once the run has settled and produced something to judge,
  // the ranking leads and the conversation becomes supporting material. A
  // restored run that never replayed a terminal phase event still counts as
  // settled when no phase is live and nothing is streaming.
  // Once the user carries on talking after a run, the conversation is live
  // again. Leaving it decision-first would fold their follow-up, and the reply
  // streaming into it, inside a collapsed disclosure.
  const settled = terminal || (!phaseState && !typing);
  const decisionFirst = settled && !running && !conversationLive && !typing &&
    Boolean(results || report);

  // Scroll only the thread's own container. scrollIntoView would also scroll
  // every ancestor scroll container (including the page-level <main>), which
  // dragged the whole console up during streaming and hid the header and rail.
  const scrollRef = useRef(null);
  useEffect(() => {
    const el = scrollRef.current;
    if (!el) return;
    const target = decisionFirst ? 0 : el.scrollHeight;
    if (typeof el.scrollTo === "function") {
      el.scrollTo({ top: target, behavior: decisionFirst ? "auto" : "smooth" });
    } else {
      // jsdom has no Element.scrollTo; scrollTop assignment is the same motion.
      el.scrollTop = target;
    }
  }, [messages, trace, sandboxLogs, results, report, decisionFirst]);

  const alerts = (
    <>
      {provenanceMismatch && (
        <div className="rounded-[8px] border border-[var(--danger)] bg-[var(--danger-tint)] px-3 py-2 text-[13px] text-[var(--danger)]" role="alert">
          Results provenance does not match the benchmark specification. Treat this run as unavailable and rerun it.
        </div>
      )}
      {report && reportProvenanceMismatch && (
        <div className="rounded-[8px] border border-[var(--danger)] bg-[var(--danger-tint)] px-3 py-2 text-[13px] text-[var(--danger)]" role="alert">
          Report provenance does not match the result metrics. The report has been blocked.
        </div>
      )}
      {evidenceWithheld && (
        <div className="rounded-[8px] border border-[var(--warn)] bg-[var(--warn-tint)] px-3 py-2 text-[13px] text-[var(--ink)]" role="status">
          {withheldLabel}. Metrics stay hidden until this run has immutable
          verified evidence.
        </div>
      )}
    </>
  );

  const resultsCard = (results || report || running || terminal) ? (
    <ResultsCard
      metrics={provenanceMismatch || evidenceWithheld ? null : results}
      report={provenanceMismatch || reportProvenanceMismatch || evidenceWithheld ? null : report}
      runId={runId}
      simulated={isHistoricalSynthetic(resultsProvenance)}
      phase={phase}
      running={running}
      executionMode={executionMode}
    />
  ) : null;

  const specCard = spec ? (
    <SpecCard
      spec={spec}
      datasetId={datasetId}
      onRun={onRun}
      onStop={onStop}
      running={running}
      stopping={stopping}
      interactionDisabled={interactionDisabled}
    />
  ) : null;

  /* The agent's work belongs to the turn that produced it, so trace is grouped
     by its stamped turn and rendered inline just before that turn's reply —
     the way a chat shows "Searched the web" above the answer it informed.
     Anything stamped past the last message is the turn in flight and renders at
     the end of the thread. Trace with no stamp at all (an older session) also
     falls to the end rather than being attributed to a guess. */
  const traceByTurn = new Map();
  for (const item of trace) {
    const turn = Number.isInteger(item?.turn) ? item.turn : messages.length;
    if (!traceByTurn.has(turn)) traceByTurn.set(turn, []);
    traceByTurn.get(turn).push(item);
  }
  const trailingTrace = [...traceByTurn.entries()]
    .filter(([turn]) => turn >= messages.length)
    .flatMap(([, items]) => items);

  const turnActivity = (turn, isLive) => {
    const items = traceByTurn.get(turn);
    if (!items || items.length === 0) return null;
    return (
      <AgentActivity
        key={`activity-${turn}`}
        trace={items}
        sandboxLogs={sandboxLogs}
        phaseState={phaseState}
        simulated={simulatedTrace}
        live={isLive}
        onOpenLog={() => setLogTrace(items)}
      />
    );
  };

  /* "Working" is the agent thinking OR a benchmark executing. Keying liveness
     to `running` alone collapsed the list to its one-line summary while the
     agent was still mid-search, because `running` only covers an executing
     benchmark — not the chat turn that precedes it. */
  const working = running || typing;

  const conversation = messages.flatMap((m, i) => {
    /* The reply beginning is what ends the work, not the turn finishing. Keying
       liveness to `working` alone left the full search log expanded above the
       answer for as long as it streamed, so the reader had to scroll past a
       finished list of URLs to reach the thing they asked for. */
    const answered = m.role === "assistant" && Boolean(m.text);
    const activity = turnActivity(i, working && !answered && i === messages.length - 1);
    const bubble = (
      <Bubble key={i} role={m.role} streaming={m.streaming}>
        {m.text}
      </Bubble>
    );
    return activity ? [activity, bubble] : [bubble];
  });

  return (
    <div ref={scrollRef} className="flex-1 overflow-y-auto">
      {/* pt clears the fixed top strip (3.25rem) plus a little air, so the first
          message starts below it rather than under it. */}
      <div className="mx-auto w-full max-w-canvas px-4 pb-6 pt-[4.25rem] sm:px-8 sm:pb-8">

        {/* Connection state sits with the conversation, not in the top strip:
            it is transient prose about THIS thread, and the strip is reserved
            for the title and the controls that must never scroll away.
            Never gated on the thread having messages — a stream can connect,
            fail or complete before the first reply arrives, and that is exactly
            when the reader most needs to be told. */}
        {statusMessage && (
          <p
            className={`mx-auto mb-3 w-full max-w-[var(--thread-w)] text-[12px] ${
              statusFailed ? "text-[var(--danger)]" : "text-[var(--ink-3)]"
            }`}
            role="status"
          >
            {statusMessage}
          </p>
        )}

        {empty && (
          <div className="mx-auto flex min-h-[max(320px,calc(100dvh-330px))] max-w-[720px] flex-col justify-center text-center">
            <h2 className="pb-display mx-auto max-w-[26ch] text-[34px] leading-[1.15] text-[var(--ink)] sm:text-[40px]">
              What should we prove today?
            </h2>
            <p className="mx-auto mt-3 max-w-[52ch] text-[14px] leading-relaxed text-[var(--ink-2)]">
              {datasetId
                ? "Name the tools to compare. Your labelled dataset is attached and ready to score against."
                : "Name the tools to compare and attach a labelled dataset, or start from the sample labelled dataset."}
            </p>
            <div className="pb-glass mx-auto mt-6 w-full max-w-[560px] divide-y divide-[var(--line)] overflow-hidden rounded-[24px] text-left shadow-[var(--shadow-card)]">
              {EXAMPLE_PROMPTS.map((example, index) => (
                <button
                  key={example}
                  type="button"
                  onClick={() => onPickPrompt?.(example)}
                  className="group flex min-h-12 w-full items-center gap-3 px-3.5 py-2.5 text-left text-[13px] text-[var(--ink)] transition-colors duration-150 hover:bg-[var(--surface-2)]"
                >
                  <span
                    aria-hidden="true"
                    className={`flex h-7 w-7 shrink-0 items-center justify-center rounded-[9px] ${
                      index === 0
                        ? "bg-[var(--ok-tint)] text-[var(--ok)]"
                        : index === 1
                          ? "bg-[var(--stone-tint)] text-[var(--stone)]"
                          : "bg-[var(--danger-tint)] text-[var(--danger)]"
                    }`}
                  >
                    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
                      {index === 0 ? (
                        <>
                          <path d="m9 8-5 4 5 4" />
                          <path d="m15 8 5 4-5 4" />
                        </>
                      ) : index === 1 ? (
                        <>
                          <path d="M6 3h9l4 4v14H6z" />
                          <path d="M14 3v5h5" />
                        </>
                      ) : (
                        <>
                          <path d="M5 20V10" />
                          <path d="M12 20V4" />
                          <path d="M19 20v-7" />
                        </>
                      )}
                    </svg>
                  </span>
                  <span className="min-w-0 flex-1">{example}</span>
                  <span
                    aria-hidden="true"
                    className="shrink-0 text-[var(--ink-3)] transition-colors duration-150 group-hover:text-[var(--accent)]"
                  >
                    →
                  </span>
                </button>
              ))}
            </div>
            <p className="mt-4 text-[12px] text-[var(--ink-3)]">
              Nothing runs until you confirm the proposed specification.
            </p>
          </div>
        )}

        {/* One centered reading column. During a run the conversation leads
            and the trace follows it in flow; once the run settles the verdict
            leads and everything else folds away below it. */}
        {decisionFirst ? (
          <div className="mx-auto flex w-full max-w-[var(--thread-w)] min-w-0 flex-col gap-4">
            {alerts}
            {resultsCard}
            {specCard}
            {messages.length > 0 && (
              <Disclosure
                title="Conversation"
                summary={`${messages.length} ${messages.length === 1 ? "message" : "messages"}`}
              >
                <div className="flex flex-col gap-5">{conversation}</div>
              </Disclosure>
            )}
            {trailingTrace.length > 0 && (
              <AgentActivity
                trace={trailingTrace}
                sandboxLogs={sandboxLogs}
                phaseState={phaseState}
                simulated={simulatedTrace}
                onOpenLog={() => setLogTrace(trailingTrace)}
              />
            )}
          </div>
        ) : (
          <div className="mx-auto flex w-full max-w-[var(--thread-w)] min-w-0 flex-col gap-4">
            {conversation.length > 0 && (
              <div className="flex flex-col gap-5">{conversation}</div>
            )}
            {typing && <TypingIndicator phase={phaseState} />}
            {alerts}
            {specCard}
            {/* Only the turn still in flight lands here; work belonging to an
                earlier turn is rendered beside that turn's reply above. */}
            {trailingTrace.length > 0 && (
              <AgentActivity
                trace={trailingTrace}
                sandboxLogs={sandboxLogs}
                phaseState={phaseState}
                simulated={simulatedTrace}
                /* Bare and streaming while the agent works, so its progress is
                   the visible thing; a quiet one-line summary once it stops,
                   because then the ANSWER is the visible thing and the log is
                   evidence available on request. */
                live={working}
                onOpenLog={() => setLogTrace(trailingTrace)}
              />
            )}
            {resultsCard}
          </div>
        )}

      </div>

      <TracePanel
        open={logTrace !== null}
        onClose={() => setLogTrace(null)}
        trace={logTrace || []}
        sandboxLogs={sandboxLogs}
        phaseState={phaseState}
        simulated={simulatedTrace}
      />
    </div>
  );
}
