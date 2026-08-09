/* Backend phase vocabulary, mapped to the words the console shows.
   The server's terminal phase is DONE; the product says "completed", because a
   run that produced evidence has completed rather than merely stopped.

   One mapping, because four surfaces render a phase (the benchmark header, the
   execution trace badge, the runs table, the typing indicator) and they must
   never drift into saying different words for the same state. Anything not
   listed keeps the backend's own wording, lowercased with underscores opened
   out, rather than being replaced by a guess. */
const PHASE_WORDS = {
  done: "completed",
};

export function phaseLabel(phase) {
  const normalized = String(phase || "")
    .toLowerCase()
    .replace(/_/g, " ")
    .trim();
  return PHASE_WORDS[normalized] || normalized;
}

/* Phase to semantic tone, so a state is drawn with the same glyph and colour
   wherever it appears (trace badge, session row, run header, candidate list). */
const PHASE_TONES = {
  done: "ok",
  provisioning: "running",
  "docs intel": "running",
  "adapter gen": "running",
  evaluating: "running",
  reporting: "running",
  failed: "danger",
  stopped: "danger",
  error: "danger",
  validating: "warn",
  building: "running",
  running: "running",
  ok: "ok",
};

export function phaseTone(phase) {
  const key = String(phase || "").toLowerCase().replace(/_/g, " ").trim();
  return PHASE_TONES[key] || "pending";
}
