export type ClipLength = "under30" | "under60" | "under90" | "under3min";
export type Pace = "off" | "natural" | "fast";
export type Platform = "youtube" | "tiktok" | "instagram";

/** Results of a posted clip, typed in from the platform's analytics. */
export interface ClipStats {
  platform: Platform | null;
  views: number;
  likes: number | null;
  comments: number | null;
  shares: number | null;
  /** Average % of the clip viewed. */
  retention: number | null;
  updated_at?: number;
}

export interface Options {
  length: ClipLength;
  max_clips: number;
  language: string | null;
  caption_template: string;
  hook_title: boolean;
  /** Jump cuts: remove pauses and filler words. */
  pace: Pace;
  /** Face-tracked or centred 9:16 crop. */
  reframe: "face" | "center";
  start: number | null;
  end: number | null;
}

export interface Clip {
  id: string;
  index: number;
  start: number;
  end: number;
  duration: number;
  title: string;
  hook: string;
  reason: string;
  score: number;
  status: "pending" | "done";
  video: string | null;
  thumb: string | null;
  srt: string | null;
  post?: PostCopy | null;
  /** Seconds of pauses and filler words cut out. */
  trimmed?: number;
  framing?: "face" | "center";
  stats?: ClipStats | null;
}

/** Ready-to-paste text for each platform's upload form. */
export interface PostCopy {
  hashtags: string[];
  youtube: { title: string; description: string };
  tiktok: { caption: string };
  instagram: { caption: string };
}

export type Status = "queued" | "processing" | "done" | "error";

export interface Project {
  id: string;
  title: string;
  created_at: number;
  source: { type: "url" | "upload"; url?: string; filename?: string };
  options: Options;
  status: Status;
  stage: "queued" | "download" | "transcribe" | "curate" | "posts" | "render" | "done";
  progress: number;
  message: string;
  error: string | null;
  media: { duration: number; width: number; height: number; fps: number } | null;
  language: string | null;
  curation_mode: "llm" | "heuristic" | null;
  note?: string | null;
  posts_mode?: "llm" | "basic" | null;
  posts_note?: string | null;
  render_note?: string | null;
  /** Number of past clips with results the AI learned from when picking these clips. */
  learned_from?: number;
  clips: Clip[];
}

export interface SystemInfo {
  ffmpeg: boolean;
  encoder: string | null;
  cuda: boolean;
  whisper_model: string;
  ollama: { running: boolean; model_ready: boolean; model: string };
  ollama_model: string;
  templates: { id: string; name: string }[];
}

export interface InsightClip extends ClipStats {
  project_id: string;
  project_title: string;
  clip_id: string;
  title: string;
  hook: string;
  score: number;
  duration: number;
  thumb: string | null;
  video: string | null;
  /** 0..1 performance relative to the creator's other logged clips. */
  perf: number;
  engagement: number | null;
}

export interface Insights {
  logged: number;
  min_needed: number;
  learning: boolean;
  /** Rank correlation between the AI score and real performance (-1..1), null below 5 clips. */
  accuracy: number | null;
  clips: InsightClip[];
}
