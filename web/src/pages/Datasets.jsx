import { useRef, useState } from "react";
import { uploadDataset } from "../api.js";

const LS_KEY = "pb_datasets";

const PANEL = "rounded-[14px] border border-[color:var(--border)] bg-[color:var(--surface)] shadow-card";
const CHIP =
  "pb-pill inline-flex max-w-full items-center gap-1 px-2 py-1 font-mono text-[12px] text-[color:var(--text)]";
const BTN =
  "pb-hover-lift inline-flex h-9 items-center justify-center gap-1.5 rounded-md px-3 text-[13px] font-medium transition-colors duration-150 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[color:color-mix(in_oklab,var(--accent)_40%,transparent)] disabled:cursor-not-allowed disabled:opacity-50";
const BTN_PRIMARY = `${BTN} bg-[color:var(--accent)] text-[color:oklch(0.99_0.002_264)] hover:bg-[color:var(--accent-hover)]`;
const BTN_SECONDARY = `${BTN} border border-[color:var(--border-strong)] bg-[color:var(--surface)] text-[color:var(--text)] hover:bg-[color:var(--surface-2)]`;

const IMAGE_RE = /\.(png|jpe?g|webp|tiff?|bmp|gif)$/i;
const isImage = (f) => f.type.startsWith("image/") || IMAGE_RE.test(f.name);
const isCsv = (f) => f.type === "text/csv" || /\.csv$/i.test(f.name);

function loadRecords() {
  try {
    const parsed = JSON.parse(localStorage.getItem(LS_KEY) || "[]");
    return Array.isArray(parsed) ? parsed : [];
  } catch {
    return [];
  }
}

function formatTime(value) {
  const d = new Date(value);
  if (!value || Number.isNaN(d.getTime())) return "unknown";
  return d.toLocaleString();
}

export default function Datasets() {
  const [records, setRecords] = useState(loadRecords);
  const [syn, setSyn] = useState({ busy: false, path: null, error: null });
  const [up, setUp] = useState({ busy: false, id: null, error: null });
  const [images, setImages] = useState([]);
  const [csv, setCsv] = useState(null);
  const [dragOver, setDragOver] = useState(false);
  const imgInputRef = useRef(null);
  const csvInputRef = useRef(null);

  const addRecord = (rec) => {
    setRecords((prev) => {
      const next = [rec, ...prev];
      try {
        localStorage.setItem(LS_KEY, JSON.stringify(next));
      } catch {
        /* storage full or blocked; keep in-memory list */
      }
      return next;
    });
  };

  const addFiles = (fileList) => {
    const files = Array.from(fileList || []);
    const imgs = files.filter(isImage);
    const csvs = files.filter(isCsv);
    if (imgs.length) setImages((prev) => [...prev, ...imgs]);
    if (csvs.length) setCsv(csvs[csvs.length - 1]);
  };

  const onGenerate = async () => {
    setSyn({ busy: true, path: null, error: null });
    try {
      const res = await uploadDataset({ useSynthetic: true });
      setSyn({ busy: false, path: res.path, error: null });
      addRecord({
        id: res.dataset_id,
        path: res.path,
        when: new Date().toISOString(),
      });
    } catch (e) {
      setSyn({ busy: false, path: null, error: e.message });
    }
  };

  const onUpload = async () => {
    if (images.length === 0) {
      setUp({
        busy: false,
        id: null,
        error: "Add at least one invoice image.",
      });
      return;
    }
    if (!csv) {
      setUp({
        busy: false,
        id: null,
        error: "Attach one CSV named ground_truth.csv.",
      });
      return;
    }
    setUp({ busy: true, id: null, error: null });
    try {
      const res = await uploadDataset({ images, groundTruth: csv });
      setUp({ busy: false, id: res.dataset_id, error: null });
      addRecord({
        id: res.dataset_id,
        path: res.path,
        when: new Date().toISOString(),
      });
      setImages([]);
      setCsv(null);
    } catch (e) {
      setUp({ busy: false, id: null, error: e.message });
    }
  };

  return (
    <div className="mx-auto w-full max-w-5xl px-8 py-8">
      <h1 className="text-[22px] font-semibold tracking-tight text-[color:var(--text)]">
        Datasets
      </h1>

      <div className="mt-8 flex flex-col gap-8">
        <section className={`${PANEL} p-6`}>
          <h2 className="text-[16px] font-semibold text-[color:var(--text)]">
            Synthetic demo set
          </h2>
          <p className="mt-1.5 max-w-[65ch] text-[13px] leading-relaxed text-[color:var(--text-2)]">
            Generates 15 synthetic invoice images with known ground truth,
            ready for an end-to-end benchmark run.
          </p>
          <div className="mt-4 flex flex-wrap items-center gap-3">
            <button
              onClick={onGenerate}
              disabled={syn.busy}
              className={BTN_PRIMARY}
            >
              {syn.busy ? "Generating..." : "Generate demo set"}
            </button>
            {syn.path && <code className={CHIP}>{syn.path}</code>}
          </div>
          {syn.error && (
            <p className="mt-3 text-[12px] text-[color:var(--err)]">
              Generation failed: {syn.error}
            </p>
          )}
        </section>

        <section className={`${PANEL} p-6`}>
          <h2 className="text-[16px] font-semibold text-[color:var(--text)]">
            Upload your own
          </h2>
          <p className="mt-1.5 max-w-[65ch] text-[13px] leading-relaxed text-[color:var(--text-2)]">
            Invoice images plus one CSV of labels, named ground_truth.csv.
          </p>

          <div
            role="button"
            tabIndex={0}
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
            className={`mt-4 flex cursor-pointer flex-col items-center justify-center gap-1.5 rounded-[12px] border border-dashed px-6 py-10 transition-all duration-150 ${
              dragOver
                ? "border-[color:var(--accent)] bg-[color:var(--accent-soft)]"
                : "border-[color:var(--border-strong)] bg-[color:var(--surface)] hover:bg-[color:var(--surface-2)]"
            }`}
          >
            <p className="text-[13px] font-medium text-[color:var(--text)]">
              Drop invoice images here, or click to browse
            </p>
            <p className="text-[12px] text-[color:var(--text-3)]">
              PNG or JPG, multiple files accepted. A dropped CSV is used as
              ground truth.
            </p>
          </div>

          <div className="mt-4 flex flex-wrap items-center gap-3">
            <span className="text-[12px] font-medium text-[color:var(--text-2)]">
              Ground truth CSV
            </span>
            <button
              onClick={() => csvInputRef.current?.click()}
              className={BTN_SECONDARY}
            >
              Choose file
            </button>
            {csv ? (
              <code className={CHIP}>{csv.name}</code>
            ) : (
              <span className="text-[12px] text-[color:var(--text-3)]">
                ground_truth.csv
              </span>
            )}
          </div>

          {images.length > 0 && (
            <div className="mt-4">
              <div className="flex items-center gap-3">
                <span className="text-[12px] font-medium text-[color:var(--text-2)]">
                  {images.length} image{images.length === 1 ? "" : "s"}{" "}
                  selected
                </span>
                <button
                  onClick={() => setImages([])}
                  className="text-[12px] font-medium text-[color:var(--text-2)] underline-offset-2 hover:text-[color:var(--text)] hover:underline"
                >
                  Clear
                </button>
              </div>
              <ul className="mt-2 max-h-28 overflow-y-auto rounded-md border border-[color:var(--border)] bg-[color:var(--surface-2)] px-3 py-2">
                {images.map((f, i) => (
                  <li
                    key={`${f.name}-${i}`}
                    className="truncate font-mono text-[12px] leading-5 text-[color:var(--text-2)]"
                  >
                    {f.name}
                  </li>
                ))}
              </ul>
            </div>
          )}

          <div className="mt-5 flex flex-wrap items-center gap-3">
            <button
              onClick={onUpload}
              disabled={up.busy}
              className={BTN_PRIMARY}
            >
              {up.busy ? "Uploading..." : "Upload dataset"}
            </button>
            {up.id && (
              <span className="text-[13px] text-[color:var(--text-2)]">
                Dataset created: <code className={CHIP}>{up.id}</code>
              </span>
            )}
          </div>
          {up.error && (
            <p className="mt-3 text-[12px] text-[color:var(--err)]">
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
        </section>

        <section>
          <h2 className="text-[16px] font-semibold text-[color:var(--text)]">
            Created datasets
          </h2>
          <div className={`mt-3 ${PANEL} overflow-hidden`}>
            {records.length === 0 ? (
              <p className="px-4 py-6 text-[13px] text-[color:var(--text-3)]">
                No datasets yet. Generate the demo set or upload your own
                above.
              </p>
            ) : (
              <table className="w-full border-collapse text-left text-[13px]">
                <thead>
                  <tr className="bg-[color:var(--surface-2)]">
                    <th className="px-4 py-2.5 text-[12px] font-semibold text-[color:var(--text-2)]">
                      Dataset ID
                    </th>
                    <th className="px-4 py-2.5 text-[12px] font-semibold text-[color:var(--text-2)]">
                      Path
                    </th>
                    <th className="px-4 py-2.5 text-[12px] font-semibold text-[color:var(--text-2)]">
                      Created
                    </th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-[color:var(--border)]">
                  {records.map((r, i) => (
                    <tr
                      key={`${r.id}-${i}`}
                      className="transition-colors duration-150 hover:bg-[color:color-mix(in_oklab,var(--accent-soft)_50%,transparent)]"
                    >
                      <td className="px-4 py-2.5 font-mono text-[12px] text-[color:var(--text)]">
                        {r.id}
                      </td>
                      <td
                        className="max-w-0 truncate px-4 py-2.5 font-mono text-[12px] text-[color:var(--text-2)]"
                        title={r.path}
                      >
                        {r.path}
                      </td>
                      <td className="whitespace-nowrap px-4 py-2.5 font-mono text-[12px] text-[color:var(--text-2)]">
                        {formatTime(r.when)}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>
        </section>
      </div>
    </div>
  );
}
