import { Clapperboard, Film, Trash2 } from "lucide-react";
import { useState } from "react";
import { Link } from "react-router-dom";
import { api, fmtTime, mediaUrl } from "../api";
import NewProjectForm from "../components/NewProjectForm";
import { ProgressBar, StatusBadge } from "../components/ui";
import { usePolling } from "../hooks";
import type { Project } from "../types";

function ProjectCard({ project, onDelete }: { project: Project; onDelete: () => void }) {
  const thumb = project.clips.find((c) => c.thumb)?.thumb;
  const running = project.status === "processing" || project.status === "queued";

  return (
    <Link
      to={`/projects/${project.id}`}
      className="group relative flex gap-4 rounded-xl border border-line bg-panel p-3 transition hover:border-muted"
    >
      <div className="flex h-24 w-[54px] shrink-0 items-center justify-center overflow-hidden rounded-lg bg-panel-2">
        {thumb ? (
          <img src={mediaUrl(project.id, thumb)} alt="" className="h-full w-full object-cover" />
        ) : (
          <Film className="size-5 text-muted" />
        )}
      </div>
      <div className="min-w-0 flex-1 py-0.5">
        <div className="flex items-start justify-between gap-2">
          <h3 className="line-clamp-2 text-sm font-medium leading-snug">{project.title}</h3>
          <button
            type="button"
            aria-label="Delete project"
            onClick={(e) => {
              e.preventDefault();
              if (confirm(`Delete "${project.title}" and all its clips?`)) onDelete();
            }}
            className="rounded p-1 text-muted opacity-0 transition hover:bg-panel-2 hover:text-danger group-hover:opacity-100 focus:opacity-100"
          >
            <Trash2 className="size-4" />
          </button>
        </div>
        <div className="mt-2 flex flex-wrap items-center gap-2 text-xs text-muted">
          <StatusBadge status={project.status} />
          {project.media && <span>{fmtTime(project.media.duration)}</span>}
          {project.status === "done" && <span>· {project.clips.length} clips</span>}
        </div>
        {running && (
          <div className="mt-2.5 space-y-1">
            <ProgressBar value={project.progress} />
            <div className="truncate text-xs text-muted">{project.message}</div>
          </div>
        )}
      </div>
    </Link>
  );
}

export default function Home() {
  const [projects, setProjects] = useState<Project[] | null>(null);
  const load = async () => {
    try {
      setProjects(await api.projects());
    } catch {
      /* backend offline banner is shown by Layout */
    }
  };
  const anyRunning = projects?.some((p) => p.status === "processing" || p.status === "queued") ?? true;
  usePolling(load, anyRunning ? 2000 : 15000);

  return (
    <div className="space-y-12">
      <section className="mx-auto max-w-3xl">
        <div className="mb-6 text-center">
          <h1 className="text-3xl font-bold tracking-tight sm:text-4xl">
            Long videos in. <span className="text-accent">Viral shorts</span> out.
          </h1>
          <p className="mt-3 text-muted">
            Runs entirely on your computer: no account, no credits, no limits.
          </p>
        </div>
        <NewProjectForm onCreated={load} />
      </section>

      <section>
        <h2 className="mb-4 flex items-center gap-2 text-lg font-semibold">
          <Clapperboard className="size-5 text-muted" /> Your projects
        </h2>
        {projects === null ? (
          <div className="text-sm text-muted">Loading…</div>
        ) : projects.length === 0 ? (
          <div className="rounded-xl border border-dashed border-line px-4 py-10 text-center text-sm text-muted">
            No projects yet. Paste a link above to make your first clips.
          </div>
        ) : (
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
            {projects.map((p) => (
              <ProjectCard
                key={p.id}
                project={p}
                onDelete={async () => {
                  await api.remove(p.id);
                  load();
                }}
              />
            ))}
          </div>
        )}
      </section>
    </div>
  );
}
