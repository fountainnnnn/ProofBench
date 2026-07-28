import { useState } from "react";
import { safeVisibleText } from "../displaySafety.js";
import { BTN_PRIMARY, BTN_SECONDARY } from "./ui.jsx";

/**
 * The direction confirmation, docked above the composer.
 *
 * A vague opening request used to send the agent searching on whichever reading
 * it picked, and the mismatch only surfaced once a shortlist arrived — by which
 * point the round budget was spent. This card asks first. The body is the exact
 * prompt the search will run on, shown verbatim rather than paraphrased, because
 * a user cannot correct wording they were never shown.
 *
 * Confirming sends an ordinary chat message. That is deliberate: the answer
 * lands in the thread as part of the record, and because the user pressed send
 * on it, everything in it counts as user-stated for the spec's constraints.
 */
export default function DirectionCard({ direction, onSend, onDismiss }) {
  const assumptions = Array.isArray(direction?.assumptions) ? direction.assumptions : [];
  const unknowns = Array.isArray(direction?.unknowns) ? direction.unknowns : [];
  // Every inference starts accepted: the agent grounded each one in the user's
  // own words, so the click a user should have to make is the correction, not
  // the confirmation.
  const [rejected, setRejected] = useState(() => new Set());
  const prompt = safeVisibleText(direction?.improved_prompt || "");

  if (!prompt) return null;

  const toggle = (index) => setRejected((current) => {
    const next = new Set(current);
    if (next.has(index)) next.delete(index);
    else next.add(index);
    return next;
  });

  const send = () => {
    const kept = assumptions
      .filter((_item, index) => !rejected.has(index))
      .map((item) => safeVisibleText(item?.assumption || ""))
      .filter(Boolean);
    const dropped = assumptions
      .filter((_item, index) => rejected.has(index))
      .map((item) => safeVisibleText(item?.assumption || ""))
      .filter(Boolean);
    let text = `Proceed with this direction: ${prompt}`;
    if (kept.length) text += `\nConfirmed assumptions: ${kept.join("; ")}`;
    if (dropped.length) text += `\nNot true: ${dropped.join("; ")}`;
    onSend(text);
  };

  return (
    <div className="shrink-0 px-4 pt-3 sm:px-8">
      <section
        aria-label="Confirm the direction"
        className="pb-glass mx-auto w-full max-w-[var(--thread-w)] rounded-[16px] p-4 shadow-[var(--shadow-card)]"
      >
        <h2 className="text-[13px] font-semibold text-[var(--ink)]">Here's what I understood</h2>
        <p className="mt-2 text-[14px] leading-6 text-[var(--ink)]">{prompt}</p>

        {assumptions.length > 0 && (
          <div className="mt-3">
            <p className="text-[11px] text-[var(--ink-3)]">
              Assumptions I made. Switch off anything that is not true.
            </p>
            <ul className="mt-1.5 flex flex-wrap gap-1.5">
              {assumptions.map((item, index) => {
                const label = safeVisibleText(item?.assumption || "");
                const basis = safeVisibleText(item?.basis || "");
                const kept = !rejected.has(index);
                return (
                  <li key={`${label}-${index}`}>
                    <button
                      type="button"
                      onClick={() => toggle(index)}
                      aria-pressed={kept}
                      title={basis ? `From your words: ${basis}` : undefined}
                      className={`inline-flex min-h-8 items-center gap-1.5 rounded-full px-3 text-[12px] transition-colors duration-150 ${
                        kept
                          ? "bg-[var(--ok-tint)] text-[var(--ink)]"
                          : "bg-[var(--surface-2)] text-[var(--ink-3)] line-through"
                      }`}
                    >
                      <span aria-hidden="true">{kept ? "✓" : "✕"}</span>
                      <span>{label}</span>
                    </button>
                  </li>
                );
              })}
            </ul>
          </div>
        )}

        {unknowns.length > 0 && (
          <div className="mt-3">
            {/* Named, not guessed: the point of showing these is that nothing
                was filled in on the user's behalf. */}
            <p className="text-[11px] text-[var(--ink-3)]">Not assumed either way</p>
            <ul className="mt-1.5 flex flex-wrap gap-1.5">
              {unknowns.map((item, index) => (
                <li
                  key={`${item}-${index}`}
                  className="inline-flex min-h-8 items-center rounded-full bg-[var(--surface-2)] px-3 text-[12px] text-[var(--ink-3)]"
                >
                  {safeVisibleText(item)}
                </li>
              ))}
            </ul>
          </div>
        )}

        <div className="mt-4 flex flex-wrap items-center gap-2">
          <button type="button" onClick={send} className={BTN_PRIMARY}>
            Search with this
          </button>
          <button type="button" onClick={onDismiss} className={BTN_SECONDARY}>
            I'll rephrase
          </button>
        </div>
      </section>
    </div>
  );
}
