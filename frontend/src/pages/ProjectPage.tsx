import {
  ArrowLeft, Check, Copy, Download, FileText, Loader2, PenLine, RotateCcw, Sparkles, Trash2, TriangleAlert,
} from "lucide-react";
import { useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { api, downloadUrl, fmtTime, mediaUrl } from "../api";
import { ProgressBar, scoreColor, Segmented, StatusBadge } from "../components/ui";
import { usePolling } from "../hooks";
import type { Clip, PostCopy, Project } from "../types";

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

type Platform = "youtube" | "tiktok" | "instagram";

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

function ClipCard({ projectId, clip, platform }: { projectId: string; clip: Clip; platform: Platform }) {
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
        <span className={`absolute left-2 top-2 rounded-md px-2 py-0.5 text-xs font-bold ${scoreColor(clip.score)}`}>
          {clip.score}
        </span>
        <span className="absolute right-2 top-2 rounded-md bg-black/70 px-2 py-0.5 text-xs tabular-nums">
          {fmtTime(clip.duration)}
        </span>
      </div>
      <div className="flex flex-1 flex-col gap-2 p-4">
        <h3 className="font-semibold leading-snug">{clip.title}</h3>
        {clip.hook && <p className="text-sm text-accent">“{clip.hook}”</p>}
        {clip.reason && <p className="text-xs leading-relaxed text-muted">{clip.reason}</p>}
        <p className="text-xs text-muted">
          From {fmtTime(clip.start)} to {fmtTime(clip.end)}
        </p>
        {clip.post && <PostPanel post={clip.post} platform={platform} />}
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

  const running = !project || project.status === "processing" || project.status === "queued";
  usePolling(
    async () => {
      try {
        setProject(await api.project(id));
      } catch (err) {
        if ((err as Error).message.includes("not found")) setNotFound(true);
      }
    },
    1500,
    running && !notFound,
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
            {project.curation_mode === "llm" && (
              <span className="inline-flex items-center gap-1">
                · <Sparkles className="size-3.5 text-accent" /> AI-picked
              </span>
            )}
            {project.curation_mode === "heuristic" && <span>· Heuristic picks</span>}
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

      {project.clips.length > 0 && (
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
          {project.clips.map((c) => (
            <ClipCard key={c.id} projectId={project.id} clip={c} platform={platform} />
          ))}
        </div>
      )}
    </div>
  );
}
