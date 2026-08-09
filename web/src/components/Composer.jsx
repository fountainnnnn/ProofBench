import { useEffect, useRef, useState } from "react";
import { generateDataset, listDatasets } from "../api.js";
import { safeVisibleText } from "../displaySafety.js";
import { BTN_PRIMARY } from "./ui.jsx";

const DATASET_KIND_LABELS = {
  synthetic: "Sample labelled dataset",
  generated: "AI-generated dataset",
  upload: "Uploaded dataset",
};

const attachment =
  "inline-flex min-h-8 items-center gap-1.5 rounded-full px-2.5 text-[12px] font-medium text-[var(--ink-2)] " +
  "transition-colors duration-150 hover:bg-[var(--surface-2)] hover:text-[var(--ink)] " +
  "disabled:cursor-not-allowed disabled:bg-transparent disabled:text-[var(--ink-3)]";

export default function Composer({ onSend, onUpload, onPickExisting, onClearDataset, dataset, disabled, provenanceLocked, injectText = "" }) {
  const [text, setText] = useState("");
  const [dragging, setDragging] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [menuOpen, setMenuOpen] = useState(false);
  const [designing, setDesigning] = useState(false);
  const [designError, setDesignError] = useState("");
  const [library, setLibrary] = useState({ loading: false, records: [], error: null });
  const imagesRef = useRef(null);
  const gtRef = useRef(null);
  const textareaRef = useRef(null);
  const menuRootRef = useRef(null);
  const pending = useRef({ images: [], groundTruth: null });

  /* One trigger, every way to get a dataset: the menu closes on outside
     pointer or Escape, the way the profile menu already behaves. */
  useEffect(() => {
    if (!menuOpen) return undefined;
    const onPointer = (event) => {
      if (!menuRootRef.current?.contains(event.target)) setMenuOpen(false);
    };
    const onKey = (event) => {
      if (event.key === "Escape") setMenuOpen(false);
    };
    document.addEventListener("pointerdown", onPointer);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("pointerdown", onPointer);
      document.removeEventListener("keydown", onKey);
    };
  }, [menuOpen]);

  useEffect(() => {
    if (!menuOpen) return undefined;
    let cancelled = false;
    setLibrary((current) => ({ ...current, loading: true, error: null }));
    listDatasets()
      .then((records) => {
        if (!cancelled) setLibrary({ loading: false, records, error: null });
      })
      .catch((exc) => {
        if (!cancelled) setLibrary({ loading: false, records: [], error: exc.message });
      });
    return () => { cancelled = true; };
  }, [menuOpen]);

  /* A quick-start card clicked upstream fills, never sends. */
  useEffect(() => {
    if (injectText) {
      setText(injectText);
      textareaRef.current?.focus();
    }
  }, [injectText]);

  const send = () => {
    const t = text.trim();
    if (!t || disabled) return;
    onSend(t);
    setText("");
  };

  const onKeyDown = (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      send();
    }
  };

  /* The designer reads the benchmark description already in the composer, so
     asking is one click: no detour through the Datasets page. */
  const doDesign = async () => {
    const prompt = text.trim();
    if (!prompt) {
      setDesignError("Describe your benchmark above first; the designer builds the dataset from it.");
      return;
    }
    setDesigning(true);
    setDesignError("");
    try {
      const result = await generateDataset(prompt);
      onPickExisting?.({
        id: result.dataset_id,
        kind: "generated",
        title: result.preview?.title || "",
        image_count: result.preview?.image_count ?? null,
      });
    } catch (exc) {
      setDesignError(exc.message);
    } finally {
      setDesigning(false);
    }
  };

  const doSyntheticUpload = async () => {
    setUploading(true);
    try {
      await onUpload({ useSynthetic: true });
    } finally {
      setUploading(false);
    }
  };

  const tryUploadFiles = async () => {
    if (provenanceLocked) return;
    const { images, groundTruth } = pending.current;
    if (!images.length || !groundTruth) return;
    setUploading(true);
    try {
      await onUpload({ images, groundTruth });
      pending.current = { images: [], groundTruth: null };
    } finally {
      setUploading(false);
    }
  };

  const onDrop = (e) => {
    e.preventDefault();
    setDragging(false);
    if (provenanceLocked) return;
    const files = Array.from(e.dataTransfer.files || []);
    const imgs = files.filter((f) => f.type.startsWith("image/"));
    const csv = files.find((f) => f.name.toLowerCase().endsWith(".csv"));
    if (imgs.length) pending.current.images = imgs;
    if (csv) pending.current.groundTruth = csv;
    tryUploadFiles();
  };

  return (
    <div className="shrink-0 px-4 pb-4 pt-2 sm:px-8">
      {/* Tracks the thread's width so the composer stays flush with the
          conversation it belongs to. */}
      <div className="mx-auto w-full max-w-[var(--thread-w)]">
        <div
          onDragOver={(e) => {
            e.preventDefault();
            if (!provenanceLocked) setDragging(true);
          }}
          onDragLeave={() => setDragging(false)}
          onDrop={onDrop}
          className={`pb-glass rounded-[16px] shadow-[var(--shadow-card)] transition-shadow duration-150 focus-within:shadow-[var(--shadow-lift)] ${
            dragging ? "bg-[var(--accent-tint)]" : ""
          }`}
        >
          {/* An attached dataset is an object, not a status caption: it renders
              as a chip above the input, the way chat products show attachments. */}
          {designing && !dataset && (
            <div className="flex flex-wrap items-center gap-2 px-3 pt-3">
              <span
                className="inline-flex min-w-0 items-center gap-2 rounded-[10px] border border-dashed border-[var(--line)] py-1.5 pl-1.5 pr-3"
                role="status"
                aria-live="polite"
              >
                <span
                  aria-hidden="true"
                  className="flex h-6 w-6 shrink-0 animate-pulse items-center justify-center rounded-[7px] bg-[var(--surface-2)] text-[var(--ink-2)]"
                >
                  <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
                    <path d="M12 3l1.8 4.6L18 9l-4.2 1.4L12 15l-1.8-4.6L6 9l4.2-1.4z" />
                  </svg>
                </span>
                <span className="min-w-0">
                  <span className="block truncate text-[12px] font-medium leading-4 text-[var(--ink)]">
                    Designing your dataset…
                  </span>
                  <span className="block truncate text-[10px] leading-3 text-[var(--ink-3)]">
                    Proposing fields, writing ground truth, drawing documents. Usually under 15s.
                  </span>
                </span>
              </span>
            </div>
          )}

          {dataset && (
            <div className="flex flex-wrap items-center gap-2 px-3 pt-3">
              <span className="inline-flex min-w-0 items-center gap-2 rounded-[10px] bg-[var(--ok-tint)] py-1.5 pl-1.5 pr-3">
                <span
                  aria-hidden="true"
                  className="flex h-6 w-6 shrink-0 items-center justify-center rounded-[7px] bg-[var(--ok)] text-[var(--surface)]"
                >
                  <svg width="12" height="12" viewBox="0 0 12 12" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
                    <path d="M2.5 6.5 5 9l4.5-6" />
                  </svg>
                </span>
                <span className="min-w-0">
                  <span className="block truncate text-[12px] font-medium leading-4 text-[var(--ink)]">
                    {dataset.title
                      ? safeVisibleText(dataset.title)
                      : DATASET_KIND_LABELS[dataset.kind] || "Labelled dataset"}
                  </span>
                  {(dataset.dataset_id || dataset.id) && (
                    <span className="pb-mono block truncate text-[10px] leading-3 text-[var(--ink-3)]">
                      {safeVisibleText(dataset.dataset_id || dataset.id)}
                    </span>
                  )}
                </span>
                {onClearDataset && !provenanceLocked && (
                  <button
                    type="button"
                    onClick={onClearDataset}
                    aria-label="Detach dataset"
                    title="Detach this dataset"
                    className="ml-0.5 flex h-6 w-6 shrink-0 items-center justify-center rounded-full text-[var(--ink-2)] transition-colors duration-150 hover:bg-[var(--surface)] hover:text-[var(--ink)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)]"
                  >
                    <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" aria-hidden="true">
                      <path d="M6 6l12 12M18 6 6 18" />
                    </svg>
                  </button>
                )}
              </span>
            </div>
          )}

          <textarea
            id="benchmark-composer"
            name="benchmark_prompt"
            aria-label="Benchmark prompt"
            autoComplete="off"
            ref={textareaRef}
            value={text}
            disabled={disabled}
            onChange={(e) => {
              setText(e.target.value);
              if (designError) setDesignError("");
            }}
            onKeyDown={onKeyDown}
            rows={2}
            placeholder="Describe the benchmark you need"
            /* The composer card is the control the user perceives, so it owns
               the focus indicator (see focus-within on the wrapper). A second
               outline drawn around the inner textarea reads as an error box. */
            className="max-h-40 w-full resize-none bg-transparent px-4 pb-2 pt-3.5 text-[14px] text-[var(--ink)] outline-none focus:outline-none focus-visible:outline-none placeholder:text-[var(--ink-3)]"
          />

          <div className="flex flex-wrap items-center gap-x-1 gap-y-1 border-t border-[var(--line)] px-2.5 py-2">
            <div ref={menuRootRef} className="relative">
              {menuOpen && (
                <div
                  role="menu"
                  aria-label="Add a dataset"
                  className="pb-glass-float absolute bottom-full left-0 z-30 mb-2 w-80 rounded-[14px] p-1.5 shadow-[var(--shadow-lift)]"
                >
                  {library.records.length > 0 && (
                    <>
                      <p className="px-2.5 pb-1 pt-1.5 text-[11px] font-medium uppercase tracking-wide text-[var(--ink-3)]">
                        Use an existing dataset
                      </p>
                      <ul className="max-h-44 overflow-y-auto">
                        {library.records.map((record) => (
                          <li key={record.id}>
                            <button
                              type="button"
                              role="menuitem"
                              onClick={() => {
                                setMenuOpen(false);
                                onPickExisting?.(record);
                              }}
                              className="flex min-h-10 w-full items-center gap-2.5 rounded-[10px] px-2.5 text-left transition-colors duration-150 hover:bg-[var(--surface-2)] focus-visible:bg-[var(--surface-2)] focus-visible:outline-none"
                            >
                              <span className="min-w-0 flex-1">
                                <span className="block truncate text-[13px] text-[var(--ink)]">
                                  {record.title
                                    ? safeVisibleText(record.title)
                                    : DATASET_KIND_LABELS[record.kind] || "Dataset"}
                                </span>
                                <span className="pb-mono block truncate text-[10px] text-[var(--ink-3)]">
                                  {safeVisibleText(record.id)}
                                </span>
                              </span>
                              <span className="shrink-0 text-[11px] text-[var(--ink-3)]">
                                {record.image_count} images
                              </span>
                            </button>
                          </li>
                        ))}
                      </ul>
                      <div className="mx-1 my-1.5 border-t border-[var(--line)]" aria-hidden="true" />
                    </>
                  )}
                  {library.loading && (
                    <p className="px-2.5 py-1.5 text-[12px] text-[var(--ink-3)]">Loading datasets…</p>
                  )}
                  {library.error && (
                    <p className="px-2.5 py-1.5 text-[12px] text-[var(--danger)]" role="alert">
                      {safeVisibleText(library.error)}
                    </p>
                  )}
                  <button
                    type="button"
                    role="menuitem"
                    disabled={uploading || provenanceLocked}
                    onClick={() => {
                      setMenuOpen(false);
                      doSyntheticUpload();
                    }}
                    className="flex min-h-10 w-full items-center gap-2.5 rounded-[10px] px-2.5 text-left text-[13px] text-[var(--ink-2)] transition-colors duration-150 hover:bg-[var(--surface-2)] hover:text-[var(--ink)] focus-visible:bg-[var(--surface-2)] focus-visible:outline-none disabled:cursor-not-allowed disabled:text-[var(--ink-3)]"
                  >
                    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                      <path d="M12 3v3" /><path d="M12 18v3" /><path d="M3 12h3" /><path d="M18 12h3" />
                      <path d="m5.6 5.6 2.2 2.2" /><path d="m16.2 16.2 2.2 2.2" />
                      <path d="m18.4 5.6-2.2 2.2" /><path d="m7.8 16.2-2.2 2.2" />
                    </svg>
                    {uploading ? "Preparing sample…" : "Generate the sample dataset"}
                  </button>
                  <button
                    type="button"
                    role="menuitem"
                    disabled={designing || provenanceLocked}
                    onClick={() => {
                      setMenuOpen(false);
                      doDesign();
                    }}
                    className="flex min-h-10 w-full items-center gap-2.5 rounded-[10px] px-2.5 text-left text-[13px] text-[var(--ink-2)] transition-colors duration-150 hover:bg-[var(--surface-2)] hover:text-[var(--ink)] focus-visible:bg-[var(--surface-2)] focus-visible:outline-none disabled:cursor-not-allowed disabled:text-[var(--ink-3)]"
                  >
                    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                      <path d="M12 3l1.8 4.6L18 9l-4.2 1.4L12 15l-1.8-4.6L6 9l4.2-1.4z" />
                      <path d="M18.5 15.5l.9 2.1 2.1.9-2.1.9-.9 2.1-.9-2.1-2.1-.9 2.1-.9z" />
                    </svg>
                    <span className="min-w-0 flex-1">
                      Design one with AI
                      <span className="block text-[11px] text-[var(--ink-3)]">
                        Built from the description you typed above
                      </span>
                    </span>
                  </button>
                  <button
                    type="button"
                    role="menuitem"
                    disabled={provenanceLocked}
                    onClick={() => {
                      setMenuOpen(false);
                      imagesRef.current?.click();
                    }}
                    className="flex min-h-10 w-full items-center gap-2.5 rounded-[10px] px-2.5 text-left text-[13px] text-[var(--ink-2)] transition-colors duration-150 hover:bg-[var(--surface-2)] hover:text-[var(--ink)] focus-visible:bg-[var(--surface-2)] focus-visible:outline-none disabled:cursor-not-allowed disabled:text-[var(--ink-3)]"
                  >
                    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                      <path d="M12 16V4" /><path d="m6 10 6-6 6 6" /><path d="M4 20h16" />
                    </svg>
                    Upload images + ground truth CSV
                  </button>
                </div>
              )}
              <button
                type="button"
                aria-haspopup="menu"
                aria-expanded={menuOpen}
                onClick={() => setMenuOpen((value) => !value)}
                disabled={provenanceLocked}
                title="Bring your own labelled data to score against"
                className={`${attachment} ${menuOpen ? "bg-[var(--surface-2)] text-[var(--ink)]" : ""}`}
              >
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                  <ellipse cx="12" cy="5.5" rx="7" ry="2.5" />
                  <path d="M5 5.5v13c0 1.4 3.1 2.5 7 2.5s7-1.1 7-2.5v-13" />
                  <path d="M5 12c0 1.4 3.1 2.5 7 2.5s7-1.1 7-2.5" />
                </svg>
                {designing ? "Designing dataset…" : dataset ? "Replace dataset" : "Add dataset"}
              </button>
            </div>

            {designError && (
              <span className="ml-1 text-[12px] text-[var(--danger)]" role="alert">
                {safeVisibleText(designError)}
              </span>
            )}

            {/* Data is an option, never a precondition. Some questions are
                settled by comparing what tools are, and the ones that need
                measuring build their own examples at run start — so this says
                what attaching buys, rather than asking for something first. */}
            {!dataset && (
              <span className="ml-1 text-[12px] text-[var(--ink-3)]">
                Optional — attach your own labelled data to score against, or let the run build its own.
              </span>
            )}

            <span className="ml-auto hidden items-center gap-1 text-[11px] text-[var(--ink-3)] sm:inline-flex" aria-hidden="true">
              <kbd className="rounded-[5px] border border-[var(--line)] bg-[var(--surface-2)] px-1 py-0.5 font-[inherit] text-[10px] leading-none">
                Enter
              </kbd>
              to send
            </span>
            <button
              type="button"
              onClick={send}
              disabled={disabled || !text.trim()}
              aria-label="Send"
              title="Send"
              className="ml-2 flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-[var(--ink)] text-[var(--surface)] transition-colors duration-150 hover:bg-[var(--btn-primary-hover)] focus-visible:outline-none focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[var(--accent)] disabled:cursor-not-allowed disabled:bg-[var(--surface-2)] disabled:text-[var(--ink-2)]"
            >
              <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                <path d="M12 19V5" />
                <path d="m5 12 7-7 7 7" />
              </svg>
            </button>
          </div>
        </div>

        <input
          ref={imagesRef}
          name="benchmark_images"
          aria-label="Benchmark images"
          type="file"
          accept="image/*"
          multiple
          disabled={provenanceLocked}
          hidden
          onChange={(e) => {
            pending.current.images = Array.from(e.target.files || []);
            tryUploadFiles();
          }}
        />
        <input
          ref={gtRef}
          name="benchmark_ground_truth"
          aria-label="Benchmark ground truth CSV"
          type="file"
          accept=".csv"
          disabled={provenanceLocked}
          hidden
          onChange={(e) => {
            pending.current.groundTruth = e.target.files?.[0] || null;
            tryUploadFiles();
          }}
        />
      </div>
    </div>
  );
}
