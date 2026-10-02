import React from "react";
import { Icon } from "@iconify/react";
import { Link } from "react-router";
import type { MangaImportJob } from "@/features/upload/mangaImportJobs";
import { buildMangaDetailIdUrl } from "@/utils/routeState";

type AsyncAction = () => void | Promise<void>;
export type JobSection = "active" | "queued" | "attention" | "completed";

export const mangaImportJobSection = (job: MangaImportJob): JobSection =>
  job.status === "failed"
    ? "attention"
    : job.status === "completed"
    ? "completed"
    : job.status === "queued"
    ? "queued"
    : "active";

export const MangaImportJobRow: React.FC<{
  job: MangaImportJob;
  onRetry: (id: string) => void | Promise<void>;
  onDismiss: (id: string) => void | Promise<void>;
  onOpen: () => void;
  isActionPending: (key: string) => boolean;
  runAction: (key: string, label: string, action: AsyncAction) => void;
}> = ({ job, onRetry, onDismiss, onOpen, isActionPending, runAction }) => {
  const retryKey = `retry-import:${job.id}`;
  const dismissKey = `dismiss-import:${job.id}`;
  const finished = job.status === "completed";
  const failed = job.status === "failed";
  const statusLabel = finished ? "Completed" : failed ? "Failed" : job.status === "queued" ? "Queued" : "Processing";
  const progress = job.progress ?? (job.totalPages ? Math.round(100 * (job.processedPages || 0) / job.totalPages) : undefined);
  const pageCount = job.totalPages ?? job.fileCount;

  return (
    <article className="job-card relative overflow-hidden rounded-xl border border-zinc-200 bg-zinc-50/70 dark:border-zinc-700 dark:bg-zinc-800/70">
      {(finished || failed) && (
        <button
          type="button"
          onClick={() => runAction(dismissKey, "Dismissing…", () => onDismiss(job.id))}
          disabled={isActionPending(dismissKey)}
          className="absolute right-2 top-2 z-10 inline-flex size-9 items-center justify-center rounded-lg text-zinc-600 hover:bg-zinc-200 disabled:opacity-50 dark:text-zinc-400 dark:hover:bg-zinc-700"
          title={finished ? "Dismiss import summary" : "Remove failed import and staged files"}
          aria-label={finished ? `Dismiss ${job.title}` : `Remove failed import ${job.title}`}
        >
          <Icon icon={isActionPending(dismissKey) ? "carbon:renew" : "carbon:close"} className={`size-4 ${isActionPending(dismissKey) ? "animate-spin" : ""}`} />
        </button>
      )}
      <div className="flex flex-col gap-1.5 px-3 py-2.5">
        <div className="flex min-w-0 items-center gap-1.5 pr-10">
          <Icon icon="carbon:cloud-upload" className="h-3.5 w-3.5 shrink-0 text-emerald-600 dark:text-emerald-400" aria-hidden="true" />
          <span className="min-w-0 flex-1 truncate text-sm font-semibold text-zinc-900 dark:text-zinc-100" title={job.title}>{job.title}</span>
          {finished && job.groupId && (
            <Link
              to={buildMangaDetailIdUrl(job.groupId)}
              onClick={onOpen}
              className="inline-flex min-h-10 min-w-10 shrink-0 items-center justify-center rounded-lg p-2 text-zinc-400 hover:bg-zinc-100 hover:text-indigo-600 dark:hover:bg-zinc-800 dark:hover:text-indigo-400"
              title="Open manga details"
              aria-label={`Open manga details for ${job.title}`}
            >
              <Icon icon="carbon:launch" className="h-4 w-4" />
            </Link>
          )}
        </div>
        <div className="flex items-center gap-2 pl-5 text-xs">
          <span className="inline-flex shrink-0 items-center gap-1 rounded-full bg-emerald-50 px-2 py-0.5 font-semibold text-emerald-700 dark:bg-emerald-950/60 dark:text-emerald-300">Manga upload</span>
          <span className={`inline-flex items-center gap-1 rounded-full px-2 py-0.5 font-medium ${failed ? "bg-rose-100 text-rose-700 dark:bg-rose-950/60 dark:text-rose-300" : finished ? "bg-emerald-100 text-emerald-700 dark:bg-emerald-950/60 dark:text-emerald-300" : job.status === "queued" ? "bg-amber-100 text-amber-700 dark:bg-amber-950/60 dark:text-amber-300" : "bg-indigo-100 text-indigo-700 dark:bg-indigo-950/60 dark:text-indigo-300"}`}>
            {job.status === "processing" && <span className="size-1.5 rounded-full bg-indigo-500 motion-safe:animate-pulse" aria-hidden="true" />}
            {statusLabel}
          </span>
        </div>
      </div>
      <div className="px-3 pb-2.5">
        <div className="h-1.5 overflow-hidden rounded-full bg-zinc-200 dark:bg-zinc-700" role="progressbar" aria-label={`Progress for ${job.title}`} aria-valuemin={0} aria-valuemax={100} aria-valuenow={finished ? 100 : progress}>
          <div
            className={`h-full ${finished ? "bg-emerald-500" : failed ? "w-full bg-rose-500" : progress === undefined ? "w-1/3 bg-indigo-500 motion-safe:animate-pulse" : "bg-indigo-500 transition-[width] duration-300"}`}
            style={finished ? { width: "100%" } : progress === undefined ? undefined : { width: `${progress}%` }}
          />
        </div>
        <div className="mt-1 flex items-start justify-between gap-2 text-[11px] tabular-nums text-zinc-600 dark:text-zinc-400">
          <span>{job.processedPages !== undefined && job.totalPages !== undefined ? `${job.processedPages} of ${job.totalPages} pages` : pageCount !== undefined ? `${pageCount} pages` : "Original manga import"}</span>
          {progress !== undefined && !finished && <span className="shrink-0">{progress}%</span>}
          {job.error && <span className="text-rose-700 dark:text-rose-300" role="status">{job.error}</span>}
        </div>
      </div>
      {failed && (
        <div className="flex flex-wrap items-center gap-1 border-t border-zinc-200 px-2 py-1 dark:border-zinc-700">
          <button
            type="button"
            onClick={() => runAction(retryKey, "Retrying…", () => onRetry(job.id))}
            disabled={isActionPending(retryKey)}
            aria-busy={isActionPending(retryKey)}
            className="inline-flex min-h-9 items-center gap-1.5 rounded-lg px-2.5 text-xs font-medium text-indigo-700 hover:bg-indigo-100 disabled:cursor-wait disabled:opacity-70 dark:text-indigo-300 dark:hover:bg-indigo-950/70"
            aria-label={`Retry manga import ${job.title}`}
          >
            <Icon icon="carbon:renew" className={`h-3.5 w-3.5 ${isActionPending(retryKey) ? "animate-spin" : ""}`} />
            {isActionPending(retryKey) ? "Retrying…" : "Retry"}
          </button>
        </div>
      )}
    </article>
  );
};
