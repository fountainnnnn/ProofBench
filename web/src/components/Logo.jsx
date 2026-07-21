export default function Logo({ size = 22, withWordmark = true }) {
  return (
    <span className="inline-flex items-center gap-2">
      <svg width={size} height={size} viewBox="0 0 32 32" aria-label="ProofBench logo">
        <rect width="32" height="32" rx="7" fill="var(--accent)" />
        <rect x="7" y="18" width="4" height="7" fill="var(--surface)" />
        <rect x="13.5" y="14" width="4" height="11" fill="var(--surface)" />
        <path
          d="M20 20.5 22.75 23.25 27 14.5"
          stroke="var(--surface)"
          strokeWidth="2.8"
          fill="none"
          strokeLinecap="round"
          strokeLinejoin="round"
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
