import { useEffect, useMemo, useRef, useState } from "react";
import { safeVisibleText } from "../displaySafety.js";
import { phaseLabel, phaseTone } from "../phaseLabel.js";
import StatusIcon from "./StatusIcon.jsx";

const TERMINAL_PHASES = new Set(["DONE", "FAILED", "STOPPED"]);

function phaseFor(lines, candidateStatus, overallPhase) {
  if (TERMINAL_PHASES.has(overallPhase)) {
    const status = String(candidateStatus || "").toUpperCase();
    return ["FAILED", "ERROR", "STOPPED"].includes(status) ? "FAILED" : overallPhase;
  }
  const latest = [...lines].reverse().find((entry) => entry?.phase)?.phase;
  return String(latest || candidateStatus || overallPhase || "provisioning").toUpperCase();
}

function commandLine(line) {
  return String(line || "").trimStart().startsWith("$ ");
}

function SandboxTerminal({ name, lines, files, candidateStatus, overallPhase, running }) {
  const [view, setView] = useState("activity");
  const [activeFile, setActiveFile] = useState(0);
  const scrollRef = useRef(null);
  const phase = phaseFor(lines, candidateStatus, overallPhase);
  const isLive = running && !TERMINAL_PHASES.has(phase);
  const safeName = safeVisibleText(name);
  const selectedFile = files[activeFile] || files[files.length - 1];

  useEffect(() => {
    if (!running || view !== "activity" || !scrollRef.current) return;
    scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
  }, [lines, running, view]);

  useEffect(() => {
    if (activeFile >= files.length) setActiveFile(Math.max(0, files.length - 1));
  }, [activeFile, files.length]);

  return (
    <article className="pb-sandbox-terminal" aria-label={`${safeName} sandbox`}>
      <header className="pb-sandbox-terminal__header">
        <div className="min-w-0">
          <h3 className="truncate text-[13px] font-medium text-white">{safeName}</h3>
          <p className="mt-0.5 flex items-center gap-1.5 text-[11px] text-white/55">
            <StatusIcon tone={phaseTone(phase)} size={11} pulse={isLive} />
            {phaseLabel(phase)}
          </p>
        </div>
        <span className="shrink-0 font-mono text-[10px] text-white/40">
          {lines.length} {lines.length === 1 ? "event" : "events"}
        </span>
      </header>

      {files.length > 0 && (
        <div className="pb-sandbox-terminal__tabs" role="tablist" aria-label={`${safeName} execution views`}>
          <button
            type="button"
            role="tab"
            aria-selected={view === "activity"}
            onClick={() => setView("activity")}
            className={view === "activity" ? "is-active" : ""}
          >
            Activity
          </button>
          <button
            type="button"
            role="tab"
            aria-selected={view === "files"}
            onClick={() => setView("files")}
            className={view === "files" ? "is-active" : ""}
          >
            Files <span>{files.length}</span>
          </button>
        </div>
      )}

      {view === "files" && files.length > 0 ? (
        <div className="flex min-h-0 flex-1 flex-col">
          {files.length > 1 && (
            <div className="flex gap-1 overflow-x-auto border-b border-white/10 px-3 py-2">
              {files.map((file, index) => (
                <button
                  key={`${file.path}-${file.revision || index}`}
                  type="button"
                  onClick={() => setActiveFile(index)}
                  className={`shrink-0 rounded-[5px] px-2 py-1 font-mono text-[10px] ${
                    index === activeFile ? "bg-white/12 text-white" : "text-white/45 hover:text-white/75"
                  }`}
                >
                  {safeVisibleText(file.path)}
                  {file.revision > 1 ? ` · v${file.revision}` : ""}
                </button>
              ))}
            </div>
          )}
          <pre className="pb-sandbox-terminal__code" role="tabpanel" aria-label={`${safeName} source code`} tabIndex={0}>
            <code>{safeVisibleText(selectedFile?.content || "")}</code>
          </pre>
        </div>
      ) : (
        <div ref={scrollRef} className="pb-sandbox-terminal__log" role="tabpanel" aria-label={`${safeName} activity log`} tabIndex={0}>
          {lines.length === 0 ? (
            <p className="text-white/40">Waiting for sandbox output…</p>
          ) : (
            lines.map((entry, index) => (
              <div
                key={`${index}-${entry.line}`}
                className={commandLine(entry.line) ? "pb-sandbox-terminal__command" : ""}
              >
                <span className="select-none text-white/20">{String(index + 1).padStart(2, "0")}</span>
                <span>{safeVisibleText(entry.line)}</span>
              </div>
            ))
          )}
        </div>
      )}
    </article>
  );
}

export default function SandboxExecutionPanel({
  open,
  onClose,
  sandboxLogs = {},
  sandboxFiles = {},
  phaseState,
  running,
}) {
  const closeRef = useRef(null);
  const sandboxes = useMemo(() => {
    const names = new Set([...Object.keys(sandboxLogs), ...Object.keys(sandboxFiles)]);
    // A live run's candidates get a card from the moment they are named, so
    // provisioning shows per-candidate progress instead of an empty panel.
    if (running && phaseState && !TERMINAL_PHASES.has(
        String(phaseState.phase || "").toUpperCase())) {
      Object.keys(phaseState.candidates || {}).forEach((name) => names.add(name));
    }
    return [...names].map((name) => ({
      name,
      lines: sandboxLogs[name] || [],
      files: sandboxFiles[name] || [],
    }));
  }, [sandboxLogs, sandboxFiles, phaseState, running]);

  useEffect(() => {
    if (!open) return undefined;
    const onKeyDown = (event) => {
      if (event.key === "Escape") onClose();
    };
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [open, onClose]);

  if (!open) return null;

  const overallPhase = String(phaseState?.phase || (running ? "RUNNING" : "DONE")).toUpperCase();
  const live = running && !TERMINAL_PHASES.has(overallPhase);

  return (
    <aside
      id="sandbox-execution-panel"
      className="pb-sandbox-panel"
      aria-labelledby="sandbox-execution-title"
      data-sandbox-execution-panel
    >
      <header className="pb-sandbox-panel__header">
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <h2 id="sandbox-execution-title" className="text-[14px] font-medium text-[var(--ink)]">
              Sandbox execution
            </h2>
            {sandboxes.length > 0 && (
              <span className="rounded-full bg-[var(--surface-2)] px-2 py-0.5 font-mono text-[10px] text-[var(--ink-3)]">
                {sandboxes.length}
              </span>
            )}
          </div>
          <p className="mt-0.5 flex items-center gap-1.5 text-[11px] text-[var(--ink-3)]" aria-live="polite">
            <StatusIcon tone={phaseTone(overallPhase)} size={11} pulse={live} />
            {live ? "Live from disposable sandboxes" : "Saved with this run"}
          </p>
        </div>
        <button
          ref={closeRef}
          type="button"
          onClick={onClose}
          aria-label="Close sandbox execution"
          className="inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-[8px] text-[var(--ink-2)] transition-colors duration-150 hover:bg-[var(--surface-2)] hover:text-[var(--ink)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)]"
        >
          <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" aria-hidden="true">
            <path d="M6 6l12 12M18 6 6 18" />
          </svg>
        </button>
      </header>

      <div className="pb-sandbox-panel__body">
        {sandboxes.length === 0 ? (
          <div className="flex h-full min-h-52 items-center justify-center px-6 text-center text-[13px] text-[var(--ink-3)]">
            The execution stream will appear when a sandbox starts.
          </div>
        ) : (
          <div className={`pb-sandbox-grid ${sandboxes.length > 1 ? "pb-sandbox-grid--split" : ""}`}>
            {sandboxes.map(({ name, lines, files }) => (
              <SandboxTerminal
                key={name}
                name={name}
                lines={lines}
                files={files}
                candidateStatus={phaseState?.candidates?.[name]}
                overallPhase={overallPhase}
                running={running}
              />
            ))}
          </div>
        )}
      </div>
    </aside>
  );
}
