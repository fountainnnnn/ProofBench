import { useRef, useState } from "react";

const chip =
  "pb-pill pb-hover-lift rounded-full px-3 py-1 text-[12px] transition-colors disabled:opacity-50";

export default function Composer({ onSend, onUpload, dataset, disabled, mode, onModeChange }) {
  const [text, setText] = useState("");
  const [dragging, setDragging] = useState(false);
  const [uploading, setUploading] = useState(false);
  const imagesRef = useRef(null);
  const gtRef = useRef(null);
  const pending = useRef({ images: [], groundTruth: null });

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
    const files = Array.from(e.dataTransfer.files || []);
    const imgs = files.filter((f) => f.type.startsWith("image/"));
    const csv = files.find((f) => f.name.toLowerCase().endsWith(".csv"));
    if (imgs.length) pending.current.images = imgs;
    if (csv) pending.current.groundTruth = csv;
    tryUploadFiles();
  };

  return (
    <div className="pb-card pb-hover-lift border-t border-[var(--border)] bg-[var(--surface)] px-6 py-4">
      <div className="mx-auto max-w-3xl">
        <div className="mb-2 flex flex-wrap items-center gap-2">
          <div className="inline-flex rounded-md border border-[var(--border)] bg-[var(--surface-2)] p-0.5" role="group" aria-label="Execution mode">
            {[
              ["demo", "Demo"],
              ["real", "Real"],
            ].map(([value, label]) => (
              <button
                key={value}
                type="button"
                aria-pressed={mode === value}
                onClick={() => onModeChange(value)}
                className={`h-7 rounded px-2.5 text-[12px] font-medium transition-colors ${
                  mode === value
                    ? "pb-pill bg-[var(--surface)] text-[var(--accent)] shadow-[0_1px_2px_rgb(0_0_0_/_0.06)]"
                    : "text-[var(--text-3)] hover:text-[var(--text)]"
                }`}
              >
                {label}
              </button>
            ))}
          </div>
          <span className="text-[12px] text-[var(--text-3)]">
            {mode === "demo" ? "Instant, deterministic results" : "Live providers and measured results"}
          </span>
          <button
            onClick={() => imagesRef.current?.click()}
            className={`${chip} border-[var(--border)] bg-[var(--surface)] text-[var(--text-2)] hover:border-[var(--border-strong)] hover:text-[var(--text)]`}
          >
            Attach images
          </button>
          <button
            onClick={() => gtRef.current?.click()}
            className={`${chip} border-[var(--border)] bg-[var(--surface)] text-[var(--text-2)] hover:border-[var(--border-strong)] hover:text-[var(--text)]`}
          >
            Attach ground_truth.csv
          </button>
          <button
            onClick={doSyntheticUpload}
            disabled={uploading}
            className={`${chip} border-transparent bg-[var(--accent-soft)] text-[var(--accent)] hover:border-[var(--accent)]`}
          >
            {uploading ? "Preparing..." : "Use synthetic demo set"}
          </button>
          {dataset && (
            <span className="rounded-full border border-transparent bg-[color-mix(in_oklab,var(--ok)_10%,transparent)] px-3 py-1 text-[12px] text-[var(--ok)]">
              Dataset ready: {dataset.dataset_id}
            </span>
          )}
        </div>

        <div
          onDragOver={(e) => {
            e.preventDefault();
            setDragging(true);
          }}
          onDragLeave={() => setDragging(false)}
          onDrop={onDrop}
          className={`pb-card pb-hover-lift flex items-end gap-2 rounded-[12px] border bg-[var(--surface)] p-2 transition-all focus-within:ring-2 focus-within:ring-[color-mix(in_oklab,var(--accent)_40%,transparent)] ${
            dragging ? "border-[var(--accent)]" : "border-[var(--border-strong)]"
          }`}
        >
          <textarea
            value={text}
            onChange={(e) => setText(e.target.value)}
            onKeyDown={onKeyDown}
            rows={1}
            placeholder={mode === "demo"
              ? "Describe the benchmark, e.g. compare Tesseract vs EasyOCR on invoices"
              : "Describe the company need and tools to compare, e.g. assess Slack vs Teams integrations"}
            className="max-h-40 flex-1 resize-none bg-transparent px-2 py-1.5 text-sm text-[var(--text)] outline-none placeholder:text-[var(--text-3)]"
          />
          <button
            onClick={send}
            disabled={disabled || !text.trim()}
            className="pb-hover-lift h-9 rounded-md bg-[var(--accent)] px-4 text-[13px] font-medium text-[var(--surface)] transition-colors hover:bg-[var(--accent-hover)] disabled:opacity-40"
          >
            Send
          </button>
        </div>

        <input
          ref={imagesRef}
          type="file"
          accept="image/*"
          multiple
          hidden
          onChange={(e) => {
            pending.current.images = Array.from(e.target.files || []);
            tryUploadFiles();
          }}
        />
        <input
          ref={gtRef}
          type="file"
          accept=".csv"
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
