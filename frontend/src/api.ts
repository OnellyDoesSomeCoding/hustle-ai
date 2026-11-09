import type { ClipStats, CountdownJob, Insights, Options, Project, SystemInfo } from "./types";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, init);
  if (!res.ok) throw new Error(await errorText(res));
  return res.status === 204 ? (undefined as T) : res.json();
}

async function errorText(res: Response): Promise<string> {
  try {
    const body = await res.json();
    return typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail);
  } catch {
    return `${res.status} ${res.statusText}`;
  }
}

export const api = {
  system: () => request<SystemInfo>("/api/system"),
  projects: () => request<Project[]>("/api/projects"),
  project: (id: string) => request<Project>(`/api/projects/${id}`),
  remove: (id: string) => request<void>(`/api/projects/${id}`, { method: "DELETE" }),
  retry: (id: string, full = false) =>
    request<Project>(`/api/projects/${id}/retry?full=${full}`, { method: "POST" }),
  rewritePosts: (id: string) => request<Project>(`/api/projects/${id}/posts`, { method: "POST" }),
  saveStats: (id: string, clipId: string, stats: ClipStats) =>
    request<Project>(`/api/projects/${id}/clips/${clipId}/stats`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(stats),
    }),
  deleteStats: (id: string, clipId: string) =>
    request<Project>(`/api/projects/${id}/clips/${clipId}/stats`, { method: "DELETE" }),
  insights: () => request<Insights>("/api/insights"),
  makeCountdown: (
    id: string,
    body: { items: CountdownJob["items"]; title: string; reveal: number[]; clip_max: number | null },
  ) =>
    request<Project>(`/api/projects/${id}/countdown`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }),
  deleteCountdown: (id: string) => request<Project>(`/api/projects/${id}/countdown`, { method: "DELETE" }),

  /** Uses XHR so large uploads can report progress. */
  create(source: { url?: string; file?: File }, options: Options, onUpload?: (pct: number) => void) {
    const form = new FormData();
    form.append("options", JSON.stringify(options));
    if (source.url) form.append("url", source.url);
    if (source.file) form.append("file", source.file);

    return new Promise<Project>((resolve, reject) => {
      const xhr = new XMLHttpRequest();
      xhr.open("POST", "/api/projects");
      xhr.upload.onprogress = (e) => e.lengthComputable && onUpload?.(e.loaded / e.total);
      xhr.onload = () => {
        let body: { detail?: unknown } & Partial<Project> = {};
        try {
          body = JSON.parse(xhr.responseText);
        } catch {
          /* non-JSON error */
        }
        if (xhr.status >= 200 && xhr.status < 300) resolve(body as Project);
        else reject(new Error(typeof body.detail === "string" ? body.detail : `Upload failed (${xhr.status})`));
      };
      xhr.onerror = () => reject(new Error("Could not reach the HustleAI server. Is the backend running?"));
      xhr.send(form);
    });
  },
};

export const mediaUrl = (projectId: string, rel: string) => `/media/${projectId}/${rel}`;
export const downloadUrl = (projectId: string, clipId: string, kind: "mp4" | "srt") =>
  `/api/projects/${projectId}/clips/${clipId}/download?kind=${kind}`;

export const countdownDownloadUrl = (projectId: string) => `/api/projects/${projectId}/countdown/download`;

export function fmtTime(sec: number): string {
  const s = Math.max(0, Math.round(sec));
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const r = s % 60;
  return h ? `${h}:${String(m).padStart(2, "0")}:${String(r).padStart(2, "0")}` : `${m}:${String(r).padStart(2, "0")}`;
}

/** "1:23" or "01:02:03" or "83" -> seconds. Empty -> null. Invalid -> NaN. */
export function parseTime(text: string): number | null {
  const t = text.trim();
  if (!t) return null;
  const parts = t.split(":").map(Number);
  if (parts.some((n) => Number.isNaN(n) || n < 0) || parts.length > 3) return NaN;
  return parts.reduce((acc, n) => acc * 60 + n, 0);
}

/** 1234 -> "1,234", 48200 -> "48.2K", 1500000 -> "1.5M". */
export function fmtCount(n: number): string {
  if (n >= 1e6) return `${(n / 1e6).toFixed(1).replace(/\.0$/, "")}M`;
  if (n >= 1e4) return `${(n / 1e3).toFixed(1).replace(/\.0$/, "")}K`;
  return n.toLocaleString();
}
