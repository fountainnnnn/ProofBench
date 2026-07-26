import { useEffect, useRef, useState } from "react";
import { safeVisibleText } from "../displaySafety.js";
import { BTN_PRIMARY } from "./ui.jsx";

const attachment =
  "inline-flex min-h-8 items-center gap-1.5 rounded-full px-2.5 text-[12px] font-medium text-[var(--ink-2)] " +
  "transition-colors duration-150 hover:bg-[var(--surface-2)] hover:text-[var(--ink)] " +
  "disabled:cursor-not-allowed disabled:bg-transparent disabled:text-[var(--ink-3)]";

export default function Composer({ onSend, onUpload, dataset, disabled, provenanceLocked, injectText = "" }) {
  const [text, setText] = useState("");
  const [dragging, setDragging] = useState(false);
  const [uploading, setUploading] = useState(false);
  const imagesRef = useRef(null);
  const gtRef = useRef(null);
  const textareaRef = useRef(null);
  const pending = useRef({ images: [], groundTruth: null });

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
      <div className="mx-auto w-full max-w-[840px]">
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
                    {dataset.kind === "synthetic" ? "Sample labelled dataset" : "Labelled dataset"}
                  </span>
                  {(dataset.dataset_id || dataset.id) && (
                    <span className="pb-mono block truncate text-[10px] leading-3 text-[var(--ink-3)]">
                      {safeVisibleText(dataset.dataset_id || dataset.id)}
                    </span>
                  )}
                </span>
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
            onChange={(e) => setText(e.target.value)}
            onKeyDown={onKeyDown}
            rows={2}
            placeholder="Describe the benchmark you need"
            /* The composer card is the control the user perceives, so it owns
               the focus indicator (see focus-within on the wrapper). A second
               outline drawn around the inner textarea reads as an error box. */
            className="max-h-40 w-full resize-none bg-transparent px-4 pb-2 pt-3.5 text-[14px] text-[var(--ink)] outline-none focus:outline-none focus-visible:outline-none placeholder:text-[var(--ink-3)]"
          />

          <div className="flex flex-wrap items-center gap-x-1 gap-y-1 border-t border-[var(--line)] px-2.5 py-2">
            <button
              type="button"
              onClick={() => imagesRef.current?.click()}
              disabled={provenanceLocked}
              title="Attach invoice images (PNG or JPG)"
              className={attachment}
            >
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                <path d="m21.4 11.05-8.79 8.79a5.5 5.5 0 0 1-7.78-7.78l8.79-8.79a3.67 3.67 0 0 1 5.19 5.19l-8.8 8.79a1.83 1.83 0 0 1-2.59-2.6l8.12-8.11" />
              </svg>
              Images
            </button>
            <button
              type="button"
              onClick={() => gtRef.current?.click()}
              disabled={provenanceLocked}
              title="Attach the labelled answers as a CSV (ground_truth.csv)"
              className={attachment}
            >
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                <path d="M3 5h18v14H3z" />
                <path d="M3 10h18" />
                <path d="M9 10v9" />
              </svg>
              Ground truth
            </button>
            {/* A dataset is already attached: the sample-dataset shortcut would
                only satisfy an already-satisfied need. Images/Ground truth stay,
                since replacing the dataset is legitimate. */}
            {!dataset && (
              <button
                type="button"
                onClick={doSyntheticUpload}
                disabled={uploading || provenanceLocked}
                title="Generate 15 synthetic labelled invoices to benchmark against"
                className={attachment}
              >
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                  <path d="M12 3v3" />
                  <path d="M12 18v3" />
                  <path d="M3 12h3" />
                  <path d="M18 12h3" />
                  <path d="m5.6 5.6 2.2 2.2" />
                  <path d="m16.2 16.2 2.2 2.2" />
                  <path d="m18.4 5.6-2.2 2.2" />
                  <path d="m7.8 16.2-2.2 2.2" />
                </svg>
                {uploading ? "Preparing..." : "Sample dataset"}
              </button>
            )}

            {!dataset && (
              <span className="ml-1 text-[12px] text-[var(--ink-3)]">
                Attach a labelled dataset so the run can be scored.
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
