function phaseColor(phase) {
  const p = String(phase || "").toLowerCase();
  if (p === "done") return "var(--ok)";
  if (p === "failed") return "var(--err)";
  if (p === "validating") return "var(--warn)";
  if (p === "intake" || p === "spec_confirm") return "var(--text-3)";
  return "var(--info)";
}

export default function Sidebar({ sessions, activeId, onSelect, onNew, onDelete }) {
  return (
    <aside className="flex h-full w-[220px] shrink-0 flex-col border-r border-[var(--border)] bg-[var(--surface-2)]">
      <div className="px-3 pt-3">
        <button
          onClick={onNew}
          className="pb-hover-lift h-9 w-full rounded-md bg-[var(--accent)] px-3 text-[13px] font-medium text-[var(--surface)] transition-colors hover:bg-[var(--accent-hover)]"
        >
          New benchmark
        </button>
      </div>

      <div className="mt-4 flex-1 overflow-y-auto px-2 pb-4">
        <div className="px-2 pb-2 text-[11px] font-medium uppercase tracking-wide text-[var(--text-3)]">
          Sessions
        </div>
        {sessions.length === 0 && (
          <div className="px-2 py-3 text-[13px] text-[var(--text-3)]">
            Start a new benchmark to create a session.
          </div>
        )}
        {sessions.map((s) => {
          const active = s.id === activeId;
          return (
            <div
              key={s.id}
              className={`group pb-hover-lift mb-0.5 flex items-center rounded-md transition-colors ${
                active
                  ? "bg-[var(--accent-soft)] text-[var(--accent)]"
                  : "text-[var(--text-2)] hover:bg-[var(--surface)] hover:text-[var(--text)]"
              }`}
            >
              <button
                onClick={() => onSelect(s.id)}
                className="flex min-w-0 flex-1 items-center gap-2 px-2 py-1.5 text-left text-[13px]"
              >
                <span
                  className="h-2 w-2 shrink-0 rounded-full"
                  style={{ backgroundColor: phaseColor(s.phase) }}
                  title={s.phase}
                />
                <span className="truncate">{s.title || "Untitled"}</span>
              </button>
              <button
                onClick={() => onDelete(s)}
                disabled={s.is_running}
                className="mr-1 rounded px-1.5 py-1 text-[11px] text-[var(--text-3)] opacity-0 transition hover:bg-[color-mix(in_oklch,var(--err)_10%,transparent)] hover:text-[var(--err)] focus:opacity-100 group-hover:opacity-100 disabled:cursor-not-allowed disabled:opacity-0"
                title={s.is_running ? "Stop this benchmark before deleting it" : "Delete session"}
                aria-label={`Delete ${s.title || "Untitled"}`}
              >
                Delete
              </button>
            </div>
          );
        })}
      </div>
    </aside>
  );
}
