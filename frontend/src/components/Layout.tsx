import { Cpu, Sparkles, TriangleAlert, Zap } from "lucide-react";
import { Link, Outlet } from "react-router-dom";
import { useSystem } from "../hooks";

function Chip({ ok, icon, label, title }: { ok: boolean; icon: React.ReactNode; label: string; title: string }) {
  return (
    <span
      title={title}
      className={`hidden items-center gap-1.5 rounded-full border px-2.5 py-1 text-xs sm:inline-flex ${
        ok ? "border-line text-muted" : "border-warn/40 text-warn"
      }`}
    >
      {icon}
      {label}
    </span>
  );
}

export default function Layout() {
  const { system, offline } = useSystem();
  const ollama = system?.ollama;

  return (
    <div className="min-h-screen">
      <header className="sticky top-0 z-20 border-b border-line bg-bg/85 backdrop-blur">
        <div className="mx-auto flex h-14 max-w-6xl items-center justify-between gap-4 px-4">
          <Link to="/" className="flex items-center gap-2 font-semibold tracking-tight">
            <img src="/favicon.svg" alt="" className="size-7" />
            HustleAI
          </Link>
          {system && (
            <div className="flex items-center gap-2">
              <Chip
                ok={system.cuda}
                icon={<Cpu className="size-3.5" />}
                label={system.cuda ? "GPU" : "CPU only"}
                title={system.cuda ? `Transcription runs on your GPU (${system.whisper_model})` : "CUDA not found: transcription will be slow"}
              />
              <Chip
                ok={system.encoder === "h264_nvenc"}
                icon={<Zap className="size-3.5" />}
                label={system.encoder === "h264_nvenc" ? "NVENC" : "x264"}
                title="Video encoder used for rendering"
              />
              <Chip
                ok={!!ollama?.model_ready}
                icon={<Sparkles className="size-3.5" />}
                label={ollama?.model_ready ? system.ollama_model : ollama?.running ? "Model missing" : "AI offline"}
                title={
                  ollama?.model_ready
                    ? "Clips are picked by your local AI model"
                    : ollama?.running
                      ? `Run: ollama pull ${system.ollama_model}`
                      : "Start Ollama for AI-picked clips. Until then a simpler offline heuristic is used."
                }
              />
            </div>
          )}
        </div>
      </header>

      {offline && (
        <div className="border-b border-danger/30 bg-danger/10">
          <div className="mx-auto flex max-w-6xl items-center gap-2 px-4 py-2 text-sm text-danger">
            <TriangleAlert className="size-4 shrink-0" />
            Can't reach the backend on port 8000. Start it with <code className="rounded bg-bg px-1.5">npm run dev</code> from the project folder.
          </div>
        </div>
      )}
      {system && !system.ffmpeg && (
        <div className="border-b border-warn/30 bg-warn/10">
          <div className="mx-auto max-w-6xl px-4 py-2 text-sm text-warn">
            ffmpeg wasn't found. Install it with <code>winget install Gyan.FFmpeg</code> and restart the backend.
          </div>
        </div>
      )}

      <main className="mx-auto max-w-6xl px-4 py-8">
        <Outlet />
      </main>
    </div>
  );
}
