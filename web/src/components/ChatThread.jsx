import { useEffect, useId, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import SpecCard from "./SpecCard.jsx";
import AgentActivity from "./AgentActivity.jsx";
import TracePanel from "./TracePanel.jsx";
import ResultsCard from "./ResultsCard.jsx";
import { MARKDOWN_HEADINGS_IN_THREAD } from "./ui.jsx";
import { safeVisibleText } from "../displaySafety.js";
import { phaseLabel } from "../phaseLabel.js";
import StatusIcon from "./StatusIcon.jsx";
import { safeHttpUrl } from "../linkSafety.js";
import SafeMarkdownLink from "./SafeMarkdownLink.jsx";
import {
  canRenderMetrics,
  isHistoricalSynthetic,
  provenanceLabel,
  provenanceMatches,
} from "../provenance.js";

const TERMINAL_PHASES = ["DONE", "FAILED", "STOPPED"];
/* The phases a confirmed run actually moves through. A results placeholder is
   honest only inside one of them: before that the session is still deciding
   WHAT to run, and the next card to appear is the spec, not a score.

   This is an allowlist because the denylist it replaces ("not INTAKE, not
   SPEC_CONFIRM") let every other pre-run state through — including the empty
   phase a session carries while it is still searching, which is exactly when
   the placeholder was seen promising results nobody had asked for yet. */
const RUN_PHASES = [
  "DOCS_INTEL", "ADAPTER_GEN", "PROVISIONING", "BUILDING",
  "VALIDATING", "RUNNING", "EVALUATING", "REPORTING",
];

/* The orchestrator proposes a specification as a fenced JSON block. Printed raw
   it is a wall of braces the reader has to parse by eye, so the shape it always
   has — type, category, fields, candidates — is rendered as a summary instead.
   Anything that does not match that shape falls through to a normal code block,
   because guessing at unknown JSON would hide content rather than clarify it. */
function parseSpecBlock(text) {
  try {
    const value = JSON.parse(String(text));
    if (!value || typeof value !== "object" || Array.isArray(value)) return null;
    if (!Array.isArray(value.candidates)) return null;
    return value;
  } catch {
    return null;
  }
}

const CANDIDATE_KIND = {
  hosted_api: "Hosted API",
  saas: "SaaS",
  local_tool: "Local tool",
  library: "Library",
};

function SpecPreview({ spec }) {
  const facts = [
    ["Type", spec.benchmark_type],
    ["Category", spec.category],
  ].filter(([, value]) => value);
  const fields = Array.isArray(spec.fields) ? spec.fields : [];
  const candidates = spec.candidates.filter((item) => item && typeof item === "object");

  return (
    <div className="my-2 overflow-hidden rounded-[14px] border border-[var(--line)]">
      <div className="border-b border-[var(--line)] bg-[var(--surface-2)] px-3.5 py-2">
        <p className="text-[12px] font-semibold text-[var(--ink)]">Proposed benchmark</p>
      </div>
      <div className="px-3.5 py-3">
        {facts.length > 0 && (
          <dl className="flex flex-wrap gap-x-6 gap-y-1">
            {facts.map(([label, value]) => (
              <div key={label} className="flex items-baseline gap-1.5">
                <dt className="text-[11px] uppercase tracking-wide text-[var(--ink-3)]">{label}</dt>
                <dd className="text-[13px] text-[var(--ink)]">{safeVisibleText(value)}</dd>
              </div>
            ))}
          </dl>
        )}
        {fields.length > 0 && (
          <div className="mt-2.5">
            <p className="text-[11px] uppercase tracking-wide text-[var(--ink-3)]">
              Fields extracted
            </p>
            <div className="mt-1 flex flex-wrap gap-1">
              {fields.map((field) => (
                <span
                  key={String(field)}
                  className="pb-mono rounded-full bg-[var(--surface-2)] px-2 py-0.5 text-[11px] text-[var(--ink-2)]"
                >
                  {safeVisibleText(field)}
                </span>
              ))}
            </div>
          </div>
        )}
        {candidates.length > 0 && (
          <div className="mt-3">
            <p className="text-[11px] uppercase tracking-wide text-[var(--ink-3)]">
              {candidates.length} candidate{candidates.length === 1 ? "" : "s"}
            </p>
            <ul className="mt-1 divide-y divide-[var(--line)]">
              {candidates.map((item, index) => {
                const docs = safeHttpUrl(item.docs_url);
                return (
                  <li
                    key={`${item.name || index}`}
                    className="flex flex-wrap items-baseline gap-x-2 gap-y-0.5 py-1.5"
                  >
                    <span className="text-[13px] font-medium text-[var(--ink)]">
                      {safeVisibleText(item.name) || "unnamed"}
                    </span>
                    {item.kind && (
                      <span className="text-[11px] text-[var(--ink-3)]">
                        {CANDIDATE_KIND[item.kind] || safeVisibleText(item.kind)}
                      </span>
                    )}
                    {/* Named, because "build_component" means this entry is a
                        part of the harness rather than a tool being judged. */}
                    {item.role === "build_component" && (
                      <span className="rounded-full bg-[var(--surface-2)] px-1.5 py-0.5 text-[10px] font-medium text-[var(--ink-2)]">
                        build component
                      </span>
                    )}
                    {docs && (
                      <a
                        href={docs}
                        target="_blank"
                        rel="noreferrer noopener"
                        className="ml-auto text-[11px] text-[var(--accent)] underline underline-offset-2"
                      >
                        docs
                      </a>
                    )}
                  </li>
                );
              })}
            </ul>
          </div>
        )}
      </div>
    </div>
  );
}

function MarkdownCode({ className, children, ...rest }) {
  const text = Array.isArray(children) ? children.join("") : String(children ?? "");
  // Only a fenced block carries a language class; inline code must stay inline.
  if (/language-json/.test(String(className || ""))) {
    const spec = parseSpecBlock(text);
    if (spec) return <SpecPreview spec={spec} />;
  }
  return <code className={className} {...rest}>{children}</code>;
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
              components={{ a: SafeMarkdownLink, code: MarkdownCode, ...MARKDOWN_HEADINGS_IN_THREAD }}
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
    <section
      className="border-y border-[var(--line)]"
      aria-labelledby={`${id}-title`}
      data-post-run-conversation
    >
      <button
        type="button"
        aria-expanded={open}
        // Only reference the body while it exists: the collapsed content is
        // unmounted rather than hidden, so a run's scraped detail is not in the
        // document at all until it is asked for.
        aria-controls={open ? `${id}-body` : undefined}
        onClick={() => setOpen((value) => !value)}
        className="group flex min-h-14 w-full items-center justify-between gap-3 rounded-[8px] px-1 text-left transition-colors duration-150 hover:text-[var(--accent)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)] focus-visible:ring-offset-2 focus-visible:ring-offset-[var(--surface)]"
      >
        <span id={`${id}-title`} className="text-[14px] font-medium text-[var(--ink)] group-hover:text-[var(--accent)]">
          {title}
        </span>
        <span className="flex shrink-0 items-center gap-2.5">
          {summary && <span className="text-[12px] text-[var(--ink-3)]">{summary}</span>}
          <svg
            aria-hidden="true"
            width="16"
            height="16"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="1.75"
            strokeLinecap="round"
            strokeLinejoin="round"
            className={`text-[var(--ink-3)] transition-transform duration-200 ease-out ${
              open ? "rotate-180" : ""
            }`}
          >
            <path d="m6 9 6 6 6-6" />
          </svg>
        </span>
      </button>
      {open && (
        <div id={`${id}-body`} className="border-t border-[var(--line)] pb-2 pt-5">
          {children}
        </div>
      )}
    </section>
  );
}

/* Three prompts spanning what ProofBench actually does, because the examples
   teach the product: one measured run over documents, one measured run over a
   task that has nothing to do with documents, and one comparison that needs no
   data at all. All three OCR before, which taught every new user that this was
   an OCR tool and that a benchmark needs a dataset. */
const EXAMPLE_PROMPTS = [
  "Compare Tesseract, PaddleOCR, and EasyOCR on my invoices",
  "Which speech-to-text API transcribes support calls most accurately",
  "Which error tracking tools fit a self-hosted Django stack, and what do they cost",
];

export default function ChatThread({ statusMessage = "", statusFailed = false, messages, trace, sandboxLogs, phaseState, typing, spec, results, report, runId, onRun, onStop, running, stopping, mode, datasetId, provenance, specProvenance, resultsProvenance, executionMode, interactionDisabled = false, onPickPrompt, conversationLive = false, completedFooter = false }) {

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

  // Real output always renders. A loading placeholder only once the run is past
  // the point where the spec is what arrives next.
  const inRun = RUN_PHASES.includes(phase);
  const resultsCard = (results || report || terminal || (running && inRun)) ? (
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
      hasRun={Boolean(results || runId)}
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
      <div
        className={`mx-auto w-full max-w-canvas px-4 pt-[4.25rem] sm:px-8 ${
          completedFooter ? "pb-36 sm:pb-24" : "pb-6 sm:pb-8"
        }`}
        data-completed-footer-clearance={completedFooter || undefined}
      >

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
              {/* Never ask for a dataset here. Some questions are settled by
                  comparing what the tools are, and the ones that need measuring
                  build their own examples — so this describes what to say, not
                  what to bring. */}
              {datasetId
                ? "Name the tools to compare. Your labelled data is attached and ready to score against."
                : "Name the tools to compare and what you need to know. Bring your own labelled data if you have it."}
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
