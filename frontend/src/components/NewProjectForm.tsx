import { ChevronDown, Film, Link2, Loader2, Upload, Wand2, X } from "lucide-react";
import { useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api, parseTime } from "../api";
import type { ClipLength, Options, Pace } from "../types";
import { Field, inputClass, ProgressBar, Segmented } from "./ui";

const LENGTHS: { value: ClipLength; label: string }[] = [
  { value: "under30", label: "< 30s" },
  { value: "under60", label: "< 60s" },
  { value: "under90", label: "< 90s" },
  { value: "under3min", label: "< 3m" },
];

const COUNTS = [0, 3, 5, 8, 10, 12, 15, 20];

const PACES: { value: Pace; label: string }[] = [
  { value: "off", label: "Off" },
  { value: "natural", label: "Natural" },
  { value: "fast", label: "Fast" },
];
const PACE_HINT: Record<Pace, string> = {
  off: "keeps every pause",
  natural: "cuts pauses over 0.6s and ums",
  fast: "cuts pauses over 0.3s and ums",
};

const FRAMINGS: { value: Options["reframe"]; label: string }[] = [
  { value: "face", label: "Follow speaker" },
  { value: "center", label: "Centre" },
];

const LANGUAGES: [string, string][] = [
  ["auto", "Auto-detect"], ["en", "English"], ["es", "Spanish"], ["pt", "Portuguese"], ["fr", "French"],
  ["de", "German"], ["it", "Italian"], ["nl", "Dutch"], ["ru", "Russian"], ["uk", "Ukrainian"],
  ["pl", "Polish"], ["tr", "Turkish"], ["ar", "Arabic"], ["hi", "Hindi"], ["id", "Indonesian"],
  ["ms", "Malay"], ["vi", "Vietnamese"], ["th", "Thai"], ["zh", "Chinese"], ["ja", "Japanese"], ["ko", "Korean"],
];

const STYLES = [
  { id: "karaoke", name: "Karaoke", preview: <><span>THIS IS </span><span className="text-[#FFE14D]">HOW</span></> },
  { id: "bold", name: "Bold Pop", preview: <span className="text-lg">CRAZY</span> },
  {
    id: "clean",
    name: "Clean Box",
    preview: <span className="rounded bg-black/60 px-1.5 py-0.5 font-sans text-[11px] font-bold normal-case [-webkit-text-stroke:0]">This is how</span>,
  },
  { id: "none", name: "No captions", preview: <span className="font-sans text-xs normal-case text-muted [-webkit-text-stroke:0]">—</span> },
];

const fmtBytes = (n: number) =>
  n > 1e9 ? `${(n / 1e9).toFixed(2)} GB` : n > 1e6 ? `${(n / 1e6).toFixed(1)} MB` : `${Math.round(n / 1e3)} KB`;

export default function NewProjectForm({ onCreated }: { onCreated?: () => void }) {
  const navigate = useNavigate();
  const fileInput = useRef<HTMLInputElement>(null);
  const [mode, setMode] = useState<"link" | "upload">("link");
  const [url, setUrl] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [dragging, setDragging] = useState(false);
  const [length, setLength] = useState<ClipLength>("under60");
  const [count, setCount] = useState(0);
  const [language, setLanguage] = useState("auto");
  const [template, setTemplate] = useState("karaoke");
  const [hookTitle, setHookTitle] = useState(true);
  const [pace, setPace] = useState<Pace>("natural");
  const [reframe, setReframe] = useState<Options["reframe"]>("face");
  const [showAdvanced, setShowAdvanced] = useState(false);
  const [startText, setStartText] = useState("");
  const [endText, setEndText] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [uploadPct, setUploadPct] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);

  const pickFile = (f: File | undefined) => {
    if (!f) return;
    setFile(f);
    setMode("upload");
    setError(null);
  };

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    const start = parseTime(startText);
    const end = parseTime(endText);
    if (Number.isNaN(start) || Number.isNaN(end)) return setError("Time range must look like 1:30 or 01:02:03.");
    if (start !== null && end !== null && end <= start) return setError("End time must be after start time.");
    if (mode === "link" && !/^https?:\/\//i.test(url.trim())) return setError("Paste a full video link (https://…).");
    if (mode === "upload" && !file) return setError("Choose a video file first.");

    const options: Options = {
      length,
      max_clips: count,
      language: language === "auto" ? null : language,
      caption_template: template,
      hook_title: hookTitle,
      pace,
      reframe,
      start,
      end,
    };
    setSubmitting(true);
    try {
      const project = await api.create(
        mode === "link" ? { url: url.trim() } : { file: file! },
        options,
        mode === "upload" ? setUploadPct : undefined,
      );
      onCreated?.();
      navigate(`/projects/${project.id}`);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setSubmitting(false);
      setUploadPct(null);
    }
  }

  return (
    <form onSubmit={submit} className="rounded-2xl border border-line bg-panel p-5 sm:p-6">
      {/* Source */}
      <div className="mb-4 flex gap-1 rounded-lg border border-line bg-bg p-1 sm:w-fit">
        {(["link", "upload"] as const).map((m) => (
          <button
            key={m}
            type="button"
            onClick={() => setMode(m)}
            className={`flex flex-1 items-center justify-center gap-2 rounded-md px-4 py-1.5 text-sm transition sm:flex-none ${
              mode === m ? "bg-panel-2 font-medium" : "text-muted hover:text-text"
            }`}
          >
            {m === "link" ? <Link2 className="size-4" /> : <Upload className="size-4" />}
            {m === "link" ? "Video link" : "Upload file"}
          </button>
        ))}
      </div>

      {mode === "link" ? (
        <input
          className={`${inputClass} py-3.5 text-base`}
          placeholder="Paste a YouTube, Twitch, Vimeo or Kick link"
          value={url}
          onChange={(e) => setUrl(e.target.value)}
          autoFocus
        />
      ) : (
        <div
          role="button"
          tabIndex={0}
          onClick={() => fileInput.current?.click()}
          onKeyDown={(e) => e.key === "Enter" && fileInput.current?.click()}
          onDragOver={(e) => {
            e.preventDefault();
            setDragging(true);
          }}
          onDragLeave={() => setDragging(false)}
          onDrop={(e) => {
            e.preventDefault();
            setDragging(false);
            pickFile(e.dataTransfer.files[0]);
          }}
          className={`flex cursor-pointer flex-col items-center justify-center gap-2 rounded-xl border-2 border-dashed px-4 py-8 text-center transition ${
            dragging ? "border-accent bg-accent/5" : "border-line hover:border-muted"
          }`}
        >
          {file ? (
            <div className="flex items-center gap-3">
              <Film className="size-5 text-accent" />
              <div className="text-left">
                <div className="max-w-[60vw] truncate text-sm font-medium sm:max-w-md">{file.name}</div>
                <div className="text-xs text-muted">{fmtBytes(file.size)}</div>
              </div>
              <button
                type="button"
                className="rounded p-1 text-muted hover:bg-panel-2 hover:text-text"
                onClick={(e) => {
                  e.stopPropagation();
                  setFile(null);
                }}
                aria-label="Remove file"
              >
                <X className="size-4" />
              </button>
            </div>
          ) : (
            <>
              <Upload className="size-6 text-muted" />
              <div className="text-sm">Drop a video here or click to browse</div>
              <div className="text-xs text-muted">MP4, MOV, MKV or WEBM</div>
            </>
          )}
          <input
            ref={fileInput}
            type="file"
            accept="video/*,.mkv"
            hidden
            onChange={(e) => pickFile(e.target.files?.[0])}
          />
        </div>
      )}

      {/* Options */}
      <div className="mt-6 grid gap-5 md:grid-cols-3">
        <Field label="Clip length">
          <Segmented value={length} options={LENGTHS} onChange={setLength} />
        </Field>
        <Field label="Number of clips">
          <select className={inputClass} value={count} onChange={(e) => setCount(Number(e.target.value))}>
            {COUNTS.map((n) => (
              <option key={n} value={n}>
                {n === 0 ? "Auto" : `Up to ${n}`}
              </option>
            ))}
          </select>
        </Field>
        <Field label="Spoken language">
          <select className={inputClass} value={language} onChange={(e) => setLanguage(e.target.value)}>
            {LANGUAGES.map(([code, name]) => (
              <option key={code} value={code}>
                {name}
              </option>
            ))}
          </select>
        </Field>
      </div>

      <div className="mt-5">
        <Field label="Caption style">
          <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
            {STYLES.map((s) => (
              <button
                key={s.id}
                type="button"
                onClick={() => setTemplate(s.id)}
                className={`overflow-hidden rounded-xl border text-left transition ${
                  template === s.id ? "border-accent ring-1 ring-accent" : "border-line hover:border-muted"
                }`}
              >
                <div className="caption-preview flex h-16 items-center justify-center bg-gradient-to-br from-[#2a3140] to-[#151820] text-sm text-white">
                  {s.preview}
                </div>
                <div className="px-3 py-2 text-xs font-medium">{s.name}</div>
              </button>
            ))}
          </div>
        </Field>
      </div>

      <div className="mt-5 grid gap-5 md:grid-cols-2">
        <Field label="Jump cuts" hint={PACE_HINT[pace]}>
          <Segmented value={pace} options={PACES} onChange={setPace} />
        </Field>
        <Field label="Framing" hint={reframe === "face" ? "crops to whoever is talking" : "fixed centre crop"}>
          <Segmented value={reframe} options={FRAMINGS} onChange={setReframe} />
        </Field>
      </div>

      <div className="mt-5 flex flex-wrap items-center justify-between gap-3">
        <label className="flex cursor-pointer items-center gap-3 text-sm">
          <input
            type="checkbox"
            checked={hookTitle}
            onChange={(e) => setHookTitle(e.target.checked)}
            className="size-4 accent-[var(--color-accent)]"
          />
          Show AI hook title for the first 3 seconds
        </label>
        <button
          type="button"
          onClick={() => setShowAdvanced((v) => !v)}
          className="flex items-center gap-1 text-sm text-muted hover:text-text"
        >
          Time range
          <ChevronDown className={`size-4 transition ${showAdvanced ? "rotate-180" : ""}`} />
        </button>
      </div>

      {showAdvanced && (
        <div className="mt-4 grid gap-4 rounded-xl border border-line bg-bg p-4 sm:grid-cols-2">
          <Field label="Start" hint="optional, e.g. 2:30">
            <input className={inputClass} placeholder="0:00" value={startText} onChange={(e) => setStartText(e.target.value)} />
          </Field>
          <Field label="End" hint="optional, e.g. 1:05:00">
            <input className={inputClass} placeholder="end of video" value={endText} onChange={(e) => setEndText(e.target.value)} />
          </Field>
        </div>
      )}

      {error && <div className="mt-4 rounded-lg border border-danger/30 bg-danger/10 px-3 py-2 text-sm text-danger">{error}</div>}
      {uploadPct !== null && (
        <div className="mt-4 space-y-1.5">
          <div className="flex justify-between text-xs text-muted">
            <span>Uploading…</span>
            <span>{Math.round(uploadPct * 100)}%</span>
          </div>
          <ProgressBar value={uploadPct} />
        </div>
      )}

      <button
        type="submit"
        disabled={submitting}
        className="mt-6 flex w-full items-center justify-center gap-2 rounded-xl bg-accent px-4 py-3.5 font-semibold text-accent-ink transition hover:brightness-110 disabled:opacity-60"
      >
        {submitting ? <Loader2 className="size-5 animate-spin" /> : <Wand2 className="size-5" />}
        {submitting ? "Starting…" : "Generate clips"}
      </button>
    </form>
  );
}
