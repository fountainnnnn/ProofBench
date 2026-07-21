import { Link } from "react-router-dom";
import Logo from "../components/Logo.jsx";
import { BTN_PRIMARY, BTN_SECONDARY, PANEL } from "../components/ui.jsx";

const PRINCIPLES = [
  {
    title: "Deterministic scores",
    body: "Every number comes from an evaluator that compares output to your labelled ground truth. No model grades another model.",
  },
  {
    title: "Verified execution",
    body: "Tools that can safely run are built and executed in an isolated Daytona sandbox, on your data, and the sandbox log is kept.",
  },
  {
    title: "Honest evidence labels",
    body: "Hosted and unrunnable tools are compared from their own documentation and are labelled that way. They are never shown as executed.",
  },
];

// A stable snapshot of completed run 1c06b60b9717. Keeping the snapshot local
// makes the public page deterministic while preserving the run's real basis:
// documentation comparison only, with no candidate presented as executed.
const SAMPLE_ROWS = [
  { name: "aws", score: "49/100", basis: "docs" },
  { name: "gcp", score: "49/100", basis: "docs" },
  { name: "heroku", score: "34/100", basis: "docs" },
];

function BasisCell({ basis }) {
  if (basis === "verified") {
    return (
      <span className="inline-flex items-center gap-1.5 whitespace-nowrap rounded-full bg-[var(--ok-tint)] px-2.5 py-0.5 text-[12px] font-medium text-[var(--ok)]">
        <svg
          aria-hidden="true"
          viewBox="0 0 12 12"
          className="h-3 w-3 shrink-0"
          fill="none"
          stroke="currentColor"
          strokeWidth="1.8"
          strokeLinecap="round"
          strokeLinejoin="round"
        >
          <path d="M2.5 6.5 5 9l4.5-6" />
        </svg>
        Verified in Daytona
      </span>
    );
  }
  return (
    <span className="inline-flex items-center gap-1.5 whitespace-nowrap rounded-full border border-dashed border-[var(--line-strong)] px-2.5 py-0.5 text-[12px] font-medium text-[var(--ink-2)]">
      <span aria-hidden="true" className="h-1.5 w-1.5 rounded-full border border-current" />
      Docs only, not executed
    </span>
  );
}

function SampleVerdict() {
  return (
    <div id="sample-verdict" className={`${PANEL} scroll-mt-6 p-5 sm:p-6`}>
      <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
        <span className="pb-eyebrow">Sample output</span>
        <span className="text-[12px] text-[var(--ink-3)]">
          Recorded run 1c06b60b9717. Documentation comparison only.
        </span>
      </div>

      <div className="mt-5">
        <div className="pb-eyebrow">Verdict</div>
        <p className="pb-display mt-1.5 text-[32px] leading-tight text-[var(--ink)]">
          aws
        </p>
        <p className="mt-1 text-[13px] leading-relaxed text-[var(--ink-2)]">
          Ranked first of five candidates at 49/100, level with gcp on the primary metric.
        </p>
      </div>

      <table className="mt-5 w-full text-left text-[13px]">
        <caption className="sr-only">Sample candidate comparison</caption>
        <thead>
          <tr>
            <th scope="col" className="pb-2 text-[12px] font-semibold text-[var(--ink-3)]">Candidate</th>
            <th scope="col" className="pb-2 text-right text-[12px] font-semibold text-[var(--ink-3)]">Suitability</th>
            <th scope="col" className="pb-2 text-right text-[12px] font-semibold text-[var(--ink-3)]">Evidence</th>
          </tr>
        </thead>
        <tbody>
          {SAMPLE_ROWS.map((row) => (
            <tr key={row.name}>
              <td className="py-2.5 font-medium text-[var(--ink)]">{row.name}</td>
              <td className="pb-mono py-2.5 text-right text-[12px] text-[var(--ink-2)]">
                {row.score}
              </td>
              <td className="py-2.5 text-right">
                <BasisCell basis={row.basis} />
              </td>
            </tr>
          ))}
        </tbody>
      </table>

      <p className="mt-4 text-[12px] leading-relaxed text-[var(--ink-3)]">
        Suitability was assessed from vendor documentation. No candidate in this recorded run
        was executed or presented as sandbox verified.
      </p>
    </div>
  );
}

export default function Landing() {
  return (
    <div className="pb-viewport-min pb-safe-inline-shell flex flex-col bg-[var(--paper)] text-[var(--ink)]">
      <a
        href="#landing-main"
        className="sr-only z-50 rounded-[12px] bg-[var(--surface)] px-3 py-2 text-[13px] font-medium text-[var(--accent)] shadow-lift focus:not-sr-only focus:fixed focus:left-4 focus:top-4"
      >
        Skip to main content
      </a>

      <header className="pb-safe-top">
        <div className="mx-auto flex min-h-16 w-full max-w-canvas items-center justify-between gap-4 px-4 sm:px-8">
          <Logo />
          <Link to="/app/benchmark" className={BTN_SECONDARY}>
            Open console
          </Link>
        </div>
      </header>

      <main id="landing-main" tabIndex={-1} className="flex-1">
        <div className="mx-auto w-full max-w-canvas px-4 py-12 sm:px-8 sm:py-16">
          <div className="grid grid-cols-1 gap-x-[32px] gap-y-12 lg:grid-cols-12">
            <div className="lg:col-span-5 lg:pt-6">
              <h1 className="pb-display max-w-[18ch] text-[40px] leading-[46px] tracking-[-0.03em] text-[var(--ink)] sm:text-[56px] sm:leading-[62px]">
                Verdicts on software tools, with the evidence attached.
              </h1>
              <p className="mt-6 max-w-[52ch] text-[16px] leading-[26px] text-[var(--ink-2)]">
                ProofBench reads a candidate tool's own documentation, builds the integration,
                runs it against your labelled data where that is safe to do, and scores the
                output deterministically. Where a tool cannot be run, it says so instead of
                guessing.
              </p>

              <div className="mt-8 flex flex-wrap items-center gap-3">
                <Link to="/app/benchmark" className={BTN_PRIMARY}>
                  Run a benchmark
                </Link>
                <a href="#sample-verdict" className={BTN_SECONDARY}>
                  See a sample verdict
                </a>
              </div>

              <p className="mt-5 max-w-[52ch] text-[12px] leading-5 text-[var(--ink-3)]">
                This deployment runs locally. In the local profile there is no sign-in and no
                API token to enter.
              </p>
            </div>

            <div className="lg:col-span-7">
              <SampleVerdict />
            </div>
          </div>

          <section className="mt-28" aria-labelledby="principles-heading">
            <h2
              id="principles-heading"
              className="max-w-[24ch] text-[24px] font-semibold tracking-[-0.02em] text-[var(--ink)]"
            >
              How a verdict is produced.{" "}
              <span className="text-[var(--ink-2)]">Three rules, no exceptions.</span>
            </h2>
            <div className="mt-8 divide-y divide-[var(--line)]">
              {PRINCIPLES.map((principle) => (
                <div
                  key={principle.title}
                  className="grid gap-x-8 gap-y-2 py-6 md:grid-cols-12"
                >
                  <h3 className="text-[16px] font-semibold text-[var(--ink)] md:col-span-4">
                    {principle.title}
                  </h3>
                  <p className="max-w-[46ch] text-[13px] leading-relaxed text-[var(--ink-2)] md:col-span-8">
                    {principle.body}
                  </p>
                </div>
              ))}
            </div>
          </section>
        </div>
      </main>
    </div>
  );
}
