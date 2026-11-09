import {
  ArrowDownWideNarrow, ArrowLeft, BarChart3, Check, Copy, Download, FileText, Loader2, PenLine, RotateCcw, ScanFace, Scissors, Sparkles,
  Trash2, TrendingUp, TriangleAlert,
} from "lucide-react";
import { useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { api, downloadUrl, fmtCount, fmtTime, mediaUrl } from "../api";
import CountdownPanel from "../components/CountdownPanel";
import { bestLabels, ClipTags, ScoreBreakdown, SORTS, sortClips, type SortKey } from "../components/Ranking";
import { ProgressBar, scoreColor, Segmented, StatusBadge } from "../components/ui";
import { usePolling } from "../hooks";
import type { Clip, ClipStats, Platform, PostCopy, Project } from "../types";

const STEPS: { id: Project["stage"]; label: string }[] = [
  { id: "download", label: "Get video" },
  { id: "transcribe", label: "Transcribe" },
  { id: "curate", label: "Find moments" },
  { id: "posts", label: "Write posts" },
  { id: "render", label: "Render clips" },
];

function Steps({ project }: { project: Project }) {
  const current = STEPS.findIndex((s) => s.id === project.stage);
  return (
    <div className="rounded-2xl border border-line bg-panel p-5">
      <ol className="grid grid-cols-5 gap-2">
        {STEPS.map((s, i) => {
          const done = current > i || project.stage === "done";
          const active = current === i && project.status === "processing";
          return (
            <li key={s.id} className="flex flex-col items-center gap-2 text-center">
              <span
                className={`flex size-8 items-center justify-center rounded-full border text-xs font-semibold ${
                  done
                    ? "border-accent bg-accent text-accent-ink"
                    : active
                      ? "border-accent text-accent"
                      : "border-line text-muted"
                }`}
              >
                {done ? <Check className="size-4" /> : active ? <Loader2 className="size-4 animate-spin" /> : i + 1}
              </span>
              <span className={`text-xs ${active || done ? "text-text" : "text-muted"}`}>{s.label}</span>
            </li>
          );
        })}
      </ol>
      <div className="mt-5 space-y-2">
        <ProgressBar value={project.progress} />
        <div className="flex justify-between gap-4 text-sm text-muted">
          <span className="truncate">{project.message}</span>
          <span className="shrink-0 tabular-nums">{Math.round(project.progress * 100)}%</span>
        </div>
      </div>
    </div>
  );
}

/** Each platform's upload form, field by field, with the form's character limit. */
const PLATFORMS: { id: Platform; label: string; fields: { key: string; label: string; limit: number; rows: number }[] }[] = [
  {
    id: "youtube",
    label: "YouTube Shorts",
    fields: [
      { key: "title", label: "Title", limit: 100, rows: 2 },
      { key: "description", label: "Description", limit: 5000, rows: 5 },
    ],
  },
  { id: "tiktok", label: "TikTok", fields: [{ key: "caption", label: "Caption", limit: 4000, rows: 5 }] },
  { id: "instagram", label: "Reels", fields: [{ key: "caption", label: "Caption", limit: 2200, rows: 7 }] },
];
const PLATFORM_KEY = "hustle.platform";

/** The selected platform, remembered between visits. */
function usePlatform() {
  const [platform, setPlatform] = useState<Platform>(() => {
    try {
      const saved = localStorage.getItem(PLATFORM_KEY);
      if (PLATFORMS.some((p) => p.id === saved)) return saved as Platform;
    } catch {
      /* storage blocked */
    }
    return "youtube";
  });
  const choose = (p: Platform) => {
    setPlatform(p);
    try {
      localStorage.setItem(PLATFORM_KEY, p);
    } catch {
      /* storage blocked */
    }
  };
  return [platform, choose] as const;
}

/** Editable text with a one-click copy button. Edits stay local: tweak, then copy. */
function CopyField({ label, value, limit, rows }: { label: string; value: string; limit: number; rows: number }) {
  const [text, setText] = useState(value);
  const [copied, setCopied] = useState(false);
  useEffect(() => setText(value), [value]);
  const over = text.length > limit;

  const copy = async () => {
    await navigator.clipboard.writeText(text);
    setCopied(true);
    setTimeout(() => setCopied(false), 1500);
  };

  return (
    <div className="space-y-1">
      <div className="flex items-center justify-between gap-2 text-xs">
        <span className="font-medium text-muted">{label}</span>
        <span className={`tabular-nums ${over ? "text-danger" : "text-muted/70"}`}>
          {text.length}/{limit}
        </span>
      </div>
      <div className="relative">
        <textarea
          value={text}
          onChange={(e) => setText(e.target.value)}
          rows={rows}
          spellCheck={false}
          className="block w-full resize-y rounded-lg border border-line bg-bg px-2.5 py-2 pr-10 text-xs leading-relaxed outline-none transition focus:border-accent/60"
        />
        <button
          type="button"
          onClick={copy}
          title={`Copy ${label.toLowerCase()}`}
          aria-label={`Copy ${label.toLowerCase()}`}
          className={`absolute right-1.5 top-1.5 flex items-center justify-center rounded-md border px-1.5 py-1 transition ${
            copied ? "border-ok/40 bg-ok/15 text-ok" : "border-line bg-panel-2 text-muted hover:text-text"
          }`}
        >
          {copied ? <Check className="size-3.5" /> : <Copy className="size-3.5" />}
        </button>
      </div>
    </div>
  );
}

function PostPanel({ post, platform }: { post: PostCopy; platform: Platform }) {
  const spec = PLATFORMS.find((p) => p.id === platform)!;
  const fields = post[platform] as Record<string, string>;
  return (
    <div className="space-y-2.5 border-t border-line pt-3">
      {spec.fields.map((f) => (
        <CopyField key={f.key} label={f.label} value={fields[f.key] ?? ""} limit={f.limit} rows={f.rows} />
      ))}
    </div>
  );
}

const numOrNull = (text: string) => {
  const t = text.replace(/[,\s]/g, "");
  return t === "" ? null : Number(t);
};

/** Log how a posted clip did. New projects learn from these numbers. */
function ResultsPanel({
  projectId, clip, platform, editable, onSaved,
}: { projectId: string; clip: Clip; platform: Platform; editable: boolean; onSaved: (p: Project) => void }) {
  const s = clip.stats;
  const [open, setOpen] = useState(false);
  const [form, setForm] = useState<Record<string, string>>({});
  const [where, setWhere] = useState<Platform>(platform);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const start = () => {
    const str = (n: number | null | undefined) => (n == null ? "" : String(n));
    setForm({
      views: str(s?.views), likes: str(s?.likes), comments: str(s?.comments),
      shares: str(s?.shares), retention: str(s?.retention),
    });
    setWhere(s?.platform ?? platform);
    setError(null);
    setOpen(true);
  };

  const save = async (e: React.FormEvent) => {
    e.preventDefault();
    const values = Object.fromEntries(Object.entries(form).map(([k, v]) => [k, numOrNull(v)]));
    if (values.views == null) return setError("Views are required.");
    if (Object.values(values).some((v) => v != null && (Number.isNaN(v) || v < 0)))
      return setError("Use positive numbers only.");
    if (values.retention != null && values.retention > 100) return setError("Avg. % viewed must be 0–100.");
    const whole = (v: number | null) => (v == null ? null : Math.round(v));
    const stats: ClipStats = {
      platform: where,
      views: Math.round(values.views),
      likes: whole(values.likes),
      comments: whole(values.comments),
      shares: whole(values.shares),
      retention: values.retention,
    };
    setSaving(true);
    try {
      onSaved(await api.saveStats(projectId, clip.id, stats));
      setOpen(false);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setSaving(false);
    }
  };

  const remove = async () => {
    setSaving(true);
    try {
      onSaved(await api.deleteStats(projectId, clip.id));
      setOpen(false);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setSaving(false);
    }
  };

  if (!open) {
    if (!s)
      return (
        <button
          type="button"
          disabled={!editable}
          onClick={start}
          title="Posted this clip? Log its views so new projects learn what works for your audience."
          className="flex items-center justify-center gap-1.5 rounded-lg border border-dashed border-line px-3 py-2 text-xs text-muted transition hover:border-muted hover:text-text disabled:opacity-40"
        >
          <BarChart3 className="size-3.5" /> Log results
        </button>
      );
    const engaged = [s.likes, s.comments, s.shares].some((n) => n != null)
      ? ((s.likes ?? 0) + (s.comments ?? 0) + (s.shares ?? 0)) / Math.max(s.views, 1)
      : null;
    return (
      <button
        type="button"
        disabled={!editable}
        onClick={start}
        title="Edit results"
        className="flex items-center gap-2 rounded-lg border border-ok/30 bg-ok/10 px-3 py-2 text-left text-xs text-ok transition hover:border-ok/60"
      >
        <BarChart3 className="size-3.5 shrink-0" />
        <span className="min-w-0 flex-1 leading-relaxed">
          <b className="font-semibold">{fmtCount(s.views)} views</b>
          {engaged != null && <> · {(engaged * 100).toFixed(1)}% engaged</>}
          {s.retention != null && <> · {Math.round(s.retention)}% viewed</>}
        </span>
        <PenLine className="size-3.5 shrink-0 opacity-70" />
      </button>
    );
  }

  const input = (key: string, label: string, placeholder = "") => (
    <label className="space-y-1">
      <span className="text-[11px] font-medium text-muted">{label}</span>
      <input
        inputMode="decimal"
        value={form[key] ?? ""}
        placeholder={placeholder}
        onChange={(e) => setForm((f) => ({ ...f, [key]: e.target.value }))}
        className="w-full rounded-md border border-line bg-bg px-2 py-1.5 text-xs tabular-nums outline-none focus:border-accent/60"
      />
    </label>
  );

  return (
    <form onSubmit={save} className="space-y-2.5 rounded-lg border border-line bg-bg/50 p-3">
      <div className="flex items-center justify-between gap-2">
        <span className="text-xs font-semibold">How did it do?</span>
        <select
          value={where}
          onChange={(e) => setWhere(e.target.value as Platform)}
          aria-label="Platform"
          className="rounded-md border border-line bg-bg px-1.5 py-1 text-[11px] outline-none"
        >
          {PLATFORMS.map((p) => (
            <option key={p.id} value={p.id}>
              {p.label}
            </option>
          ))}
        </select>
      </div>
      <div className="grid grid-cols-2 gap-2">
        {input("views", "Views *", "12400")}
        {input("retention", "Avg. % viewed", "e.g. 62")}
        {input("likes", "Likes")}
        {input("comments", "Comments")}
        {input("shares", "Shares")}
      </div>
      {error && <p className="text-[11px] text-danger">{error}</p>}
      <div className="flex gap-2">
        <button
          type="submit"
          disabled={saving}
          className="flex-1 rounded-md bg-accent px-3 py-1.5 text-xs font-semibold text-accent-ink disabled:opacity-60"
        >
          {saving ? "Saving…" : "Save"}
        </button>
        {s && (
          <button
            type="button"
            disabled={saving}
            onClick={remove}
            className="rounded-md border border-line px-3 py-1.5 text-xs text-muted hover:text-danger"
          >
            Remove
          </button>
        )}
        <button
          type="button"
          onClick={() => setOpen(false)}
          className="rounded-md border border-line px-3 py-1.5 text-xs text-muted hover:text-text"
        >
          Cancel
        </button>
      </div>
    </form>
  );
}

function ClipCard({
  projectId, clip, platform, editable, animation, best, onSaved,
}: {
  projectId: string; clip: Clip; platform: Platform; editable: boolean; animation: boolean; best?: string[];
  onSaved: (p: Project) => void;
}) {
  const [copied, setCopied] = useState(false);
  const ready = clip.status === "done" && clip.video;

  const copy = async () => {
    await navigator.clipboard.writeText(clip.title);
    setCopied(true);
    setTimeout(() => setCopied(false), 1500);
  };

  return (
    <article className="flex flex-col overflow-hidden rounded-2xl border border-line bg-panel">
      <div className="relative aspect-[9/16] bg-black">
        {ready ? (
          <video
            src={mediaUrl(projectId, clip.video!)}
            poster={clip.thumb ? mediaUrl(projectId, clip.thumb) : undefined}
            controls
            preload="none"
            className="h-full w-full object-contain"
          />
        ) : (
          <div className="flex h-full flex-col items-center justify-center gap-2 text-sm text-muted">
            <Loader2 className="size-6 animate-spin" />
            Rendering…
          </div>
        )}
        <div className="absolute left-2 top-2 flex items-center gap-1">
          {clip.rank != null && (
            <span className="rounded-md bg-black/75 px-1.5 py-0.5 text-xs font-bold tabular-nums">#{clip.rank}</span>
          )}
          <span
            title="Overall ranking score"
            className={`rounded-md px-2 py-0.5 text-xs font-bold ${scoreColor(clip.score)}`}
          >
            {clip.score}
          </span>
        </div>
        <span className="absolute right-2 top-2 rounded-md bg-black/70 px-2 py-0.5 text-xs tabular-nums">
          {fmtTime(clip.duration)}
        </span>
        {ready && (clip.framing === "face" || (clip.trimmed ?? 0) >= 0.5) && (
          <div className="pointer-events-none absolute left-2 top-9 flex flex-col items-start gap-1">
            {clip.framing === "face" && (
              <span className="flex items-center gap-1 rounded-md bg-black/70 px-1.5 py-0.5 text-[11px]">
                <ScanFace className="size-3" /> {animation ? "Tracked" : "Speaker"}
              </span>
            )}
            {(clip.trimmed ?? 0) >= 0.5 && (
              <span className="flex items-center gap-1 rounded-md bg-black/70 px-1.5 py-0.5 text-[11px] tabular-nums">
                <Scissors className="size-3" /> −{clip.trimmed!.toFixed(1)}s
              </span>
            )}
          </div>
        )}
      </div>
      <div className="flex flex-1 flex-col gap-2 p-4">
        <h3 className="font-semibold leading-snug">{clip.title}</h3>
        {clip.hook && <p className="text-sm text-accent">“{clip.hook}”</p>}
        <ClipTags cues={clip.cues} best={best} />
        {clip.scores && <ScoreBreakdown scores={clip.scores} />}
        {clip.reason && <p className="text-xs leading-relaxed text-muted">{clip.reason}</p>}
        <p className="text-xs text-muted">
          From {fmtTime(clip.start)} to {fmtTime(clip.end)}
        </p>
        {clip.post && <PostPanel post={clip.post} platform={platform} />}
        {ready && <ResultsPanel projectId={projectId} clip={clip} platform={platform} editable={editable} onSaved={onSaved} />}
        <div className="mt-auto flex gap-2 pt-2">
          <a
            href={ready ? downloadUrl(projectId, clip.id, "mp4") : undefined}
            aria-disabled={!ready}
            className={`flex flex-1 items-center justify-center gap-1.5 rounded-lg bg-accent px-3 py-2 text-sm font-semibold text-accent-ink transition hover:brightness-110 ${
              ready ? "" : "pointer-events-none opacity-40"
            }`}
          >
            <Download className="size-4" /> MP4
          </a>
          <a
            href={ready ? downloadUrl(projectId, clip.id, "srt") : undefined}
            title="Download subtitles (.srt)"
            className={`flex items-center justify-center rounded-lg border border-line px-3 py-2 text-sm text-muted transition hover:text-text ${
              ready ? "" : "pointer-events-none opacity-40"
            }`}
          >
            <FileText className="size-4" />
          </a>
          <button
            type="button"
            onClick={copy}
            title="Copy title"
            className="flex items-center justify-center rounded-lg border border-line px-3 py-2 text-sm text-muted transition hover:text-text"
          >
            {copied ? <Check className="size-4 text-ok" /> : <Copy className="size-4" />}
          </button>
        </div>
      </div>
    </article>
  );
}

export default function ProjectPage() {
  const { id = "" } = useParams();
  const navigate = useNavigate();
  const [project, setProject] = useState<Project | null>(null);
  const [notFound, setNotFound] = useState(false);
  const [busy, setBusy] = useState(false);
  const [platform, setPlatform] = usePlatform();
  const [sort, setSort] = useState<SortKey>("rank");

  const running = !project || project.status === "processing" || project.status === "queued";
  const countdownBusy = project?.countdown?.status === "queued" || project?.countdown?.status === "processing";
  usePolling(
    async () => {
      try {
        setProject(await api.project(id));
      } catch (err) {
        if ((err as Error).message.includes("not found")) setNotFound(true);
      }
    },
    1500,
    (running || countdownBusy) && !notFound,
  );

  if (notFound)
    return (
      <div className="py-20 text-center">
        <p className="text-muted">This project doesn't exist anymore.</p>
        <Link to="/" className="mt-4 inline-block text-accent hover:underline">
          Back home
        </Link>
      </div>
    );
  if (!project) return <div className="text-sm text-muted">Loading…</div>;

  const retry = async (full: boolean) => {
    setBusy(true);
    try {
      setProject(await api.retry(project.id, full));
    } finally {
      setBusy(false);
    }
  };
  const rewritePosts = async () => {
    setBusy(true);
    try {
      setProject(await api.rewritePosts(project.id));
    } catch (err) {
      alert((err as Error).message);
    } finally {
      setBusy(false);
    }
  };
  const hasPosts = project.clips.some((c) => c.post);
  const best = bestLabels(project.clips);
  const clips = sortClips(project.clips, sort);
  const remove = async () => {
    if (!confirm(`Delete "${project.title}" and all its clips?`)) return;
    await api.remove(project.id);
    navigate("/");
  };

  const sourceLabel = project.source.type === "url" ? project.source.url : project.source.filename;

  return (
    <div className="space-y-6">
      <Link to="/" className="inline-flex items-center gap-1.5 text-sm text-muted hover:text-text">
        <ArrowLeft className="size-4" /> All projects
      </Link>

      <div className="flex flex-wrap items-start justify-between gap-4">
        <div className="min-w-0">
          <h1 className="text-2xl font-bold tracking-tight">{project.title}</h1>
          <div className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1 text-sm text-muted">
            <StatusBadge status={project.status} />
            {project.media && <span>{fmtTime(project.media.duration)} source</span>}
            {project.language && <span>· {project.language.toUpperCase()}</span>}
            {project.options.content === "animation" && <span>· Animation</span>}
            {project.curation_mode === "llm" && (
              <span className="inline-flex items-center gap-1">
                · <Sparkles className="size-3.5 text-accent" /> AI-picked
              </span>
            )}
            {project.curation_mode === "heuristic" && <span>· Heuristic picks</span>}
            {(project.weights_from ?? 0) > 0 && (
              <Link
                to="/insights"
                className="inline-flex items-center gap-1 hover:text-text"
                title="Ranking weights were tuned to what predicted your logged results"
              >
                · <TrendingUp className="size-3.5 text-ok" /> Ranking tuned on {project.weights_from} clips
              </Link>
            )}
            {(project.learned_from ?? 0) > 0 && (
              <Link
                to="/insights"
                className="inline-flex items-center gap-1 hover:text-text"
                title="The AI used your logged results when picking these clips"
              >
                · <TrendingUp className="size-3.5 text-ok" /> Learned from {project.learned_from} clips
              </Link>
            )}
            <span className="max-w-full truncate">· {sourceLabel}</span>
          </div>
        </div>
        <div className="flex gap-2">
          {project.status === "done" && (
            <button
              type="button"
              disabled={busy}
              onClick={() => retry(false)}
              title="Render all clips again (keeps transcript and picks)"
              className="flex items-center gap-1.5 rounded-lg border border-line px-3 py-2 text-sm text-muted hover:text-text"
            >
              <RotateCcw className="size-4" /> Re-render
            </button>
          )}
          <button
            type="button"
            onClick={remove}
            className="flex items-center gap-1.5 rounded-lg border border-line px-3 py-2 text-sm text-muted hover:border-danger/40 hover:text-danger"
          >
            <Trash2 className="size-4" /> Delete
          </button>
        </div>
      </div>

      {project.posts_note && (
        <div className="flex gap-2 rounded-xl border border-warn/30 bg-warn/10 px-4 py-3 text-sm text-warn">
          <TriangleAlert className="mt-0.5 size-4 shrink-0" />
          {project.posts_note}
        </div>
      )}

      {project.render_note && (
        <div className="flex gap-2 rounded-xl border border-warn/30 bg-warn/10 px-4 py-3 text-sm text-warn">
          <TriangleAlert className="mt-0.5 size-4 shrink-0" />
          {project.render_note}
        </div>
      )}

      {project.note && (
        <div className="flex gap-2 rounded-xl border border-warn/30 bg-warn/10 px-4 py-3 text-sm text-warn">
          <TriangleAlert className="mt-0.5 size-4 shrink-0" />
          {project.note}
        </div>
      )}

      {running && <Steps project={project} />}

      {project.status === "error" && (
        <div className="rounded-2xl border border-danger/30 bg-danger/10 p-5">
          <div className="flex items-center gap-2 font-semibold text-danger">
            <TriangleAlert className="size-5" /> Something went wrong
          </div>
          <pre className="mt-3 max-h-48 overflow-auto whitespace-pre-wrap rounded-lg bg-bg/60 p-3 text-xs text-text/80">
            {project.error}
          </pre>
          <div className="mt-4 flex flex-wrap gap-2">
            <button
              type="button"
              disabled={busy}
              onClick={() => retry(false)}
              className="flex items-center gap-1.5 rounded-lg bg-accent px-4 py-2 text-sm font-semibold text-accent-ink"
            >
              <RotateCcw className="size-4" /> Retry
            </button>
            <button
              type="button"
              disabled={busy}
              onClick={() => retry(true)}
              className="rounded-lg border border-line px-4 py-2 text-sm text-muted hover:text-text"
            >
              Retry from scratch
            </button>
          </div>
        </div>
      )}

      {project.clips.length > 0 && (hasPosts || project.status === "done") && (
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div className="flex w-full min-w-0 items-center gap-3 sm:w-auto">
            <span className="shrink-0 text-sm font-medium">Post copy for</span>
            <div className="min-w-0 flex-1 sm:w-96 sm:flex-none">
              <Segmented
                value={platform}
                options={PLATFORMS.map((p) => ({ value: p.id, label: p.label }))}
                onChange={setPlatform}
              />
            </div>
          </div>
          {project.status === "done" && (
            <button
              type="button"
              disabled={busy}
              onClick={rewritePosts}
              title="Write new titles, descriptions and hashtags (keeps the rendered clips)"
              className="flex items-center gap-1.5 rounded-lg border border-line px-3 py-2 text-sm text-muted hover:text-text disabled:opacity-50"
            >
              <PenLine className="size-4" /> {hasPosts ? "Rewrite posts" : "Write post copy"}
            </button>
          )}
        </div>
      )}

      {project.status === "done" && <CountdownPanel project={project} onChange={setProject} />}

      {project.clips.length > 0 && (
        <section className="space-y-3">
          <div className="flex items-center justify-between gap-3">
            <h2 className="text-lg font-semibold">
              {project.clips.length} clip{project.clips.length === 1 ? "" : "s"}
            </h2>
            <label className="flex items-center gap-2 text-sm text-muted">
              <ArrowDownWideNarrow className="size-4" />
              <span className="sr-only sm:not-sr-only">Sort by</span>
              <select
                value={sort}
                onChange={(e) => setSort(e.target.value as SortKey)}
                className="rounded-lg border border-line bg-bg px-2 py-1.5 text-sm text-text outline-none focus:border-accent/60"
              >
                {SORTS.map((o) => (
                  <option key={o.value} value={o.value}>
                    {o.label}
                  </option>
                ))}
              </select>
            </label>
          </div>
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
            {clips.map((c) => (
              <ClipCard
                key={c.id}
                projectId={project.id}
                clip={c}
                platform={platform}
                editable={project.status === "done" || project.status === "error"}
                animation={project.options.content === "animation"}
                best={best[c.id]}
                onSaved={setProject}
              />
            ))}
          </div>
        </section>
      )}
    </div>
  );
}
