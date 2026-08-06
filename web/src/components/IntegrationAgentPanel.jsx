import { useEffect, useRef, useState } from "react";
import { getIntegrationAgentStatus, streamIntegrationAgentMessage } from "../api.js";
import { safeVisibleText } from "../displaySafety.js";
import { safeHttpUrl } from "../linkSafety.js";
import { PANEL, Skeleton } from "./ui.jsx";
import sparkleMark from "../assets/sparkle.png";
import proposalMark from "../assets/proposal.png";

/* Starters, not instructions: a blank textarea is the weakest possible
   invitation (recognition over recall), so the empty state hands over two
   real questions the agent can act on immediately. */
const PROMPTS = [
  "Add support for Mistral",
  "Check if Groq is supported",
];

function SendIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M12 19V5" />
      <path d="m5 12 7-7 7 7" />
    </svg>
  );
}

function ClearIcon() {
  return (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M18 6 6 18" />
      <path d="m6 6 12 12" />
    </svg>
  );
}

/* Same warm amber/coral/orchid trio the marketing pages use for their
   gradient eyebrows (see new-site-design-direction's glow-card system) — the
   one place in this panel that borrows that grammar, kept to a hairline ring
   and a two-tone icon rather than a filled gradient surface. */
const COMPOSER_GRADIENT = "linear-gradient(90deg, #F5C344, #F28482, #B567C2)";

function SparkleIcon({ size = 20 }) {
  return (
    <img
      src={sparkleMark}
      alt=""
      aria-hidden="true"
      width={size}
      height={size}
      className="shrink-0 select-none"
    />
  );
}

/* A validation outcome is the one part of a reply an operator acts on, so the
   word carries the tone. Anything the backend sends that is neither a pass nor
   a failure stays neutral. */
function implementationTone(status) {
  const word = String(status || "").toLowerCase();
  if (/fail|error|reject|invalid|unsupported/.test(word)) return "text-[var(--danger)]";
  if (/valid|pass|ready|complete|success/.test(word)) return "text-[var(--ok)]";
  return "text-[var(--ink)]";
}

/* A page is one row that changes state, not one row per event: "Reading X"
   followed by "Read X" said the same thing twice and buried the list. The row
   is keyed by URL, so the finishing event updates the row the starting event
   created. Rows for pages nest under the "Found n sources" line they belong to.

   Each row is labelled by host, because "GroqDocs" does not tell an operator
   which site was actually read, and the full URL is too long to scan. */
/* Host plus path, because two pages on one documentation site are common and
   the bare host would print the same row twice. The row truncates from the
   right and carries the full title as its tooltip. */
function hostOf(url) {
  try {
    const parsed = new URL(String(url || ""));
    const path = parsed.pathname.replace(/\/$/, "");
    return parsed.host.replace(/^www\./, "") + path;
  } catch {
    return "";
  }
}

function applyProgress(steps, event) {
  const phase = String(event?.phase || "");
  const url = safeHttpUrl(event?.url) || "";
  const host = hostOf(url);
  const title = safeVisibleText(event?.title) || host;

  if (phase === "search") {
    return [...steps, { key: "search", kind: "step", label: "Searching official documentation" }];
  }
  if (phase === "found") {
    const n = Number(event?.count) || 0;
    return [
      ...steps,
      { key: "found", kind: "step", label: `Found ${n} ${n === 1 ? "source" : "sources"}` },
    ];
  }
  if (phase === "compose") {
    return [
      ...steps,
      {
        key: "compose",
        kind: "step",
        label: `Drafting the proposal with ${safeVisibleText(event?.provider)}`,
      },
    ];
  }
  if (phase === "read") {
    if (!host) return steps;
    return [...steps, { key: url, kind: "source", host, title, state: "reading" }];
  }

  const settled = { read_done: "done", read_failed: "failed", read_empty: "empty" }[phase];
  if (!settled) return steps;
  const index = steps.findIndex((step) => step.key === url);
  if (index === -1) return steps;
  const next = [...steps];
  next[index] = { ...next[index], state: settled };
  return next;
}

/* What a settled source row says about itself, after its host. */
const SOURCE_NOTE = {
  reading: "reading",
  done: "",
  failed: "unreachable",
  empty: "nothing usable",
};

/* The backend sends machine words ("proposal", "needs_input"). They are shown
   to a person, so they are spelled as a person would read them. Anything the
   backend adds later still renders, capitalized, rather than falling through. */
function statusLabel(status) {
  const word = safeVisibleText(status) || "reported";
  const spaced = word.replace(/[_-]+/g, " ").trim();
  return spaced.charAt(0).toUpperCase() + spaced.slice(1);
}

function Sources({ sources }) {
  const rows = (sources || [])
    .map((source) => ({ title: safeVisibleText(source?.title), url: safeHttpUrl(source?.url) }))
    .filter((source) => source.url);
  if (rows.length === 0) return null;
  return (
    <div className="mt-2.5">
      <p className="pb-eyebrow">Sources</p>
      {/* A tight, underlined list: these are links first and prose second, so
          they are set smaller than the label and stacked close together. */}
      <ul className="mt-1 space-y-0.5">
        {rows.map((source) => (
          <li key={source.url} className="min-w-0">
            <a
              href={source.url}
              target="_blank"
              rel="noreferrer noopener"
              className="block truncate text-[10.5px] leading-[1.45] text-[var(--accent)] underline underline-offset-2 hover:text-[var(--accent-hover)] focus-visible:outline-none focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[var(--accent)]"
              title={source.title || source.url}
            >
              {source.title || source.url}
            </a>
          </li>
        ))}
      </ul>
    </div>
  );
}

/* The agent answers in markdown and its proposals carry fenced code, so the
   fences are rendered as code rather than printed as literal backticks. Only
   fenced blocks are handled: that is the one construct these replies actually
   use, and a full markdown renderer would be a dependency for nothing. An
   unterminated fence keeps its remaining text rather than swallowing it. */
function splitFences(text) {
  const parts = [];
  const pattern = /```([\w+-]*)\n?([\s\S]*?)(?:```|$)/g;
  let cursor = 0;
  let match;
  while ((match = pattern.exec(text)) !== null) {
    if (match.index > cursor) {
      parts.push({ type: "text", content: text.slice(cursor, match.index) });
    }
    parts.push({ type: "code", lang: match[1] || "", content: match[2].replace(/\n$/, "") });
    cursor = pattern.lastIndex;
  }
  if (cursor < text.length) parts.push({ type: "text", content: text.slice(cursor) });
  return parts.filter((part) => part.type === "code" || part.content.trim());
}

function AgentTurn({ turn }) {
  return (
    <div className="min-w-0">
      {turn.text &&
        splitFences(turn.text).map((part, index) =>
          part.type === "code" ? (
            /* Code sets its own width, so it scrolls inside its own box rather
               than widening the thread. */
            <div
              key={index}
              className="mt-2 overflow-hidden rounded-[10px] bg-[var(--code-bg)]"
            >
              {part.lang && (
                <div className="pb-mono border-b border-[var(--line)] px-3 py-1 text-[10px] uppercase tracking-wide text-[var(--ink-3)]">
                  {part.lang}
                </div>
              )}
              <pre className="pb-mono overflow-x-auto px-3 py-2.5 text-[11.5px] leading-relaxed text-[var(--code-text)]">
                {part.content}
              </pre>
            </div>
          ) : (
            <p
              key={index}
              className="pb-contain mt-2 whitespace-pre-wrap text-[13px] leading-relaxed text-[var(--ink)] first:mt-0"
            >
              {part.content.trim()}
            </p>
          ),
        )}
      {turn.implementation && (
        <div className="mt-2.5 rounded-[12px] bg-[var(--surface-2)] px-3 py-2.5">
          {/* No pill: its left padding inset the icon from the summary beneath
              it, so the block read as misaligned. Flush row, tone on the word. */}
          <span className="flex items-center gap-1.5 text-[11px] font-medium">
            <img
              src={proposalMark}
              alt=""
              aria-hidden="true"
              width="15"
              height="15"
              className="shrink-0"
            />
            <span className={implementationTone(turn.implementation.status)}>
              {statusLabel(turn.implementation.status)}
            </span>
          </span>
          {turn.implementation.summary && (
            <p className="pb-contain mt-1.5 text-[12px] leading-relaxed text-[var(--ink-2)]">
              {safeVisibleText(turn.implementation.summary)}
            </p>
          )}
        </div>
      )}
      <Sources sources={turn.sources} />
    </div>
  );
}

/* An operational console for adding a provider, not a general chatbot. It reads
   the same readiness the rest of Settings reads, but through its own endpoint:
   this agent needs a default LLM and a scraping provider specifically, which is
   a narrower question than "can this deployment run a benchmark". Credentials
   are never edited here; the Services card above owns that. */
export default function IntegrationAgentPanel({ className = "", refreshKey = 0 }) {
  const [status, setStatus] = useState(null);
  const [statusFailed, setStatusFailed] = useState(false);
  const [turns, setTurns] = useState([]);
  const [text, setText] = useState("");
  const [sending, setSending] = useState(false);
  const [composerFocused, setComposerFocused] = useState(false);
  const [steps, setSteps] = useState([]);
  const [error, setError] = useState("");
  const inputRef = useRef(null);
  const logRef = useRef(null);
  const nextId = useRef(0);

  useEffect(() => {
    let alive = true;
    getIntegrationAgentStatus()
      .then((data) => { if (alive) setStatus(data); })
      .catch(() => { if (alive) setStatusFailed(true); });
    return () => { alive = false; };
  }, [refreshKey]);

  /* The newest turn is the one worth reading, and a reply can be several lines
     long, so the log follows it down rather than leaving the reader at the top. */
  useEffect(() => {
    const node = logRef.current;
    if (node) node.scrollTop = node.scrollHeight;
  }, [turns, sending, steps]);

  const ready = status?.ready === true;
  const blocked = status != null && !ready;

  const send = async () => {
    const question = text.trim();
    if (!question || sending || !ready) return;
    const id = (nextId.current += 1);
    setText("");
    setError("");
    setTurns((current) => [...current, { id: `you-${id}`, role: "you", text: question }]);
    setSending(true);
    setSteps([]);
    try {
      const history = turns
        .filter((turn) => turn.text)
        .map((turn) => ({
          role: turn.role === "you" ? "user" : "assistant",
          content: turn.text,
        }));
      const reply = await streamIntegrationAgentMessage(question, history, (event) => {
        setSteps((current) => applyProgress(current, event));
      });
      setTurns((current) => [
        ...current,
        {
          id: `agent-${id}`,
          role: "agent",
          text: safeVisibleText(reply?.message),
          sources: Array.isArray(reply?.sources) ? reply.sources : [],
          implementation: reply?.implementation || null,
        },
      ]);
    } catch (failure) {
      setError(failure.message || "The integration agent could not answer that. Try again.");
    } finally {
      setSending(false);
      /* The steps described the wait, so they retire with it: the answer and
         its sources are the record worth keeping in the transcript. */
      setSteps([]);
      inputRef.current?.focus();
    }
  };

  const onKeyDown = (event) => {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      send();
    }
  };

  const requirements = [
    { label: "Default LLM", state: status?.llm },
    { label: "Web scraping API", state: status?.scraper },
  ];

  return (
    <div className={`relative flex flex-col ${className}`}>
      {/* The same amber/coral/orchid trio as the composer ring, diffused into an
          ambient glow behind the card rather than a hard-edged background — it
          reads as light the card is sitting in, not a colored panel. */}
      <div
        aria-hidden="true"
        className="pointer-events-none absolute -inset-5 opacity-90 blur-2xl"
        style={{
          background:
            "radial-gradient(48% 42% at 18% 12%, #F5C344 0%, transparent 72%), radial-gradient(48% 42% at 85% 28%, #F28482 0%, transparent 72%), radial-gradient(52% 46% at 52% 96%, #B567C2 0%, transparent 72%)",
        }}
      />
      <section
        aria-labelledby="integration-agent-heading"
        className={`${PANEL} relative flex min-h-0 flex-1 flex-col overflow-hidden`}
      >
      {/* A chat surface reads as one the moment it gets a fixed header and a
          scrolling body under it (Jakob's Law: every messaging app does this).
          The chip earns its place by answering the question the header used to
          leave open: can I actually use this right now. */}
      <header className="shrink-0 px-5 pb-3.5 pt-4">
        <h2
          id="integration-agent-heading"
          className="text-[16px] font-semibold tracking-[-0.01em] text-[var(--ink)]"
        >
          Integration agent
        </h2>
        <p className="mt-1 max-w-[52ch] text-[12px] leading-relaxed text-[var(--ink-2)]">
          Point the agent at any LLM provider and get back a working connector proposal, sourced
          from its own documentation.
        </p>
        {/* A gradient hairline rather than a flat border: it separates the
            header from the thread without drawing a hard box around it. */}
        <div
          aria-hidden="true"
          className="mt-3.5 h-px w-full"
          style={{
            background:
              "linear-gradient(90deg, transparent, color-mix(in oklab, #F28482 34%, transparent) 22%, color-mix(in oklab, #B567C2 30%, transparent) 68%, transparent)",
          }}
        />
      </header>

      <div
        ref={logRef}
        role="log"
        aria-live="polite"
        aria-label="Integration agent conversation"
        className="flex min-h-0 flex-1 flex-col overflow-y-auto px-5 py-4"
      >
        {!status && !statusFailed && (
          <div className="flex flex-col gap-3 pt-2">
            <Skeleton className="h-3.5 w-40" />
            <Skeleton className="h-3.5 w-full" />
            <Skeleton className="h-3.5 w-3/4" />
          </div>
        )}

        {statusFailed && (
          <p className="text-[13px] text-[var(--ink-2)]" role="status">
            The integration agent is unavailable right now.
          </p>
        )}

        {status && turns.length === 0 && (
          <div className="flex min-h-full flex-col items-center justify-center gap-4 py-6 text-center">
            <SparkleIcon size={52} />
            <div>
              <p className="pb-contain text-[15px] font-medium text-[var(--ink)]">
                {blocked
                  ? "The integration agent needs setup first"
                  : "Ask about adding an LLM provider"}
              </p>
              <p className="pb-contain mt-1 max-w-[38ch] text-[13px] leading-relaxed text-[var(--ink-2)]">
                {blocked
                  ? "One default LLM and one web scraping API must be configured first."
                  : "It reads the provider's own API documentation, proposes a connector, and validates it. Activating one stays your call."}
              </p>
            </div>

            {blocked ? (
              <dl className="mt-1 w-full max-w-[320px] divide-y divide-[var(--line)] overflow-hidden rounded-[12px] bg-[var(--surface-2)] text-left">
                {requirements.map((requirement) => (
                  <div key={requirement.label} className="flex items-center gap-x-2.5 px-3 py-2">
                    <dt className="min-w-0 flex-1 truncate text-[12px] text-[var(--ink)]">
                      {requirement.label}
                    </dt>
                    <dd
                      className={`shrink-0 text-[12px] font-medium ${
                        requirement.state?.configured
                          ? "text-[var(--ink-3)]"
                          : "text-[var(--danger)]"
                      }`}
                    >
                      {requirement.state?.configured
                        ? safeVisibleText(requirement.state.provider) || "configured"
                        : "not configured"}
                    </dd>
                  </div>
                ))}
              </dl>
            ) : (
              <div className="flex flex-wrap justify-center gap-2">
                {PROMPTS.map((prompt) => (
                  <button
                    key={prompt}
                    type="button"
                    onClick={() => {
                      setText(prompt);
                      inputRef.current?.focus();
                    }}
                    className="rounded-full border border-[var(--line)] bg-[var(--surface)] px-3.5 py-2 text-[12px] font-medium text-[var(--ink-2)] transition-colors duration-150 hover:border-[var(--accent)] hover:text-[var(--ink)] focus-visible:outline-none focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[var(--accent)]"
                  >
                    {prompt}
                  </button>
                ))}
              </div>
            )}
          </div>
        )}

        {turns.length > 0 && (
          <ol className="space-y-4">
            {turns.map((turn) => (
              <li key={turn.id} className={`min-w-0 flex ${turn.role === "you" ? "justify-end" : "justify-start"}`}>
                {turn.role === "you" ? (
                  <p className="pb-contain max-w-[85%] whitespace-pre-wrap rounded-[16px] rounded-br-[4px] bg-[var(--accent-tint)] px-3.5 py-2 text-[13px] leading-relaxed text-[var(--ink)]">
                    {turn.text}
                  </p>
                ) : (
                  <div className="max-w-[92%]">
                    <AgentTurn turn={turn} />
                  </div>
                )}
              </li>
            ))}
          </ol>
        )}

        {/* The running log of what the agent is reading. It exists only for the
            duration of the wait and is cleared once the turn lands, so it never
            competes with the answer it was covering for. */}
        {sending && (
          <div className="mt-4">
            <ol className="space-y-1.5">
              {steps.map((step, index) => {
                const current = index === steps.length - 1;
                if (step.kind === "source") {
                  const note = SOURCE_NOTE[step.state];
                  return (
                    /* Indented, so the pages read sit visibly beneath the line
                       that announced them rather than as peers of it. */
                    <li
                      key={step.key}
                      className="flex items-start gap-2 pl-4 text-[12px] leading-relaxed"
                      title={step.title}
                    >
                      <span
                        aria-hidden="true"
                        className={`mt-[6px] h-1 w-1 shrink-0 rounded-full ${
                          step.state === "reading"
                            ? "bg-[var(--accent)]"
                            : step.state === "done"
                              ? "bg-[var(--ok)]"
                              : "bg-[var(--ink-3)]"
                        }`}
                      />
                      <span className="pb-contain min-w-0 flex-1 truncate text-[var(--ink-2)]">
                        {step.host}
                        {note && <span className="text-[var(--ink-3)]"> {note}</span>}
                      </span>
                    </li>
                  );
                }
                return (
                  <li
                    key={step.key}
                    className={`flex items-start gap-2 text-[12px] leading-relaxed ${
                      current ? "text-[var(--ink-2)]" : "text-[var(--ink-3)]"
                    }`}
                  >
                    <span
                      aria-hidden="true"
                      className={`mt-[6px] h-1 w-1 shrink-0 rounded-full ${
                        current ? "bg-[var(--accent)]" : "bg-[var(--ink-3)]"
                      }`}
                    />
                    <span className="pb-contain min-w-0 flex-1 truncate" title={step.label}>
                      {step.label}
                    </span>
                  </li>
                );
              })}
            </ol>
            <p className="mt-2 text-[12px] text-[var(--ink-3)]">
              {steps.length === 0 ? "Searching provider documentation…" : "Working…"}
            </p>
          </div>
        )}
      </div>

      {/* The composer is fixed while the thread above scrolls under it, so it is
          the one legitimate case for glass here: content genuinely passes
          behind this surface. */}
      <div className="pb-glass-float shrink-0 px-3.5 pb-4 pt-3">
        {error && (
          <p role="alert" className="mb-2 text-[12px] text-[var(--danger)]">
            {error}
          </p>
        )}
        {/* Focus is signalled by light spilling out of the pill, not by a ring
            drawn on it: the surface colour stays put and only the glow changes. */}
        <div
          className="pb-composer rounded-full p-[1.5px]"
          style={{ background: COMPOSER_GRADIENT }}
        >
          <div className="flex items-end gap-2 rounded-full bg-[var(--surface)] py-1.5 pl-3.5 pr-1.5">
            {/* The bloom reacts when the field takes focus: it blooms open and
                throws a few glitters. Purely decorative, so it is aria-hidden
                and frozen under prefers-reduced-motion. */}
            <span
              className={`pb-bloom mb-1 shrink-0 ${composerFocused ? "is-active" : ""}`}
              aria-hidden="true"
            >
              <SparkleIcon size={26} />
              <i className="pb-bloom__glitter" style={{ "--a": "-58deg" }} />
              <i className="pb-bloom__glitter" style={{ "--a": "18deg" }} />
              <i className="pb-bloom__glitter" style={{ "--a": "112deg" }} />
              <i className="pb-bloom__glitter" style={{ "--a": "205deg" }} />
            </span>
            <label htmlFor="integration-agent-message" className="sr-only">
              Message the integration agent
            </label>
            <textarea
              id="integration-agent-message"
              name="integration_agent_message"
              ref={inputRef}
              rows={1}
              value={text}
              disabled={!ready || sending}
              autoComplete="off"
              onChange={(event) => setText(event.target.value)}
              onKeyDown={onKeyDown}
              onFocus={() => setComposerFocused(true)}
              onBlur={() => setComposerFocused(false)}
              placeholder={
                ready
                  ? "Message"
                  : "Configure an LLM and a scraping API to start"
              }
              className="max-h-28 min-h-[24px] w-full flex-1 resize-none border-0 bg-transparent py-1.5 text-[13px] text-[var(--ink)] !outline-none placeholder:text-[var(--ink-3)] disabled:text-[var(--ink-2)]"
            />
            {text && !sending && (
              <button
                type="button"
                onClick={() => {
                  setText("");
                  inputRef.current?.focus();
                }}
                aria-label="Clear message"
                className="grid h-7 w-7 shrink-0 place-items-center rounded-full text-[var(--ink-3)] transition-colors duration-150 hover:bg-[var(--surface-2)] hover:text-[var(--ink)] focus-visible:outline-none focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[var(--accent)]"
              >
                <ClearIcon />
              </button>
            )}
            <button
              type="button"
              onClick={send}
              disabled={!ready || sending || !text.trim()}
              aria-label={sending ? "Sending" : "Send"}
              title="Enter to send, Shift+Enter for a new line"
              className="grid h-9 w-9 shrink-0 place-items-center rounded-full bg-[var(--ink)] text-[var(--surface)] transition-colors duration-150 hover:bg-[var(--btn-primary-hover)] focus-visible:outline-none focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[var(--accent)] disabled:bg-[var(--surface)] disabled:text-[var(--ink-2)]"
            >
              <SendIcon />
            </button>
          </div>
        </div>
      </div>
      </section>
    </div>
  );
}
