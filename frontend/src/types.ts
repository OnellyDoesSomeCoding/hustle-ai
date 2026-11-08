export type ClipLength = "under30" | "under60" | "under90" | "under3min";

export interface Options {
  length: ClipLength;
  max_clips: number;
  language: string | null;
  caption_template: string;
  hook_title: boolean;
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
