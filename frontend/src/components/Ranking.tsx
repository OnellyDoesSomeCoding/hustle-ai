import { Megaphone, Music, PartyPopper, Laugh } from "lucide-react";
import type { Clip, Cue, Factor } from "../types";

export const FACTORS: { id: Factor; label: string; best: string; help: string }[] = [
  { id: "hook", label: "Hook", best: "Best hook", help: "How hard the first 3 seconds stop the scroll." },
  { id: "payoff", label: "Payoff", best: "Best payoff", help: "Ends on a punchline, insight or resolution." },
  {
    id: "standalone",
    label: "Standalone",
    best: "Most standalone",
    help: "Makes sense to someone who hasn't seen the full video.",
  },
  {
    id: "energy",
    label: "Energy",
    best: "Most energy",
    help: "Measured: loudness and expression vs. the rest of the video, laughter, applause, on-screen motion.",
  },
  {
    id: "pacing",
    label: "Pacing",
    best: "Best pacing",
    help: "Measured: words per second after jump cuts, and dead air.",
  },
];

const CUES: Record<Cue, { label: string; Icon: typeof Laugh; tone: string; help: string }> = {
  laughter: { label: "Laughs", Icon: Laugh, tone: "text-accent", help: "Laughter detected in or right after the clip" },
  applause: { label: "Applause", Icon: PartyPopper, tone: "text-accent", help: "Applause or cheering detected" },
  shouting: { label: "Hype", Icon: Megaphone, tone: "text-accent", help: "Shouting or yelling detected" },
  music: { label: "Music", Icon: Music, tone: "text-warn", help: "Mostly music, little speech" },
};

/** "Best hook", "Most energy"... for the clip that leads each factor in its project. */
export function bestLabels(clips: Clip[]): Record<string, string[]> {
  const out: Record<string, string[]> = {};
  const scored = clips.filter((c) => c.scores);
  if (scored.length < 3) return out;
  for (const f of FACTORS) {
    const values = scored.map((c) => c.scores![f.id] ?? -1);
    const max = Math.max(...values);
    if (max < 60 || values.filter((v) => v === max).length > 1) continue; // no clear leader
    const leader = scored[values.indexOf(max)];
    (out[leader.id] ??= []).push(f.best);
  }
  return out;
}

function barTone(v: number) {
  if (v >= 75) return "bg-accent";
  if (v >= 55) return "bg-ok";
  if (v >= 40) return "bg-warn";
  return "bg-muted/60";
}

export function ScoreBreakdown({ scores }: { scores: NonNullable<Clip["scores"]> }) {
  return (
    <dl className="grid grid-cols-[auto_1fr_auto] items-center gap-x-2 gap-y-1">
      {FACTORS.map((f) => {
        const v = scores[f.id];
        return (
          <div key={f.id} className="contents" title={f.help}>
            <dt className="text-[11px] text-muted">{f.label}</dt>
            <dd className="h-1.5 overflow-hidden rounded-full bg-line">
              {v != null && <div className={`h-full rounded-full ${barTone(v)}`} style={{ width: `${v}%` }} />}
            </dd>
            <dd className="w-6 text-right text-[11px] tabular-nums text-muted">{v ?? "—"}</dd>
          </div>
        );
      })}
    </dl>
  );
}

export function ClipTags({ cues = [], best = [] }: { cues?: Cue[]; best?: string[] }) {
  if (!cues.length && !best.length) return null;
  return (
    <div className="flex flex-wrap gap-1.5">
      {best.map((b) => (
        <span key={b} className="rounded-md bg-accent/15 px-1.5 py-0.5 text-[11px] font-medium text-accent">
          {b}
        </span>
      ))}
      {cues.map((c) => {
        const { label, Icon, tone, help } = CUES[c];
        return (
          <span
            key={c}
            title={help}
            className={`flex items-center gap-1 rounded-md bg-panel-2 px-1.5 py-0.5 text-[11px] ${tone}`}
          >
            <Icon className="size-3" /> {label}
          </span>
        );
      })}
    </div>
  );
}

export type SortKey = "rank" | Factor | "timeline" | "length";

export const SORTS: { value: SortKey; label: string }[] = [
  { value: "rank", label: "Best overall" },
  { value: "hook", label: "Hook" },
  { value: "payoff", label: "Payoff" },
  { value: "energy", label: "Energy" },
  { value: "standalone", label: "Standalone" },
  { value: "pacing", label: "Pacing" },
  { value: "timeline", label: "Video order" },
  { value: "length", label: "Shortest" },
];

export function sortClips(clips: Clip[], key: SortKey): Clip[] {
  const out = [...clips];
  if (key === "rank") out.sort((a, b) => b.score - a.score);
  else if (key === "timeline") out.sort((a, b) => a.start - b.start);
  else if (key === "length") out.sort((a, b) => a.duration - b.duration);
  else out.sort((a, b) => (b.scores?.[key] ?? -1) - (a.scores?.[key] ?? -1) || b.score - a.score);
  return out;
}
