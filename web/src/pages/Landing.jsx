import { Link } from "react-router-dom";
import Logo from "../components/Logo.jsx";
import StatusIcon from "../components/StatusIcon.jsx";
import { AmbientOrb, GlowCard, Reveal, SectionHeader, useStuckNav } from "../components/glow.jsx";
import shotOverview from "../assets/product/shot-overview.png";

/* The public front door, built to the glow-card grammar: a centered header
   block then content in soft radial-top-glow cards, the same shape on every
   section. Real product screenshots carry the credibility; the copy stays the
   product's real claims (deterministic scores, sandbox verification, honest
   evidence labels). */

const PRINCIPLES = [
  {
    eyebrow: "01 · Scoring",
    title: "Deterministic scores",
    body: "Every number comes from an evaluator that compares output to your labelled ground truth. No model grades another model.",
  },
  {
    eyebrow: "02 · Execution",
    title: "Verified execution",
    body: "Tools that can safely run are built and executed in an isolated sandbox, on your data, and the execution log is kept.",
  },
  {
    eyebrow: "03 · Evidence",
    title: "Honest evidence labels",
    body: "Hosted and unrunnable tools are compared from their own documentation and labelled that way. They are never shown as executed.",
  },
];

// A stable snapshot of completed run 1c06b60b9717 — documentation comparison
// only, no candidate presented as executed. Kept local so the public page is
// deterministic while staying true to the recorded run's real basis.
const SAMPLE_ROWS = [
  { name: "aws", score: "49/100" },
  { name: "gcp", score: "49/100" },
  { name: "heroku", score: "34/100" },
];

function DocsChip() {
  return (
    <span className="inline-flex items-center gap-1.5 whitespace-nowrap rounded-full border border-dashed border-[var(--line-strong)] px-2.5 py-0.5 text-[12px] font-medium text-[var(--ink-2)]">
      <StatusIcon tone="docs" size={12} />
      Docs only, not executed
    </span>
  );
}

/* Hero visual: a real console screenshot inside a mesh glow card, with one
   floating glass prop layered over it so it reads as a product mid-use. */
function HeroVisual() {
  return (
    <div className="relative">
      <GlowCard glow={0} className="p-2.5 sm:p-3">
        <div className="pb-glow-mesh" />
        <div className="relative overflow-hidden rounded-[14px] border border-[var(--line)] shadow-[var(--shadow-card)]">
          <img
            src={shotOverview}
            alt="ProofBench Overview: a benchmark activity calendar, ranked verdicts, and the tools evaluated"
            className="block w-full"
            loading="eager"
            width={1440}
            height={900}
          />
        </div>
      </GlowCard>

      {/* Floating glass prop — a verdict at a glance. */}
      <div className="absolute -bottom-5 -left-4 hidden max-w-[16rem] rounded-[16px] border border-[var(--line)] bg-[color-mix(in_oklab,var(--surface)_80%,transparent)] p-3.5 shadow-[var(--shadow-lift)] backdrop-blur-md sm:block">
        <div className="pb-eyebrow-glow">Verdict</div>
        <p className="pb-display mt-1 text-[22px] leading-none text-[var(--ink)]">aws</p>
        <p className="mt-1.5 text-[12px] leading-snug text-[var(--ink-2)]">
          Ranked first of five, level with gcp at 49/100.
        </p>
      </div>
    </div>
  );
}

/* One feature card: an artifact-first glow card. Each principle gets a distinct
   real prop rather than a decorative icon. */
function PrincipleCard({ principle, index }) {
  return (
    <GlowCard glow={index} className="flex min-h-[19rem] flex-col p-6">
      <span className="pb-eyebrow-glow">{principle.eyebrow}</span>

      <div className="mt-4 min-h-0 flex-1">
        {index === 0 && (
          <div className="rounded-[14px] border border-[var(--line)] bg-[var(--surface)] p-3.5 shadow-[var(--shadow-card)]">
            <div className="flex items-center justify-between text-[12px]">
              <span className="font-medium text-[var(--ink)]">exact_accuracy</span>
              <span className="pb-mono text-[var(--ink-2)]">92.0%</span>
            </div>
            <div className="mt-2 h-1.5 overflow-hidden rounded-full bg-[var(--surface-2)]">
              <span className="block h-full w-[92%] rounded-full bg-[var(--accent)]" />
            </div>
            <p className="mt-3 text-[11.5px] leading-relaxed text-[var(--ink-2)]">
              Compared to your <span className="pb-glow-word font-semibold">labelled ground truth</span>, not to another model's opinion.
            </p>
          </div>
        )}

        {index === 1 && (
          <div className="rounded-[14px] border border-[var(--line)] bg-[var(--code-bg)] p-3.5 shadow-[var(--shadow-card)]">
            <div className="pb-mono space-y-1 text-[11px] leading-relaxed text-[var(--code-text)]">
              <p><span className="text-[var(--ok)]">✓</span> daytona sandbox created</p>
              <p><span className="text-[var(--ok)]">✓</span> adapter built · deps resolved</p>
              <p><span className="text-[var(--ok)]">✓</span> ran on 15 labelled docs</p>
              <p className="text-[color-mix(in_oklab,var(--code-text)_60%,transparent)]">log retained · run 1c06b60b</p>
            </div>
          </div>
        )}

        {index === 2 && (
          <div className="flex flex-col items-start gap-2">
            <span className="inline-flex items-center gap-1.5 whitespace-nowrap rounded-full bg-[var(--ok-tint)] px-2.5 py-1 text-[12px] font-medium text-[var(--ok)]">
              <StatusIcon tone="ok" size={12} />
              Verified by execution
            </span>
            <DocsChip />
            <p className="mt-1 text-[11.5px] leading-relaxed text-[var(--ink-2)]">
              A tool is only ever shown as executed when it actually was.
            </p>
          </div>
        )}
      </div>

      <div className="mt-5">
        <h3 className="text-[1.05rem] font-semibold text-[var(--ink)]">{principle.title}</h3>
        <p className="mt-1.5 text-[13px] leading-relaxed text-[var(--ink-2)]">{principle.body}</p>
      </div>
    </GlowCard>
  );
}

function SampleVerdictCard() {
  return (
    <GlowCard glow={1} className="p-6 sm:p-8">
      <div className="pb-glow-mesh" />
      <div className="relative grid gap-8 lg:grid-cols-12">
        <div className="lg:col-span-5">
          <span className="pb-eyebrow-glow">Sample output</span>
          <p className="pb-display mt-3 text-[34px] leading-none text-[var(--ink)]">aws</p>
          <p className="mt-2 max-w-[34ch] text-[13px] leading-relaxed text-[var(--ink-2)]">
            Ranked first of five candidates at 49/100, level with gcp on the primary metric.
          </p>
          <p className="mt-4 text-[12px] leading-relaxed text-[var(--ink-3)]">
            Recorded run 1c06b60b9717. Suitability assessed from vendor documentation — no
            candidate was executed or presented as sandbox verified.
          </p>
        </div>

        <div className="lg:col-span-7">
          <div className="overflow-hidden rounded-[14px] border border-[var(--line)] bg-[var(--surface)] shadow-[var(--shadow-card)]">
            <table className="w-full text-left text-[13px]">
              <caption className="sr-only">Sample candidate comparison</caption>
              <thead>
                <tr className="border-b border-[var(--line)]">
                  <th scope="col" className="px-4 py-2.5 text-[12px] font-semibold text-[var(--ink-3)]">Candidate</th>
                  <th scope="col" className="px-4 py-2.5 text-right text-[12px] font-semibold text-[var(--ink-3)]">Suitability</th>
                  <th scope="col" className="px-4 py-2.5 text-right text-[12px] font-semibold text-[var(--ink-3)]">Evidence</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-[var(--line)]">
                {SAMPLE_ROWS.map((row) => (
                  <tr key={row.name}>
                    <td className="px-4 py-3 font-medium text-[var(--ink)]">{row.name}</td>
                    <td className="pb-mono px-4 py-3 text-right text-[12px] text-[var(--ink-2)]">{row.score}</td>
                    <td className="px-4 py-3 text-right"><DocsChip /></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      </div>
    </GlowCard>
  );
}

export default function Landing() {
  const stuck = useStuckNav(48);

  return (
    <div className="relative min-h-screen overflow-x-hidden bg-[var(--paper)] text-[var(--ink)]">
      <a
        href="#landing-main"
        className="sr-only z-50 rounded-[12px] bg-[var(--surface)] px-3 py-2 text-[13px] font-medium text-[var(--accent)] shadow-lift focus:not-sr-only focus:fixed focus:left-4 focus:top-4"
      >
        Skip to main content
      </a>

      <AmbientOrb style={{ top: "-14%", left: "-6%" }} />
      <AmbientOrb style={{ top: "44%", right: "-18%", opacity: 0.07 }} />

      {/* Detaching nav: transparent over the hero, a floating glass island once
          the page scrolls. */}
      <div className="pb-nav-float" data-stuck={stuck ? "true" : "false"}>
        <div className="mx-auto flex min-h-16 w-full max-w-canvas items-center justify-between gap-4 px-4 sm:px-8">
          <Logo />
          <Link to="/app/benchmark" className="pb-btn-glass">
            Open console
          </Link>
        </div>
      </div>

      <main id="landing-main" tabIndex={-1} className="relative">
        {/* Hero — the older sibling of a glow card. */}
        <section className="mx-auto w-full max-w-canvas px-4 pb-24 pt-10 sm:px-8 sm:pt-16">
          <div className="grid grid-cols-1 items-center gap-x-[40px] gap-y-14 lg:grid-cols-12">
            <div className="lg:col-span-5">
              <span className="pb-eyebrow-glow">Benchmark · with receipts</span>
              <h1 className="pb-glow-title mt-4 text-[clamp(2.4rem,1.4rem+3.4vw,3.6rem)] leading-[1.04]">
                Verdicts on software tools, with the <span className="pb-glow-word">evidence</span> attached.
              </h1>
              <p className="pb-glow-sub mt-5 max-w-[52ch]">
                ProofBench reads a candidate tool's own documentation, builds the integration,
                runs it against your labelled data where that is safe, and scores the output
                deterministically. Where a tool cannot be run, it says so instead of guessing.
              </p>

              <div className="mt-8 flex flex-wrap items-center gap-3">
                <Link to="/app/benchmark" className="pb-btn-glow">
                  Run a benchmark
                </Link>
                <a href="#sample-verdict" className="pb-btn-glass">
                  See a sample verdict
                </a>
              </div>

              <p className="mt-5 max-w-[48ch] text-[12px] leading-5 text-[var(--ink-3)]">
                This deployment runs locally. In the local profile there is no sign-in and no
                password to enter.
              </p>
            </div>

            <div className="lg:col-span-7">
              <HeroVisual />
            </div>
          </div>
        </section>

        {/* How a verdict is produced — three glow cards. */}
        <section className="mx-auto w-full max-w-canvas px-4 py-16 sm:px-8" aria-labelledby="principles-heading">
          <Reveal>
            <SectionHeader
              id="principles-heading"
              eyebrow="How it works"
              title="How a verdict is produced."
              subtitle="Three rules, no exceptions. The same discipline runs behind every score ProofBench prints."
            />
          </Reveal>
          <Reveal className="mt-12 grid grid-cols-1 gap-6 md:grid-cols-2 lg:grid-cols-3">
            {PRINCIPLES.map((principle, index) => (
              <PrincipleCard key={principle.title} principle={principle} index={index} />
            ))}
          </Reveal>
        </section>

        {/* A real verdict. */}
        <section id="sample-verdict" className="mx-auto w-full max-w-canvas scroll-mt-24 px-4 py-16 sm:px-8" aria-labelledby="sample-heading">
          <Reveal>
            <SectionHeader
              id="sample-heading"
              eyebrow="See it"
              title="A verdict, and the evidence behind it."
              subtitle="This is a recorded run, shown exactly as ProofBench reported it — including what it would not claim."
            />
          </Reveal>
          <Reveal className="mt-12">
            <SampleVerdictCard />
          </Reveal>
        </section>

        {/* Dark band CTA. */}
        <section className="pb-band relative mt-8 overflow-hidden">
          <AmbientOrb style={{ bottom: "-30%", left: "50%", transform: "translateX(-50%)" }} />
          <div className="relative mx-auto w-full max-w-canvas px-4 py-24 text-center sm:px-8">
            <Reveal>
              <span className="pb-eyebrow-glow">Ready when you are</span>
              <h2 className="pb-glow-title mt-4">
                Stop guessing which tool wins.
              </h2>
              <p className="mx-auto mt-4 max-w-[42rem] text-[1.075rem] leading-relaxed text-[var(--band-ink-2)]">
                Attach a labelled dataset, name the candidates, and get a scored verdict with the
                sandbox log attached.
              </p>
              <div className="mt-8 flex flex-wrap items-center justify-center gap-3">
                <Link to="/app/benchmark" className="pb-btn-glow">
                  Run a benchmark
                </Link>
                <Link to="/app/overview" className="pb-btn-glass">
                  Explore the console
                </Link>
              </div>
            </Reveal>
          </div>
        </section>

        <footer className="mx-auto flex w-full max-w-canvas flex-col items-center justify-between gap-3 px-4 py-8 text-[12px] text-[var(--ink-3)] sm:flex-row sm:px-8">
          <Logo />
          <span>Local profile · no sign-in required</span>
        </footer>
      </main>
    </div>
  );
}
