import { useEffect, useId, useRef, useState } from "react";
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
 * The question is a plain yes or no. "Yes" sends the prompt on as it stands.
 * "No" opens one field for the correction and sends that instead, keeping the
 * rejected prompt in the message so the agent knows what it is replacing. Either
 * answer is an ordinary chat message: it lands in the thread as part of the
 * record, and because the user pressed send on it, everything in it counts as
 * user-stated for the spec's constraints.
 */
export default function DirectionCard({ direction, onSend }) {
  const [clarifying, setClarifying] = useState(false);
  const [clarification, setClarification] = useState("");
  const fieldId = useId();
  const clarificationId = `${fieldId}-clarification`;
  const fieldRef = useRef(null);
  const prompt = safeVisibleText(direction?.improved_prompt || "");

  // The correction field is focused the moment it appears, so a user who
  // pressed "No" can start typing without a second click to reach it.
  useEffect(() => {
    if (clarifying) fieldRef.current?.focus();
  }, [clarifying]);

  if (!prompt) return null;

  const confirm = () => onSend(`Proceed with this direction: ${prompt}`);

  const note = safeVisibleText(clarification).trim();

  const submitCorrection = () => {
    // An empty or all-whitespace field is not a correction, so it cannot send.
    if (!note) return;
    onSend(
      `That is not quite the direction I mean. Here is what I actually want: ${note}\n\n`
      + `For context, the direction I am correcting was: ${prompt}`,
    );
  };

  return (
    <div className="shrink-0 px-4 pt-3 sm:px-8">
      <section
        aria-label="Confirm the direction"
        className="pb-glass mx-auto w-full max-w-[var(--thread-w)] rounded-[16px] p-4 shadow-[var(--shadow-card)]"
      >
        <h2 className="text-[13px] font-semibold text-[var(--ink)]">Is this what you mean?</h2>
        <p className="mt-2 text-[14px] leading-6 text-[var(--ink)]">{prompt}</p>

        <div className="mt-4 flex flex-wrap items-center gap-2">
          {/* Kept available even after "No" is pressed: a user who reads their
              own correction and decides the prompt was right all along should
              not have to close the field to accept it. */}
          <button type="button" onClick={confirm} className={BTN_PRIMARY}>
            Yes
          </button>
          <button
            type="button"
            onClick={() => setClarifying(true)}
            aria-expanded={clarifying}
            aria-controls={clarificationId}
            className={BTN_SECONDARY}
          >
            No
          </button>
        </div>

        {clarifying && (
          <div id={clarificationId} className="mt-3">
            <label htmlFor={fieldId} className="text-[12px] font-medium text-[var(--ink-2)]">
              What should it be instead?
            </label>
            <textarea
              id={fieldId}
              ref={fieldRef}
              value={clarification}
              onChange={(event) => setClarification(event.target.value)}
              rows={3}
              placeholder="Say what you actually want to compare."
              className="mt-1.5 w-full rounded-[12px] bg-[var(--surface-2)] px-3.5 py-2.5 text-[13px] leading-6 text-[var(--ink)] outline-none placeholder:text-[var(--ink-3)] focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[var(--accent)]"
            />
            <div className="mt-2 flex flex-wrap items-center gap-2">
              <button
                type="button"
                onClick={submitCorrection}
                disabled={!note}
                className={BTN_PRIMARY}
              >
                Send correction
              </button>
            </div>
          </div>
        )}
      </section>
    </div>
  );
}
