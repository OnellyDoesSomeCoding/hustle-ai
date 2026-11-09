import {
  ArrowDown, ArrowUp, Download, Film, ListOrdered, Loader2, Plus, Shuffle, Trash2, TriangleAlert, X,
} from "lucide-react";
import { useEffect, useState } from "react";
import { api, countdownDownloadUrl, fmtTime, mediaUrl } from "../api";
import type { Clip, Project } from "../types";
import { inputClass, ProgressBar } from "./ui";

const SIZES = [3, 5, 10];
const LENGTHS: { value: number | null; label: string }[] = [
  { value: null, label: "Full" },
  { value: 30, label: "30s" },
  { value: 15, label: "15s" },
  { value: 10, label: "10s" },
];
const SHORTS_MAX = 180; // YouTube Shorts limit, in seconds
const LABEL_MAX = 26; // the renderer cuts labels longer than this

type Item = { id: string; title: string };

/** A short list label: the AI hook if there is one, else the title, cut at a word. */
function defaultLabel(clip: Clip): string {
  const text = (clip.hook || clip.title).replace(/\s+/g, " ").trim();
  if (text.length <= LABEL_MAX) return text;
  const cut = text.slice(0, LABEL_MAX + 1);
  const space = cut.lastIndexOf(" ");
  return (space > 10 ? cut.slice(0, space) : cut.slice(0, LABEL_MAX)).replace(/[\s,.;:!?-]+$/, "");
}

/** Random reveal order of ranks 1..n; never a plain 1→N or N→1 (for 3+). */
function shuffled(n: number): number[] {
  const plain = (o: number[]) =>
    n >= 3 && (o.every((r, i) => r === i + 1) || o.every((r, i) => r === n - i));
  let order: number[];
  do {
    order = Array.from({ length: n }, (_, i) => i + 1);
    for (let i = n - 1; i > 0; i--) {
      const j = Math.floor(Math.random() * (i + 1));
      [order[i], order[j]] = [order[j], order[i]];
    }
  } while (plain(order));
  return order;
}

/** The best clips as one "Top N" list video: clips play back to back, ranks revealed in a shuffled order. */
export default function CountdownPanel({
  project, onChange,
}: { project: Project; onChange: (p: Project) => void }) {
  const job = project.countdown ?? null;
  const ready = project.clips.filter((c) => c.status === "done" && c.video);
  const byId = Object.fromEntries(project.clips.map((c) => [c.id, c]));
  const best = [...ready].sort((a, b) => b.score - a.score);

  const [editing, setEditing] = useState(false);
  const [items, setItems] = useState<Item[]>([]);
  const [title, setTitle] = useState("");
  const [reveal, setReveal] = useState<number[]>([]);
  const [clipMax, setClipMax] = useState<number | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // A new list size needs a new reveal order.
  useEffect(() => {
    if (editing && reveal.length !== items.length) setReveal(shuffled(items.length));
  }, [editing, items.length, reveal.length]);

  if (ready.length < 2) return null;
  const running = job?.status === "queued" || job?.status === "processing";

  const pickTop = (n: number) => setItems(best.slice(0, n).map((c) => ({ id: c.id, title: defaultLabel(c) })));
  const open = () => {
    if (job?.items?.length) {
      const kept = job.items.filter((i) => byId[i.id]);
      setItems(kept.map((i) => ({ id: i.id, title: i.title || defaultLabel(byId[i.id]) })));
      setTitle(job.title);
      setReveal(job.reveal?.length === kept.length ? job.reveal : shuffled(kept.length));
      setClipMax(job.clip_max ?? null);
    } else {
      pickTop(Math.min(5, ready.length));
      setTitle("");
      setClipMax(null);
    }
    setError(null);
    setEditing(true);
  };
  const move = (i: number, d: -1 | 1) =>
    setItems((list) => {
      const next = [...list];
      [next[i], next[i + d]] = [next[i + d], next[i]];
      return next;
    });
  const unused = best.filter((c) => !items.some((i) => i.id === c.id));
  const lengthOf = (c: Clip | undefined) => Math.min(c?.duration ?? 0, clipMax ?? Infinity);
  const total = items.reduce((s, i) => s + lengthOf(byId[i.id]), 0);

  const render = async () => {
    setBusy(true);
    setError(null);
    try {
      onChange(
        await api.makeCountdown(project.id, {
          items: items.map((i) => ({ id: i.id, title: i.title.trim() })),
          title: title.trim(),
          reveal: reveal.length === items.length ? reveal : shuffled(items.length),
          clip_max: clipMax,
        }),
      );
      setEditing(false);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  };
  const remove = async () => {
    if (!confirm("Delete the top list video?")) return;
    setBusy(true);
    try {
      onChange(await api.deleteCountdown(project.id));
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const header = (
    <div className="flex items-center gap-2 text-sm font-semibold">
      <ListOrdered className="size-4 text-accent" /> Top list video
    </div>
  );

  if (editing) {
    return (
      <section className="space-y-4 rounded-2xl border border-line bg-panel p-5">
        <div className="flex flex-wrap items-center justify-between gap-3">
          {header}
          <div className="flex items-center gap-2 text-xs text-muted">
            Start from the top
            {[...new Set(SIZES.map((n) => Math.min(n, ready.length)))].map((n) => (
              <button
                key={n}
                type="button"
                onClick={() => pickTop(n)}
                className="rounded-md border border-line px-2 py-1 font-medium text-text hover:border-muted"
              >
                {n}
              </button>
            ))}
          </div>
        </div>
        <label className="block space-y-1">
          <span className="text-xs font-medium text-muted">Title at the top</span>
          <input
            value={title}
            maxLength={80}
            onChange={(e) => setTitle(e.target.value)}
            placeholder={`Top ${items.length || 5} moments`}
            className={inputClass}
          />
        </label>
        <div className="space-y-1">
          <span className="text-xs font-medium text-muted">Ranking: the label shows in the list as each clip plays</span>
          <ol className="space-y-2">
            {items.map((item, i) => {
              const clip = byId[item.id] as Clip;
              return (
                <li key={item.id} className="flex items-center gap-3 rounded-xl border border-line bg-bg/50 p-2">
                  <span className="w-8 shrink-0 text-center text-lg font-black text-accent">{i + 1}.</span>
                  <div className="flex h-12 w-7 shrink-0 items-center justify-center overflow-hidden rounded bg-panel-2">
                    {clip.thumb ? (
                      <img src={mediaUrl(project.id, clip.thumb)} alt="" className="h-full w-full object-cover" />
                    ) : (
                      <Film className="size-3.5 text-muted" />
                    )}
                  </div>
                  <div className="min-w-0 flex-1 space-y-1">
                    <input
                      value={item.title}
                      maxLength={LABEL_MAX}
                      aria-label={`List label for #${i + 1}`}
                      onChange={(e) =>
                        setItems((list) => list.map((x, j) => (j === i ? { ...x, title: e.target.value } : x)))
                      }
                      className="w-full rounded-md border border-line bg-bg px-2 py-1.5 text-sm outline-none focus:border-accent/60"
                    />
                    <div className="truncate text-[11px] text-muted" title={clip.title}>
                      Score {clip.score} · {fmtTime(clip.duration)} · {clip.title}
                    </div>
                  </div>
                  <div className="flex shrink-0 gap-1">
                    <IconBtn label="Move up" disabled={i === 0} onClick={() => move(i, -1)}>
                      <ArrowUp className="size-3.5" />
                    </IconBtn>
                    <IconBtn label="Move down" disabled={i === items.length - 1} onClick={() => move(i, 1)}>
                      <ArrowDown className="size-3.5" />
                    </IconBtn>
                    <IconBtn label="Remove" onClick={() => setItems((list) => list.filter((_, j) => j !== i))}>
                      <X className="size-3.5" />
                    </IconBtn>
                  </div>
                </li>
              );
            })}
          </ol>
        </div>
        {unused.length > 0 && items.length < 10 && (
          <div className="flex items-center gap-2">
            <Plus className="size-4 text-muted" />
            <select
              value=""
              onChange={(e) => {
                const clip = byId[e.target.value];
                if (clip) setItems((list) => [...list, { id: clip.id, title: defaultLabel(clip) }]);
              }}
              aria-label="Add a clip"
              className="min-w-0 flex-1 rounded-md border border-line bg-bg px-2 py-1.5 text-sm text-muted outline-none"
            >
              <option value="">Add a clip…</option>
              {unused.map((c) => (
                <option key={c.id} value={c.id}>
                  {c.score} · {c.title}
                </option>
              ))}
            </select>
          </div>
        )}
        <div className="flex flex-wrap items-center gap-x-6 gap-y-3">
          <div className="flex flex-wrap items-center gap-2 text-xs text-muted">
            Reveal order
            <span className="flex flex-wrap gap-1">
              {reveal.length === items.length &&
                reveal.map((r, n) => (
                  <span key={n} className="rounded-md bg-panel-2 px-1.5 py-0.5 font-semibold tabular-nums text-text">
                    #{r}
                  </span>
                ))}
            </span>
            <button
              type="button"
              onClick={() => setReveal(shuffled(items.length))}
              disabled={items.length < 2}
              className="flex items-center gap-1 rounded-md border border-line px-2 py-1 font-medium text-text hover:border-muted disabled:opacity-50"
            >
              <Shuffle className="size-3.5" /> Shuffle
            </button>
          </div>
          <div className="flex items-center gap-2 text-xs text-muted">
            Per clip
            <div className="flex overflow-hidden rounded-md border border-line">
              {LENGTHS.map((l) => (
                <button
                  key={l.label}
                  type="button"
                  onClick={() => setClipMax(l.value)}
                  className={`px-2 py-1 font-medium ${
                    clipMax === l.value ? "bg-accent text-accent-ink" : "text-text hover:bg-panel-2"
                  }`}
                >
                  {l.label}
                </button>
              ))}
            </div>
          </div>
        </div>
        <p className="text-xs text-muted">
          The clips play back to back. The title stays at the top, and the numbered list on the left fills in as each
          clip plays. About {fmtTime(total)} long{clipMax ? `, each clip cut to its first ${clipMax}s` : ""}.
        </p>
        {total > SHORTS_MAX && (
          <p className="flex items-center gap-1.5 text-xs text-warn">
            <TriangleAlert className="size-3.5 shrink-0" /> Over 3:00 is too long for YouTube Shorts. Pick fewer clips
            or shorten each one.
          </p>
        )}
        {error && <p className="text-sm text-danger">{error}</p>}
        <div className="flex gap-2">
          <button
            type="button"
            disabled={busy || items.length < 2}
            onClick={render}
            className="rounded-lg bg-accent px-4 py-2 text-sm font-semibold text-accent-ink disabled:opacity-50"
          >
            {busy ? "Starting…" : `Render top ${items.length}`}
          </button>
          <button
            type="button"
            onClick={() => setEditing(false)}
            className="rounded-lg border border-line px-4 py-2 text-sm text-muted hover:text-text"
          >
            Cancel
          </button>
        </div>
      </section>
    );
  }

  if (!job) {
    return (
      <section className="flex flex-wrap items-center justify-between gap-3 rounded-2xl border border-dashed border-line px-5 py-4">
        <div>
          {header}
          <p className="mt-1 text-xs text-muted">
            Turn your best clips into one "Top {Math.min(5, ready.length)}" video. The ranks are revealed in a random
            order as the clips play.
          </p>
        </div>
        <button
          type="button"
          disabled={project.status !== "done"}
          onClick={open}
          className="rounded-lg border border-line px-3 py-2 text-sm font-medium hover:border-muted disabled:opacity-50"
        >
          Make top list
        </button>
      </section>
    );
  }

  const firstShown = job.reveal?.length ? job.items[job.reveal[0] - 1] : job.items[0];
  return (
    <section className="rounded-2xl border border-line bg-panel p-5">
      <div className="flex flex-wrap items-start gap-5">
        {job.status === "done" && job.video ? (
          <video
            src={`${mediaUrl(project.id, job.video)}?v=${job.rendered_at ?? 0}`}
            poster={byId[firstShown?.id]?.thumb ? mediaUrl(project.id, byId[firstShown.id].thumb!) : undefined}
            controls
            preload="none"
            className="aspect-[9/16] w-40 shrink-0 rounded-xl bg-black object-contain"
          />
        ) : null}
        <div className="min-w-0 flex-1 space-y-3">
          {header}
          <p className="text-sm">
            <b>{job.title}</b>
            <span className="text-muted">
              {" "}
              · Top {job.items.length}
              {job.duration ? ` · ${fmtTime(job.duration)}` : ""}
            </span>
          </p>
          <ol className="space-y-0.5 text-xs text-muted">
            {job.items.map((i, n) => (
              <li key={i.id} className="truncate">
                <b className="text-text">{n + 1}.</b> {i.title || byId[i.id]?.title}
              </li>
            ))}
          </ol>
          {job.reveal?.length ? (
            <p className="text-xs text-muted">
              Revealed in the order {job.reveal.map((r) => `#${r}`).join(" → ")}
            </p>
          ) : null}
          {running && (
            <div className="space-y-1.5">
              <ProgressBar value={job.progress} />
              <div className="flex items-center gap-1.5 text-xs text-muted">
                <Loader2 className="size-3.5 animate-spin" /> {job.message}
              </div>
            </div>
          )}
          {job.status === "error" && (
            <div className="flex gap-2 rounded-lg border border-danger/30 bg-danger/10 p-3 text-xs text-danger">
              <TriangleAlert className="size-4 shrink-0" />
              <span className="whitespace-pre-wrap">{job.error}</span>
            </div>
          )}
          {error && <p className="text-sm text-danger">{error}</p>}
          {!running && (
            <div className="flex flex-wrap gap-2">
              {job.status === "done" && (
                <a
                  href={countdownDownloadUrl(project.id)}
                  className="flex items-center gap-1.5 rounded-lg bg-accent px-3 py-2 text-sm font-semibold text-accent-ink"
                >
                  <Download className="size-4" /> MP4
                </a>
              )}
              <button
                type="button"
                disabled={busy || project.status !== "done"}
                onClick={open}
                className="rounded-lg border border-line px-3 py-2 text-sm text-muted hover:text-text disabled:opacity-50"
              >
                {job.status === "error" ? "Edit & retry" : "Edit & re-render"}
              </button>
              <button
                type="button"
                disabled={busy}
                onClick={remove}
                title="Delete top list video"
                className="flex items-center rounded-lg border border-line px-3 py-2 text-sm text-muted hover:border-danger/40 hover:text-danger"
              >
                <Trash2 className="size-4" />
              </button>
            </div>
          )}
        </div>
      </div>
    </section>
  );
}

function IconBtn({
  label, disabled, onClick, children,
}: { label: string; disabled?: boolean; onClick: () => void; children: React.ReactNode }) {
  return (
    <button
      type="button"
      title={label}
      aria-label={label}
      disabled={disabled}
      onClick={onClick}
      className="flex size-7 items-center justify-center rounded-md border border-line text-muted hover:text-text disabled:opacity-30"
    >
      {children}
    </button>
  );
}
