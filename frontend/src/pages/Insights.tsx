import { BarChart3, Film, Scale, Sparkles, Target, TrendingUp } from "lucide-react";
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, fmtCount, fmtTime, mediaUrl } from "../api";
import { FACTORS } from "../components/Ranking";
import { ProgressBar, scoreColor } from "../components/ui";
import type { InsightClip, Insights as InsightsData, RankingReport } from "../types";

const PLATFORM_LABEL = { youtube: "Shorts", tiktok: "TikTok", instagram: "Reels" } as const;

function accuracyLabel(r: number) {
  if (r >= 0.5) return { text: "Strong match", tone: "text-ok" };
  if (r >= 0.2) return { text: "Some match", tone: "text-warn" };
  return { text: "Weak match", tone: "text-danger" };
}

function Row({ clip, rank, total }: { clip: InsightClip; rank: number; total: number }) {
  // Same rule the backend uses to pick examples for the AI (learning.examples).
  const top = total >= 4 && rank < 3 && clip.perf >= 0.5;
  const bottom = total >= 4 && rank >= total - 2 && clip.perf < 0.5;
  return (
    <Link
      to={`/projects/${clip.project_id}`}
      className="flex items-center gap-3 rounded-xl border border-line bg-panel p-3 transition hover:border-muted"
    >
      <span className="w-6 shrink-0 text-center text-sm font-semibold tabular-nums text-muted">{rank + 1}</span>
      <div className="flex h-16 w-9 shrink-0 items-center justify-center overflow-hidden rounded-md bg-panel-2">
        {clip.thumb ? (
          <img src={mediaUrl(clip.project_id, clip.thumb)} alt="" className="h-full w-full object-cover" />
        ) : (
          <Film className="size-4 text-muted" />
        )}
      </div>
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2">
          <h3 className="truncate text-sm font-medium">{clip.title}</h3>
          {top && <span className="shrink-0 rounded bg-ok/15 px-1.5 py-0.5 text-[10px] font-semibold text-ok">TOP</span>}
          {bottom && <span className="shrink-0 rounded bg-danger/15 px-1.5 py-0.5 text-[10px] font-semibold text-danger">WEAK</span>}
        </div>
        <div className="mt-0.5 truncate text-xs text-muted">
          {clip.project_title} · {fmtTime(clip.duration)}
          {clip.platform && <> · {PLATFORM_LABEL[clip.platform]}</>}
        </div>
        <div className="mt-2 flex items-center gap-2">
          <ProgressBar value={clip.perf} className="max-w-40" />
          <span className="text-[11px] tabular-nums text-muted">{Math.round(clip.perf * 100)}</span>
        </div>
      </div>
      <div className="hidden shrink-0 grid-cols-3 gap-5 text-right sm:grid">
        <Stat label="Views" value={fmtCount(clip.views)} />
        <Stat label="Engaged" value={clip.engagement == null ? "—" : `${(clip.engagement * 100).toFixed(1)}%`} />
        <Stat label="Viewed" value={clip.retention == null ? "—" : `${Math.round(clip.retention)}%`} />
      </div>
      <div className="flex shrink-0 flex-col items-end gap-1 sm:hidden">
        <span className="text-sm font-semibold tabular-nums">{fmtCount(clip.views)}</span>
        <span className="text-[10px] text-muted">views</span>
      </div>
      <span
        title="The ranking score when the clip was picked"
        className={`shrink-0 rounded-md px-2 py-0.5 text-xs font-bold ${scoreColor(clip.score)}`}
      >
        {clip.score}
      </span>
    </Link>
  );
}

function corrLabel(r: number | null) {
  if (r == null) return { text: "not enough data", tone: "text-muted" };
  if (r >= 0.4) return { text: "strong predictor", tone: "text-ok" };
  if (r >= 0.15) return { text: "some effect", tone: "text-warn" };
  if (r > -0.15) return { text: "no clear effect", tone: "text-muted" };
  return { text: "opposite effect", tone: "text-danger" };
}

/** Which ranking factors predicted the creator's real results, and the weights in use. */
function RankingCard({ ranking }: { ranking: RankingReport }) {
  const tuned = ranking.learned_from > 0;
  const remaining = Math.max(0, ranking.min_needed - ranking.with_scores);
  const byId = Object.fromEntries(ranking.factors.map((f) => [f.id, f]));
  const top = [...ranking.factors].filter((f) => f.corr != null).sort((a, b) => b.corr! - a.corr!)[0];
  return (
    <div className="rounded-2xl border border-line bg-panel p-5">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex items-center gap-2 text-sm font-medium">
          <Scale className="size-4 text-accent" /> What drives your results
        </div>
        <span className={`text-xs ${tuned ? "text-ok" : "text-muted"}`}>
          {tuned
            ? `Ranking tuned on ${ranking.learned_from} clips`
            : `Default weights · log ${remaining} more clip${remaining === 1 ? "" : "s"} to tune`}
        </span>
      </div>
      <p className="mt-2 text-xs text-muted">
        Every clip gets five sub-scores; the overall score weights them. Once {ranking.min_needed} clips have results,
        the weights shift toward the factors that actually predicted your views.
        {tuned && top && top.corr! >= 0.15 && (
          <>
            {" "}So far, <b className="text-text">{FACTORS.find((f) => f.id === top.id)?.label.toLowerCase()}</b> matters
            most for your audience.
          </>
        )}
      </p>
      <div className="mt-4 space-y-2.5">
        {FACTORS.map((f) => {
          const r = byId[f.id];
          const label = corrLabel(r?.corr ?? null);
          return (
            <div key={f.id} className="grid grid-cols-[6.5rem_1fr_3rem] items-center gap-3" title={f.help}>
              <div>
                <div className="text-sm">{f.label}</div>
                <div className={`text-[10px] ${label.tone}`}>{label.text}</div>
              </div>
              <div className="relative h-2 rounded-full bg-line">
                <div className="h-full rounded-full bg-accent" style={{ width: `${Math.min(100, (r?.weight ?? 0) * 200)}%` }} />
                <div
                  className="absolute top-[-3px] h-3.5 w-0.5 rounded bg-muted"
                  style={{ left: `${Math.min(100, (r?.default ?? 0) * 200)}%` }}
                  title="Default weight"
                />
              </div>
              <div className="text-right text-xs tabular-nums text-muted">{Math.round((r?.weight ?? 0) * 100)}%</div>
            </div>
          );
        })}
      </div>
      <p className="mt-3 text-[11px] text-muted">
        Bars show each factor's weight (the tick is the default). Clips logged before ranking existed have no
        sub-scores and don't count here.
      </p>
    </div>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="w-16">
      <div className="text-sm font-semibold tabular-nums">{value}</div>
      <div className="text-[10px] uppercase tracking-wide text-muted">{label}</div>
    </div>
  );
}

export default function Insights() {
  const [data, setData] = useState<InsightsData | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.insights().then(setData, (err) => setError((err as Error).message));
  }, []);

  if (error) return <div className="text-sm text-danger">{error}</div>;
  if (!data) return <div className="text-sm text-muted">Loading…</div>;

  const remaining = Math.max(0, data.min_needed - data.logged);

  return (
    <div className="space-y-6">
      <div>
        <h1 className="flex items-center gap-2 text-2xl font-bold tracking-tight">
          <TrendingUp className="size-6 text-accent" /> What's working
        </h1>
        <p className="mt-2 max-w-2xl text-sm text-muted">
          After you post a clip, open its project and press <b className="text-text">Log results</b> on the clip.
          Once {data.min_needed} clips have results, the AI shows your best and worst clips to itself as examples
          whenever it picks and scores moments in a new video.
        </p>
      </div>

      <div className="grid gap-3 sm:grid-cols-2">
        <div className="rounded-2xl border border-line bg-panel p-5">
          <div className="flex items-center gap-2 text-sm font-medium">
            <Sparkles className="size-4 text-accent" /> Learning
          </div>
          {data.learning ? (
            <p className="mt-3 text-sm">
              <b className="text-ok">On.</b> New projects learn from your {data.logged} logged clips.
            </p>
          ) : (
            <>
              <p className="mt-3 text-sm">
                Log {remaining} more clip{remaining === 1 ? "" : "s"} to switch it on.
              </p>
              <ProgressBar value={data.logged / data.min_needed} className="mt-3" />
            </>
          )}
          <p className="mt-3 text-xs text-muted">Needs Ollama: the offline heuristic can't learn from examples.</p>
        </div>
        <div className="rounded-2xl border border-line bg-panel p-5">
          <div className="flex items-center gap-2 text-sm font-medium">
            <Target className="size-4 text-accent" /> Ranking score vs. real results
          </div>
          {data.accuracy == null ? (
            <p className="mt-3 text-sm text-muted">
              Shows how well each clip's ranking score predicted its real results, once 5 clips have results.
            </p>
          ) : (
            <>
              <p className={`mt-3 text-lg font-semibold ${accuracyLabel(data.accuracy).tone}`}>
                {accuracyLabel(data.accuracy).text}
              </p>
              <p className="mt-1 text-xs text-muted">
                Rank correlation {data.accuracy.toFixed(2)} across {data.logged} clips (1 = the score ranked them
                perfectly). It should go up as the AI learns from more results.
              </p>
            </>
          )}
        </div>
      </div>

      <RankingCard ranking={data.ranking} />

      <section>
        <h2 className="mb-3 flex items-center gap-2 text-lg font-semibold">
          <BarChart3 className="size-5 text-muted" /> Your clips by performance
        </h2>
        {data.clips.length === 0 ? (
          <div className="rounded-xl border border-dashed border-line px-4 py-10 text-center text-sm text-muted">
            No results logged yet. Post a clip, then log its views on the project page.
          </div>
        ) : (
          <div className="space-y-2">
            {data.clips.map((c, i) => (
              <Row key={`${c.project_id}-${c.clip_id}`} clip={c} rank={i} total={data.clips.length} />
            ))}
            <p className="pt-1 text-xs text-muted">
              Performance is relative to your own clips: views 60%, engagement 25%, average % viewed 15%.
            </p>
          </div>
        )}
      </section>
    </div>
  );
}
