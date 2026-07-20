import { Link } from "react-router-dom";
import Logo from "../components/Logo.jsx";

const PRINCIPLES = [
  "Integrations built from each tool's own documentation.",
  "Every candidate runs in an isolated Daytona sandbox.",
  "Scoring is deterministic, against your labelled ground truth. No LLM judges.",
];

const LOG_LINES = [
  { tag: null, text: "proofbench run demo --candidates tesseract,paddleocr,easyocr" },
  { tag: "spawn", tone: "dim", text: "daytona sandbox sb-41a7 ready, debian 12, 2.1s" },
  { tag: "build", tone: "info", text: "apt-get install tesseract-ocr, adapter deps ok" },
  { tag: "validate", tone: "warn", text: "adapter output matches schema, 15/15 labels parsed" },
  { tag: "run", tone: "info", text: "15 images scored, exact match + CER" },
  { tag: "done", tone: "ok", text: "tesseract acc 0.933, cer 0.041, 1.18s median" },
];

const TONE_CLASS = {
  dim: "text-[var(--code-text)] opacity-70",
  info: "text-[var(--info)]",
  warn: "text-[var(--warn)]",
  ok: "text-[var(--ok)]",
};

const SPONSORS = ["Daytona", "Kimi", "Nosana", "Doubleword", "Oxylabs"];

function TranscriptPanel() {
  return (
    <div className="pb-hover-lift pb-card overflow-hidden rounded-card border border-[var(--border)] bg-code-bg">
      <div className="flex items-center justify-between border-b border-[color-mix(in_oklch,var(--code-text)_12%,transparent)] px-4 py-2.5">
        <span className="font-mono text-xs text-code-text opacity-70">
          agent trace, demo run
        </span>
        <span className="rounded-full bg-[color-mix(in_oklch,var(--ok)_10%,transparent)] px-2 py-0.5 text-[11px] font-medium text-ok">
          done
        </span>
      </div>
      <div className="space-y-1.5 px-4 py-3.5 font-mono text-xs leading-relaxed">
        {LOG_LINES.map((line, i) =>
          line.tag === null ? (
            <div key={i} className="text-code-text">
              <span className="mr-2 select-none opacity-60">$</span>
              {line.text}
            </div>
          ) : (
            <div key={i} className="flex gap-3">
              <span className={`w-[64px] shrink-0 ${TONE_CLASS[line.tone]}`}>
                [{line.tag}]
              </span>
              <span className="text-code-text opacity-90">{line.text}</span>
            </div>
          )
        )}
      </div>
    </div>
  );
}

export default function Landing() {
  return (
    <div className="flex min-h-screen flex-col bg-bg text-text">
      <header className="mx-auto flex h-16 w-full max-w-5xl items-center justify-between px-6">
        <Logo />
        <Link
          to="/app"
          className="pb-hover-lift inline-flex h-9 items-center rounded-md border border-border bg-surface px-3.5 text-[13px] font-medium text-text transition-colors duration-150 ease-out-quart hover:bg-surface-2"
        >
          Open console
        </Link>
      </header>

      <main className="flex-1">
        <section className="mx-auto w-full max-w-5xl px-6 pb-16 pt-20">
          <h1 className="max-w-2xl text-[32px] font-semibold leading-[40px] text-text">
            Benchmark tools on your own data, with grounded evidence.
          </h1>
          <p className="mt-4 max-w-xl text-lg leading-relaxed text-text-2">
            ProofBench reads each candidate's own documentation, builds the
            integration, runs it in an isolated sandbox, and scores every
            output against your labelled ground truth.
          </p>
        <div className="mt-8">
            <Link
              to="/app"
              className="pb-hover-lift inline-flex h-9 items-center rounded-md bg-accent px-4 text-[13px] font-medium text-[var(--bg)] transition-colors duration-150 ease-out-quart hover:bg-accent-hover focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/40"
            >
              Open console
            </Link>
          </div>
        </section>

        <section className="mx-auto w-full max-w-5xl px-6 pb-20">
          <div className="grid grid-cols-1 gap-10 md:grid-cols-5">
            <div className="md:col-span-2">
              <h2 className="text-[11px] font-medium uppercase tracking-wide text-text-3">
                Principles
              </h2>
              <ol className="mt-5 border-t border-border">
            {PRINCIPLES.map((p, i) => (
              <li
                key={i}
                className="flex gap-4 border-b border-border py-4 last:border-b-0 hover:bg-surface-2/80"
              >
                    <span className="w-5 shrink-0 text-[13px] text-text-3">
                      {i + 1}.
                    </span>
                    <p className="text-sm leading-relaxed text-text">{p}</p>
                  </li>
                ))}
              </ol>
            </div>
            <div className="md:col-span-3">
              <h2 className="text-[11px] font-medium uppercase tracking-wide text-text-3">
                Sample run transcript
              </h2>
              <div className="mt-5 pb-card pb-hover-lift">
                <TranscriptPanel />
              </div>
            </div>
          </div>
        </section>
      </main>

      <footer className="border-t border-border">
        <div className="mx-auto flex w-full max-w-5xl flex-wrap items-center gap-x-6 gap-y-2 px-6 py-5">
          {SPONSORS.map((s) => (
            <span key={s} className="text-xs text-text-3">
              {s}
            </span>
          ))}
        </div>
      </footer>
    </div>
  );
}
