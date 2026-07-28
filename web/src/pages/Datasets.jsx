import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { deleteDataset, listDatasets, uploadDataset } from "../api.js";
import { safeVisibleText } from "../displaySafety.js";
import { relativeTime } from "../relativeTime.js";
import {
  BTN_DANGER,
  BTN_PRIMARY,
  BTN_SECONDARY,
  Eyebrow,
  InlineError,
  PAGE_HEADER,
  PAGE_TITLE,
  PANEL,
  Skeleton,
} from "../components/ui.jsx";
import HeaderActions from "../components/HeaderActions.jsx";

const IMAGE_RE = /\.(png|jpe?g|webp|tiff?|bmp|gif)$/i;
const isImage = (f) => f.type.startsWith("image/") || IMAGE_RE.test(f.name);
const isCsv = (f) => f.type === "text/csv" || /\.csv$/i.test(f.name);

// Compact "Use dataset" affordance: the secondary weight at row density.
const USE_BTN = `${BTN_SECONDARY.replace("min-h-10", "min-h-8")} h-8`;
// A solid, theme-aware light gray for the upload panel's recessed controls.
// Mixing the two neutral surfaces keeps it quieter than --surface-2 without
// inheriting any of the surrounding glass gradient.
const UPLOAD_BOX =
  "bg-[color-mix(in_oklab,var(--surface-2)_38%,var(--surface))] transition-colors duration-150 ease-out-quart hover:bg-[color-mix(in_oklab,var(--surface-2)_58%,var(--surface))]";

function DatasetTime({ value }) {
  const d = new Date(value);
  const valid = value && !Number.isNaN(d.getTime());
  return (
    <time
      className="pb-mono"
      dateTime={valid ? value : undefined}
      title={valid ? d.toLocaleString() : undefined}
    >
      {relativeTime(value)}
    </time>
  );
}

function DatasetRow({ record, highlighted, onUse, confirming, onAskDelete, onCancelDelete, onDelete, deleting }) {
  const synthetic = record.kind === "synthetic";
  return (
    <li
      className={`group flex min-h-14 flex-col gap-2 px-4 py-3 sm:flex-row sm:items-center sm:gap-4 ${
        highlighted ? "bg-[var(--surface-2)]" : ""
      }`}
    >
      {/* A dataset is an object: icon tile, human name first, machine id second.
          The tile is the brand accent, not a status colour — green here read as
          a success signal a dataset row does not carry. Sample vs uploaded is
          said by the heading, so the icon need not colour-code it. */}
      <span
        aria-hidden="true"
        className="hidden h-9 w-9 shrink-0 items-center justify-center rounded-[10px] bg-[var(--accent-tint)] text-[var(--accent)] sm:flex"
      >
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round">
          <ellipse cx="12" cy="5.5" rx="7" ry="2.5" />
          <path d="M5 5.5v13c0 1.4 3.1 2.5 7 2.5s7-1.1 7-2.5v-13" />
          <path d="M5 12c0 1.4 3.1 2.5 7 2.5s7-1.1 7-2.5" />
        </svg>
      </span>

      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-baseline gap-x-2 gap-y-0.5">
          <h3 className="text-[13px] font-medium text-[var(--ink)]">
            {synthetic ? "Sample labelled dataset" : "Uploaded dataset"}
          </h3>
          {record.image_count ? (
            <span className="text-[12px] text-[var(--ink-2)]">{record.image_count} images</span>
          ) : null}
          {highlighted && (
            <span className="text-[12px] font-medium text-[var(--ok)]">Ready to use</span>
          )}
        </div>
        <p className="mt-0.5 flex flex-wrap items-baseline gap-x-2 text-[12px] text-[var(--ink-2)]">
          <span className="pb-mono pb-contain text-[11px] text-[var(--ink-3)]">
            {safeVisibleText(record.id)}
          </span>
          <span>
            created <DatasetTime value={record.created_at} />
          </span>
        </p>
      </div>

      <div className="flex shrink-0 flex-wrap items-center gap-2">
        <button type="button" onClick={() => onUse(record)} className={USE_BTN}>
          Use in benchmark
        </button>
        {!synthetic &&
          (confirming ? (
            <span
              className="flex items-center gap-2"
              role="group"
              aria-label={`Confirm deletion of dataset ${safeVisibleText(record.id)}`}
            >
              <button
                type="button"
                onClick={() => onDelete(record)}
                disabled={deleting}
                className={BTN_DANGER}
              >
                {deleting ? "Deleting..." : "Confirm delete"}
              </button>
              <button type="button" onClick={onCancelDelete} disabled={deleting} className={BTN_SECONDARY}>
                Cancel
              </button>
            </span>
          ) : (
            <button
              type="button"
              onClick={() => onAskDelete(record)}
              className="min-h-8 rounded-full px-3 text-[12px] font-medium text-[var(--ink-3)] transition-all duration-150 hover:bg-[var(--danger-tint)] hover:text-[var(--danger)] sm:opacity-0 sm:group-focus-within:opacity-100 sm:group-hover:opacity-100"
              aria-label={`Delete dataset ${safeVisibleText(record.id)}`}
            >
              Delete
            </button>
          ))}
      </div>
    </li>
  );
}

export default function Datasets() {
  const navigate = useNavigate();
  const [records, setRecords] = useState([]);
  const [library, setLibrary] = useState({ loading: true, error: null, deleting: null });
  const [syn, setSyn] = useState({ busy: false, id: null, error: null });
  const [up, setUp] = useState({ busy: false, id: null, error: null });
  const [images, setImages] = useState([]);
  const [csv, setCsv] = useState(null);
  const [dragOver, setDragOver] = useState(false);
  const [confirmingDelete, setConfirmingDelete] = useState(null);
  const imgInputRef = useRef(null);
  const csvInputRef = useRef(null);

  const refreshRecords = async () => {
    setLibrary((current) => ({ ...current, loading: true, error: null }));
    try {
      setRecords(await listDatasets());
      setLibrary((current) => ({ ...current, loading: false }));
    } catch (error) {
      setLibrary((current) => ({ ...current, loading: false, error: error.message }));
    }
  };

  useEffect(() => { refreshRecords(); }, []);

  const useRecord = (record) => {
    navigate(`/app/benchmark?dataset=${encodeURIComponent(record.id)}`);
  };

  const removeRecord = async (record) => {
    setLibrary((current) => ({ ...current, deleting: record.id, error: null }));
    try {
      await deleteDataset(record.id);
      setRecords((current) => current.filter((item) => item.id !== record.id));
      setConfirmingDelete(null);
      setLibrary((current) => ({ ...current, deleting: null }));
    } catch (error) {
      setLibrary((current) => ({ ...current, deleting: null, error: error.message }));
    }
  };

  const addFiles = (fileList) => {
    const files = Array.from(fileList || []);
    const imgs = files.filter(isImage);
    const csvs = files.filter(isCsv);
    if (imgs.length) setImages((prev) => [...prev, ...imgs]);
    if (csvs.length) setCsv(csvs[csvs.length - 1]);
  };

  const onGenerate = async () => {
    setSyn({ busy: true, id: null, error: null });
    try {
      const res = await uploadDataset({ useSynthetic: true });
      setSyn({ busy: false, id: res.dataset_id, error: null });
      await refreshRecords();
    } catch (e) {
      setSyn({ busy: false, id: null, error: e.message });
    }
  };

  const onUpload = async () => {
    if (images.length === 0) {
      setUp({ busy: false, id: null, error: "Add at least one invoice image." });
      return;
    }
    if (!csv) {
      setUp({ busy: false, id: null, error: "Attach one CSV named ground_truth.csv." });
      return;
    }
    setUp({ busy: true, id: null, error: null });
    try {
      const res = await uploadDataset({ images, groundTruth: csv });
      setUp({ busy: false, id: res.dataset_id, error: null });
      await refreshRecords();
      setImages([]);
      setCsv(null);
    } catch (e) {
      setUp({ busy: false, id: null, error: e.message });
    }
  };

  const samples = records.filter((record) => record.kind === "synthetic");
  const owned = records.filter((record) => record.kind !== "synthetic");
  const readyId = up.id || syn.id;

  const rowProps = (record) => ({
    record,
    highlighted: record.id === readyId,
    onUse: useRecord,
    confirming: confirmingDelete === record.id,
    onAskDelete: (item) => setConfirmingDelete(item.id),
    onCancelDelete: () => setConfirmingDelete(null),
    onDelete: removeRecord,
    deleting: library.deleting === record.id,
  });

  return (
    <div className="flex min-h-full flex-col">
      <header className={`${PAGE_HEADER} px-4 sm:px-8`}>
        <div className="mx-auto flex w-full max-w-canvas items-start justify-between gap-x-6 pb-3 pt-3.5">
          <div className="min-w-0">
            <span className="pb-eyebrow-glow">Data</span>
            <h1 className={`${PAGE_TITLE} mt-1`}>Datasets</h1>
            <p className="mt-0.5 text-[13px] text-[var(--ink-2)]">
              A benchmark scores output against labelled data. Add the data here, then select it when
              you start a run.
            </p>
          </div>
          <HeaderActions />
        </div>
      </header>

      <div className="mx-auto grid w-full max-w-canvas grid-cols-1 gap-x-[24px] gap-y-4 px-4 pb-12 pt-8 sm:px-8 lg:grid-cols-12">
        {/* Every card on this page titles itself from inside, so this column
            carries no heading of its own: an "Add data" label outside the cards
            put one column's title outside its container and the other's inside. */}
        <section className="lg:contents" aria-label="Add data">
          {/* Fastest path first: a new operator can have scoreable data in one
              click before ever preparing an upload. */}
          <div className={`${PANEL} flex h-full flex-col p-5 lg:col-span-5 lg:col-start-1 lg:row-start-1`}>
            <div className="mb-3 flex items-center gap-3">
              <span
                aria-hidden="true"
                className="flex h-9 w-9 shrink-0 items-center justify-center rounded-[10px] bg-[var(--accent-tint)] text-[var(--accent)]"
              >
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round">
                  <path d="M12 3v3" />
                  <path d="M12 18v3" />
                  <path d="M3 12h3" />
                  <path d="M18 12h3" />
                  <path d="m5.6 5.6 2.2 2.2" />
                  <path d="m16.2 16.2 2.2 2.2" />
                  <path d="m18.4 5.6-2.2 2.2" />
                  <path d="m7.8 16.2-2.2 2.2" />
                </svg>
              </span>
              <h3 className="text-[14px] font-semibold text-[var(--ink)]">Start with the sample</h3>
            </div>
            <p className="max-w-[52ch] text-[13px] leading-relaxed text-[var(--ink-2)]">
              15 synthetic invoice images with known ground truth. The images are synthetic;
              every metric measured against them is real.
            </p>
            <div className="mt-auto flex flex-wrap items-center gap-3 pt-4">
              <button onClick={onGenerate} disabled={syn.busy} className={BTN_SECONDARY}>
                {syn.busy ? "Generating..." : "Generate sample dataset"}
              </button>
              {syn.id && (
                <button
                  type="button"
                  onClick={() =>
                    useRecord(records.find((record) => record.id === syn.id) || { id: syn.id, kind: "synthetic" })
                  }
                  className={BTN_PRIMARY}
                >
                  Use in benchmark
                </button>
              )}
            </div>
            {syn.error && (
              <p className="mt-4 text-[12px] text-[var(--danger)]" role="alert">
                Generation failed: {syn.error}
              </p>
            )}
          </div>

          <div className={`${PANEL} p-5 lg:col-span-5 lg:col-start-1 lg:row-start-2`}>
            <div className="mb-3 flex items-center gap-3">
              <span
                aria-hidden="true"
                className="flex h-9 w-9 shrink-0 items-center justify-center rounded-[10px] bg-[var(--accent-tint)] text-[var(--accent)]"
              >
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round">
                  <path d="M12 16V4" />
                  <path d="m6 10 6-6 6 6" />
                  <path d="M4 20h16" />
                </svg>
              </span>
              <h3 className="text-[14px] font-semibold text-[var(--ink)]">Upload your own</h3>
            </div>
            <div
              role="button"
              tabIndex={0}
              aria-label="Add invoice images"
              onClick={() => imgInputRef.current?.click()}
              onKeyDown={(e) => {
                if (e.key === "Enter" || e.key === " ") {
                  e.preventDefault();
                  imgInputRef.current?.click();
                }
              }}
              onDragOver={(e) => {
                e.preventDefault();
                setDragOver(true);
              }}
              onDragLeave={() => setDragOver(false)}
              onDrop={(e) => {
                e.preventDefault();
                setDragOver(false);
                addFiles(e.dataTransfer.files);
              }}
              className={`flex cursor-pointer flex-col items-center justify-center gap-1 rounded-[12px] p-6 text-center transition-colors duration-150 ease-out-quart ${
                dragOver
                  ? "bg-[var(--accent-tint)]"
                  : UPLOAD_BOX
              }`}
            >
              <p className="text-[13px] font-medium text-[var(--ink)]">
                Drop invoice images here, or click to browse
              </p>
              <p className="max-w-[42ch] text-[12px] text-[var(--ink-2)]">
                PNG or JPG, multiple files accepted. A dropped CSV is used as ground truth.
              </p>
            </div>

            {/* The two requirements as a visible checklist: state icon, name,
                and the action to satisfy it, per row. */}
            <ul className="mt-4 flex flex-col gap-1.5">
              <li className={`flex min-h-10 flex-wrap items-center gap-x-2.5 gap-y-1 rounded-[12px] px-3 py-1.5 ${UPLOAD_BOX}`}>
                <span
                  aria-hidden="true"
                  className={`flex h-5 w-5 shrink-0 items-center justify-center rounded-full ${
                    images.length > 0
                      ? "bg-[var(--ok)] text-[var(--surface)]"
                      : "border border-dashed border-[var(--ink-3)] text-transparent"
                  }`}
                >
                  <svg width="10" height="10" viewBox="0 0 12 12" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
                    <path d="M2.5 6.5 5 9l4.5-6" />
                  </svg>
                </span>
                <span className="text-[13px] font-medium text-[var(--ink)]">Invoice images</span>
                <span className="text-[12px] text-[var(--ink-2)]">
                  {images.length === 0
                    ? "none yet"
                    : `${images.length} file${images.length === 1 ? "" : "s"}`}
                </span>
                <span className="ml-auto flex items-center gap-2">
                  {images.length > 0 && (
                    <button
                      type="button"
                      onClick={() => setImages([])}
                      className="inline-flex min-h-9 items-center rounded-[8px] px-2 text-[12px] font-medium text-[var(--ink-3)] transition-colors duration-150 hover:bg-[var(--surface)] hover:text-[var(--ink)]"
                    >
                      Clear
                    </button>
                  )}
                  <button
                    type="button"
                    onClick={() => imgInputRef.current?.click()}
                    className="inline-flex min-h-9 items-center rounded-[8px] px-2 text-[12px] font-medium text-[var(--accent)] transition-colors duration-150 hover:bg-[var(--surface)] hover:text-[var(--accent-ink)]"
                  >
                    Browse
                  </button>
                </span>
              </li>
              <li className={`flex min-h-10 flex-wrap items-center gap-x-2.5 gap-y-1 rounded-[12px] px-3 py-1.5 ${UPLOAD_BOX}`}>
                <span
                  aria-hidden="true"
                  className={`flex h-5 w-5 shrink-0 items-center justify-center rounded-full ${
                    csv
                      ? "bg-[var(--ok)] text-[var(--surface)]"
                      : "border border-dashed border-[var(--ink-3)] text-transparent"
                  }`}
                >
                  <svg width="10" height="10" viewBox="0 0 12 12" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
                    <path d="M2.5 6.5 5 9l4.5-6" />
                  </svg>
                </span>
                <span className="text-[13px] font-medium text-[var(--ink)]">Ground truth</span>
                <span className={`pb-contain text-[12px] ${csv ? "pb-mono text-[var(--ink-2)]" : "text-[var(--ink-2)]"}`}>
                  {csv ? csv.name : "the labelled answers, as CSV"}
                </span>
                <button
                  type="button"
                  onClick={() => csvInputRef.current?.click()}
                  className="ml-auto inline-flex min-h-9 items-center rounded-[8px] px-2 text-[12px] font-medium text-[var(--accent)] transition-colors duration-150 hover:bg-[var(--surface)] hover:text-[var(--accent-ink)]"
                >
                  Browse
                </button>
              </li>
            </ul>

            {images.length > 0 && (
              <ul className={`mt-4 max-h-28 overflow-y-auto rounded-[12px] px-3 py-2 ${UPLOAD_BOX}`}>
                {images.map((f, i) => (
                  <li
                    key={`${f.name}-${i}`}
                    className="pb-mono pb-long-list-item truncate text-[12px] leading-5 text-[var(--ink-2)]"
                  >
                    {f.name}
                  </li>
                ))}
              </ul>
            )}

            <div className="mt-4 flex flex-wrap items-center gap-3">
              <button onClick={onUpload} disabled={up.busy} className={BTN_PRIMARY}>
                {up.busy ? "Uploading..." : "Upload dataset"}
              </button>
              {up.id && (
                <span className="text-[13px] text-[var(--ink-2)]">
                  Created <span className="pb-mono pb-contain">{safeVisibleText(up.id)}</span>
                </span>
              )}
            </div>
            {up.error && (
              <p className="mt-4 text-[12px] text-[var(--danger)]" role="alert">
                Upload failed: {up.error}
              </p>
            )}

            <input
              ref={imgInputRef}
              type="file"
              accept="image/*"
              multiple
              className="hidden"
              onChange={(e) => {
                addFiles(e.target.files);
                e.target.value = "";
              }}
            />
            <input
              ref={csvInputRef}
              type="file"
              accept=".csv,text/csv"
              className="hidden"
              onChange={(e) => {
                const f = e.target.files?.[0];
                if (f) setCsv(f);
                e.target.value = "";
              }}
            />

          </div>
        </section>

        <section className="lg:contents" aria-labelledby="library-heading">
          <div className="flex h-full flex-col lg:col-span-7 lg:col-start-6 lg:row-start-1">
            {library.error && (
              <div className="mb-3">
                <InlineError onRetry={refreshRecords}>{library.error}</InlineError>
              </div>
            )}

            <div className={`${PANEL} flex-1 overflow-hidden`}>
            <div className="px-4 pb-1 pt-4">
              <h2 id="library-heading" className="text-[16px] font-semibold text-[var(--ink)]">
                Library
              </h2>
            </div>

            {library.loading ? (
              <div className="flex flex-col gap-4 px-4 pb-4" role="status" aria-label="Loading datasets">
                {[...Array(3)].map((_, i) => (
                  <div key={i} className="flex items-center gap-4">
                    <Skeleton className="h-4 w-40" />
                    <Skeleton className="ml-auto h-8 w-28" />
                  </div>
                ))}
              </div>
            ) : records.length === 0 ? (
              <div className="px-4 pb-8">
                <p className="text-[14px] text-[var(--ink)]">No datasets yet.</p>
                <p className="mt-1 max-w-[60ch] text-[13px] text-[var(--ink-2)]">
                  Generate the sample labelled dataset, or upload your own images with a ground
                  truth CSV. A dataset is required before a benchmark can be scored.
                </p>
              </div>
            ) : (
              <>
                {/* Samples sit directly under the panel's own heading — no second
                    eyebrow, which would only repeat the group-label device. */}
                {samples.length > 0 && (
                  <ul
                    aria-labelledby="library-heading"
                    className="mt-2 divide-y divide-[var(--line)] border-t border-[var(--line)]"
                  >
                    {samples.map((record, i) => (
                      <DatasetRow key={`${record.id}-${i}`} {...rowProps(record)} />
                    ))}
                  </ul>
                )}

                <div className="px-4 pb-1 pt-4">
                  <Eyebrow as="h3" id="owned-heading">
                    Your datasets
                  </Eyebrow>
                </div>
                {owned.length === 0 ? (
                  <p className="px-4 pb-6 text-[13px] text-[var(--ink-2)]">
                    Nothing uploaded yet. Uploaded datasets appear here and can be deleted.
                  </p>
                ) : (
                  <ul
                    aria-labelledby="owned-heading"
                    className="divide-y divide-[var(--line)] border-t border-[var(--line)]"
                  >
                    {owned.map((record, i) => (
                      <DatasetRow key={`${record.id}-${i}`} {...rowProps(record)} />
                    ))}
                  </ul>
                )}
              </>
            )}
            </div>
          </div>

          {/* Quiet explainer so the column does not end abruptly below the
              library card. Plain text, not a second panel. */}
          <p className="max-w-[62ch] px-1 text-[12px] leading-relaxed text-[var(--ink-3)] lg:col-span-7 lg:col-start-6 lg:row-start-2">
            A dataset is a folder of invoice images plus a{" "}
            <span className="pb-mono">ground_truth.csv</span> holding the correct answer for each
            one. That CSV needs the columns{" "}
            <span className="pb-mono">doc_id</span>, <span className="pb-mono">invoice_number</span>,{" "}
            <span className="pb-mono">date</span>, <span className="pb-mono">vendor</span>, and{" "}
            <span className="pb-mono">total</span>.
          </p>
        </section>
      </div>
    </div>
  );
}
