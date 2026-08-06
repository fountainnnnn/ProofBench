export default function Logo({ size = 22, withWordmark = true }) {
  return (
    <span className="inline-flex items-center gap-2">
      <svg
        width={size}
        height={size}
        viewBox="0 0 32 32"
        role="img"
        aria-label="ProofBench logo"
        focusable="false"
        data-proofbench-mark
      >
        <rect x="2" y="2" width="28" height="28" rx="8" fill="var(--ink)" />
        {/* A compact custom PB ligature. The shared middle joint keeps it
            readable as one identity rather than two typeset initials. */}
        <path
          d="M9.5 24V8.5h7c3.8 0 6 1.65 6 4.25S20.3 17 16.5 17h-7"
          fill="none"
          stroke="var(--surface)"
          strokeWidth="3.2"
          strokeLinecap="round"
          strokeLinejoin="round"
          data-logo-letter="p"
        />
        <path
          d="M14 17v7h4c3.45 0 5.5-1.35 5.5-3.5S21.45 17 18 17h-4Z"
          fill="none"
          stroke="var(--surface)"
          strokeWidth="3.2"
          strokeLinecap="round"
          strokeLinejoin="round"
          data-logo-letter="b"
        />
      </svg>
      {withWordmark && (
        <span className="text-[15px] font-semibold tracking-[-0.01em] text-[var(--ink)]">
          ProofBench
        </span>
      )}
    </span>
  );
}
