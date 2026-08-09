import { useEffect, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import {
  getIntegrationAgentStatus,
  saveProviderKey,
  streamIntegrationAgentMessage,
} from "../api.js";
import { safeVisibleText } from "../displaySafety.js";
import { safeHttpUrl } from "../linkSafety.js";
import SafeMarkdownLink from "./SafeMarkdownLink.jsx";
import { MARKDOWN_HEADINGS_IN_THREAD, PANEL, Skeleton } from "./ui.jsx";
import sparkleMark from "../assets/sparkle.png";
import proposalMark from "../assets/proposal.png";

/* Starters, not instructions: a blank textarea is the weakest possible
   invitation (recognition over recall), so the empty state hands over two
   real questions the agent can act on immediately. */
// Matches the field's max-h-28 so the JS growth and the CSS cap agree.
const COMPOSER_MAX_HEIGHT = 112;
// Below this the field is not really laid out (a closed or collapsed panel
// reports a few pixels), every character wraps onto its own line, and
// scrollHeight describes a column one character wide rather than the text.
const COMPOSER_MIN_MEASURABLE_WIDTH = 120;

const PROMPTS = [
  "What are the model options for Doubleword?",
  "Add support for Mistral",
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

  /* The agent decides what a request needs before doing any of it, and that
     decision is the most useful thing to show: it is why the next steps happen
     at all, and for a question it can answer outright there are no next steps
     to explain themselves. */
  if (phase === "thinking") {
    return [...steps, { key: "plan", kind: "step", label: "Planning…" }];
  }
  if (phase === "plan") {
    const thought = safeVisibleText(event?.thought);
    const index = steps.findIndex((step) => step.key === "plan");
    if (index === -1 || !thought) return steps;
    const next = [...steps];
    next[index] = { ...next[index], label: thought };
    return next;
  }
  if (phase === "search") {
    return [...steps, {
      key: "search",
      kind: "step",
      label: safeVisibleText(event?.query)
        ? `Searching for ${safeVisibleText(event.query)}`
        : "Searching official documentation",
    }];
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

/* The agent answers in markdown, and since it also answers questions rather
   than only proposing connectors, that markdown now carries headings, lists,
   and inline code — not just the fenced blocks the old renderer handled. Those
   printed as literal ### and ** in the thread.

   Rendered the same way the main chat renders a reply: same GFM plugin, same
   safe-link and code handling, same demoted headings, so a reply reads
   identically wherever it appears. */
function AgentMarkdown({ text }) {
  return (
    <div className="md pb-contain overflow-x-auto text-[13px] leading-relaxed text-[var(--ink)]">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{ a: SafeMarkdownLink, ...MARKDOWN_HEADINGS_IN_THREAD }}
      >
        {safeVisibleText(text)}
      </ReactMarkdown>
    </div>
  );
}

/* The point of the agent naming a variable is that the operator does not have
   to. So the key is collected right here, in the turn that named it, rather
   than sending someone back to the Services card to retype a name they just
   read. The value goes straight to the credentials endpoint and is never part
   of the conversation the agent sees. */
function CredentialField({ credential, onSaved }) {
  const [value, setValue] = useState("");
  const [busy, setBusy] = useState(false);
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState("");
  const env = safeVisibleText(credential?.env);
  const label = safeVisibleText(credential?.label) || env;
  if (!env) return null;

  const submit = async (event) => {
    event.preventDefault();
    if (busy || !value) return;
    setBusy(true);
    setError("");
    try {
      await saveProviderKey(env, value);
      setValue("");
      setSaved(true);
      onSaved?.();
    } catch (failure) {
      setError(failure.message || "Could not save that key.");
    } finally {
      setBusy(false);
    }
  };

  if (saved) {
    return (
      <p className="mt-2.5 rounded-[12px] bg-[var(--surface-2)] px-3 py-2.5 text-[12px] leading-relaxed text-[var(--ink-2)]">
        Saved to <code className="pb-mono text-[var(--ink)]">{env}</code>. It shows up under
        Services, and you can change or remove it there.
      </p>
    );
  }

  return (
    <form onSubmit={submit} className="mt-2.5 rounded-[12px] bg-[var(--surface-2)] px-3 py-2.5">
      <p className="text-[12px] leading-relaxed text-[var(--ink-2)]">
        Paste your {label} key and it is stored as{" "}
        <code className="pb-mono text-[var(--ink)]">{env}</code>.
      </p>
      <div className="mt-2 flex items-start gap-2">
        <input
          type="password"
          value={value}
          onChange={(event) => setValue(event.target.value)}
          placeholder="API key"
          aria-label={`${label} API key`}
          autoComplete="off"
          spellCheck={false}
          disabled={busy}
          className="min-w-0 flex-1 rounded-[8px] bg-[var(--surface)] px-2.5 py-1.5 text-[12px] text-[var(--ink)] outline-none ring-1 ring-[var(--line)] focus:ring-[var(--accent)]"
        />
        <button
          type="submit"
          disabled={busy || !value}
          className="shrink-0 rounded-[8px] bg-[var(--accent)] px-3 py-1.5 text-[12px] font-medium text-[var(--on-accent)] disabled:opacity-50"
        >
          {busy ? "Saving" : "Save key"}
        </button>
      </div>
      {error && (
        <p role="alert" className="mt-1.5 text-[12px] text-[var(--danger)]">
          {error}
        </p>
      )}
    </form>
  );
}

function AgentTurn({ turn, onCredentialSaved }) {
  return (
    <div className="min-w-0">
      {turn.text && <AgentMarkdown text={turn.text} />}
      {/* A plain answer needs no verdict block: the proposal mark and a status
          word would dress a direct reply up as an integration outcome. */}
      {turn.implementation && turn.implementation.status !== "answer" && (
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
      {turn.credential && (
        <CredentialField credential={turn.credential} onSaved={onCredentialSaved} />
      )}
      <Sources sources={turn.sources} />
    </div>
  );
}

/* An operational console for adding a provider, not a general chatbot. It reads
   the same readiness the rest of Settings reads, but through its own endpoint:
   this agent needs a default LLM and a scraping provider specifically, which is
   a narrower question than "can this deployment run a benchmark".

   The one credential this panel writes is the one it just resolved a name for,
   in the turn that resolved it. Everything else about credentials — reading,
   replacing, removing — stays the Services card's job. */
export default function IntegrationAgentPanel({
  className = "",
  refreshKey = 0,
  focusRequest = 0,
  onCredentialSaved,
}) {
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

  /* Settings' add-a-service form hands anything it cannot offer over to this
     agent. Landing the caret in the composer is the whole handoff: the panel is
     a column away on a wide screen and a scroll away on a narrow one, so
     without this the operator is told to ask and then left to find where. */
  useEffect(() => {
    if (!focusRequest) return;
    const node = inputRef.current;
    if (!node) return;
    node.scrollIntoView?.({ block: "center" });
    node.focus();
  }, [focusRequest]);

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
          credential: reply?.credential || null,
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

  /* A one-row textarea silently clips its second line, so a starter long
     enough to wrap rendered as a half-sentence. Grow to fit, up to the same
     cap the stylesheet enforces, then let it scroll.

     The width guard is not defensive padding: while the panel is closed the
     field is laid out at zero width, every character wraps onto its own line,
     and scrollHeight reports the height of a column one character wide (714px
     for a single sentence). Measuring then would fix that nonsense as an
     inline height the moment the panel opened. */
  useEffect(() => {
    const field = inputRef.current;
    if (!field) return;
    if (!text) {
      // Empty field: drop the inline height entirely and let the stylesheet's
      // single-row minimum govern again. Leaving the last measured height in
      // place left an empty composer standing several rows tall.
      field.style.height = "";
      return;
    }
    if (field.clientWidth < COMPOSER_MIN_MEASURABLE_WIDTH) return;
    field.style.height = "auto";
    field.style.height = `${Math.min(field.scrollHeight, COMPOSER_MAX_HEIGHT)}px`;
  }, [text]);

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
          Ask what this deployment already supports, or point the agent at a provider it does
          not have yet and get back a connector proposal from that vendor's own documentation.
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
                  : "Ask about a provider"}
              </p>
              <p className="pb-contain mt-1 max-w-[38ch] text-[13px] leading-relaxed text-[var(--ink-2)]">
                {blocked
                  ? "One default LLM and one web scraping API must be configured first."
                  : "It answers from what ProofBench already implements, and reads the vendor's own documentation when it does not. Activating anything stays your call."}
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
                    <AgentTurn turn={turn} onCredentialSaved={onCredentialSaved} />
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
              {steps.length === 0 ? "Thinking…" : "Working…"}
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
          className="pb-composer rounded-[22px] p-[1.5px]"
          style={{ background: COMPOSER_GRADIENT }}
        >
          <div className="flex items-end gap-2 rounded-[21px] bg-[var(--surface)] py-1.5 pl-3.5 pr-1.5">
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
