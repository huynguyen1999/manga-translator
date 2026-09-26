import React, { useState } from "react";
import { Icon } from "@iconify/react";
import type { SummaryJob } from "@/types";
import { summaryJobProgress } from "@/utils/summaryJobs";
import { summaryModelOptions } from "@/config";
import { RenderProfiler } from "@/utils/renderPerformance";

type AsyncAction = () => void | Promise<void>;

const stages = [
  ["detecting", "Detection"],
  ["ocr", "OCR"],
  ["textline_merge", "Merge text lines"],
  ["concatenating", "Combine transcript"],
  ["summarizing", "Generate synopsis"],
] as const;

const SummaryJobRow: React.FC<{
  job: SummaryJob;
  onDismiss: (job: SummaryJob) => void | Promise<void>;
  onRetry: (job: SummaryJob, summaryModel: string, refreshText?: boolean) => void | Promise<void>;
  onPause?: (job: SummaryJob) => void | Promise<void>;
  onResume?: (job: SummaryJob) => void | Promise<void>;
  onStop?: (job: SummaryJob) => void | Promise<void>;
  onOpen: (job: SummaryJob) => void;
  isActionPending: (key: string) => boolean;
  runAction: (key: string, label: string, action: AsyncAction) => void;
}> = React.memo(({ job, onDismiss, onRetry, onPause, onResume, onStop, onOpen, isActionPending, runAction }) => {
  const [expanded, setExpanded] = useState(job.status === "generating" || job.status === "error");
  const [summaryModel, setSummaryModel] = useState(() => {
    const provider = job.provider?.toLowerCase();
    const model = job.model?.toLowerCase() || "";
    if (provider === "gemini" || model.includes("gemini")) return "gemini";
    if (provider === "groq" || model.includes("groq")) return "groq";
    return "deepseek-flash";
  });
  const stageIndex = Math.max(0, stages.findIndex(([stage]) => stage === job.jobStage));
  const progress = summaryJobProgress(job);
  const extractionRequired = job.jobExtractionRequired !== false;

  return (
    <RenderProfiler id={`SummaryJob:${job.id}`}>
    <article className="job-card job-offscreen-row relative overflow-hidden rounded-xl border border-zinc-200 bg-white dark:border-zinc-800 dark:bg-zinc-900">
      <button
        type="button"
        onClick={() => setExpanded((value) => !value)}
        className="flex w-full items-start gap-3 p-4 pr-12 text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-indigo-500"
        aria-expanded={expanded}
      >
        <Icon icon={expanded ? "carbon:chevron-down" : "carbon:chevron-right"} className="mt-0.5 h-4 w-4 shrink-0 text-zinc-400" aria-hidden="true" />
        <span className="min-w-0 flex-1">
          <span className="block truncate text-sm font-semibold text-zinc-900 dark:text-zinc-100">{job.title}</span>
          <span className="mt-1 flex flex-wrap items-center gap-2 text-xs text-zinc-500 dark:text-zinc-400">
            <span className="rounded-full bg-indigo-100 px-2 py-0.5 font-semibold text-indigo-700 dark:bg-indigo-950/70 dark:text-indigo-300">Summary</span>
            <span>
              {job.status === "queued"
                ? "Queued"
                : job.status === "paused"
                ? "Paused"
                : job.status === "generating"
                ? "In progress"
                : job.status === "ready"
                ? "Complete"
                : "Failed"}
            </span>
            {job.model && <span className="text-zinc-400 dark:text-zinc-500">· {job.model}</span>}
          </span>
        </span>
        <span className="shrink-0 text-xs font-semibold tabular-nums text-indigo-600 dark:text-indigo-300">{progress}%</span>
      </button>
      <button
        type="button"
        onClick={(event) => {
          event.stopPropagation();
          runAction(`dismiss-summary:${job.id}`, "Clearing…", () => onDismiss(job));
        }}
        disabled={isActionPending(`dismiss-summary:${job.id}`)}
        className="absolute right-2 top-2 inline-flex min-h-10 min-w-10 items-center justify-center rounded-lg p-2 text-zinc-400 hover:bg-zinc-100 hover:text-zinc-700 disabled:cursor-wait disabled:opacity-60 dark:hover:bg-zinc-800 dark:hover:text-zinc-200"
        title="Dismiss summary job"
        aria-label={`Dismiss summary job for ${job.title}`}
      >
        <Icon icon={isActionPending(`dismiss-summary:${job.id}`) ? "carbon:renew" : "carbon:close"} className={`h-4 w-4 ${isActionPending(`dismiss-summary:${job.id}`) ? "animate-spin" : ""}`} />
      </button>

      <div className="px-4 pb-4 pl-11">
        <div className="h-1.5 overflow-hidden rounded-full bg-zinc-100 dark:bg-zinc-800" role="progressbar" aria-label={`Summary progress for ${job.title}`} aria-valuemin={0} aria-valuemax={100} aria-valuenow={progress}>
          <div className={`h-full rounded-full bg-indigo-500 transition-[width] duration-300 ${job.status === "generating" && !job.jobProgress ? "animate-pulse" : ""}`} style={{ width: `${progress}%` }} />
        </div>
        {job.jobMessage && <p className="mt-2 text-xs leading-relaxed text-zinc-600 dark:text-zinc-300" aria-live="polite">{job.jobMessage}</p>}
        {job.status === "error" && !expanded && (
          <div className="mt-3 flex flex-wrap items-center gap-2">
            <button
              type="button"
              onClick={(event) => {
                event.stopPropagation();
                runAction(`retry-summary:${job.id}`, "Retrying…", () => onRetry(job, summaryModel, false));
              }}
              disabled={isActionPending(`retry-summary:${job.id}`) || isActionPending(`retry-summary-start:${job.id}`)}
              className="inline-flex min-h-8 items-center gap-1.5 rounded-lg bg-indigo-600 px-2.5 py-1 text-xs font-semibold text-white hover:bg-indigo-500 disabled:opacity-60"
              title="Retry generating summary using cached text"
            >
              <Icon icon={isActionPending(`retry-summary:${job.id}`) ? "carbon:renew" : "carbon:renew"} className={`h-3.5 w-3.5 ${isActionPending(`retry-summary:${job.id}`) ? "animate-spin" : ""}`} />
              <span>{isActionPending(`retry-summary:${job.id}`) ? "Retrying…" : "Retry"}</span>
            </button>
            <button
              type="button"
              onClick={(event) => {
                event.stopPropagation();
                runAction(`retry-summary-start:${job.id}`, "Retrying from start…", () => onRetry(job, summaryModel, true));
              }}
              disabled={isActionPending(`retry-summary:${job.id}`) || isActionPending(`retry-summary-start:${job.id}`)}
              className="inline-flex min-h-8 items-center gap-1.5 rounded-lg border border-indigo-200 bg-indigo-50 px-2.5 py-1 text-xs font-semibold text-indigo-700 hover:bg-indigo-100 disabled:opacity-60 dark:border-indigo-800 dark:bg-indigo-950/50 dark:text-indigo-300 dark:hover:bg-indigo-900/60"
              title="Run OCR and text extraction from scratch from the beginning, then generate summary"
            >
              <Icon icon={isActionPending(`retry-summary-start:${job.id}`) ? "carbon:renew" : "carbon:reset"} className={`h-3.5 w-3.5 ${isActionPending(`retry-summary-start:${job.id}`) ? "animate-spin" : ""}`} />
              <span>{isActionPending(`retry-summary-start:${job.id}`) ? "Retrying from start…" : "Retry from beginning"}</span>
            </button>
          </div>
        )}
      </div>

      {expanded && (
        <div className="border-t border-zinc-200 px-4 py-4 pl-11 dark:border-zinc-800">
          <ol className="space-y-2" aria-label="Summary progress steps">
            {stages.map(([stage, label], index) => {
              const complete = job.status === "ready" || index < stageIndex || (!extractionRequired && index < 3);
              const current = (job.status === "generating" || job.status === "paused") && index === stageIndex;
              return (
                <li key={stage} className={`flex items-center gap-2 text-xs ${complete ? "text-indigo-600 dark:text-indigo-300" : current ? "font-semibold text-zinc-900 dark:text-zinc-100" : "text-zinc-400 dark:text-zinc-600"}`}>
                  <span className={`flex h-5 w-5 items-center justify-center rounded-full border text-xs ${complete ? "border-indigo-500 bg-indigo-50 dark:bg-indigo-950/70" : current ? "border-indigo-500" : "border-zinc-300 dark:border-zinc-700"}`}>
                    {complete ? <Icon icon="carbon:checkmark" className="h-3.5 w-3.5" /> : index + 1}
                  </span>
                  <span className="flex min-w-0 flex-1 flex-wrap items-center gap-x-2 gap-y-1">
                    <span>{!extractionRequired && index < 3 ? `${label} · cached` : label}</span>
                    {current && index < 3 && job.jobStagePassedCount != null && job.jobPageCount != null && (
                      <span className="tabular-nums text-indigo-600 dark:text-indigo-300">
                        {job.jobStagePassedCount}/{job.jobPageCount} pages done
                      </span>
                    )}
                  </span>
                </li>
              );
            })}
          </ol>
          {job.jobPageCount ? (
            <p className="mt-3 text-xs text-zinc-500 dark:text-zinc-400">
              {job.jobPagesWithText ?? 0} of {job.jobPageCount} pages have text
            </p>
          ) : null}
          {job.jobError && <p role="alert" className="mt-3 rounded-lg border border-rose-200 bg-rose-50 px-3 py-2 text-xs text-rose-700 dark:border-rose-900/60 dark:bg-rose-950/30 dark:text-rose-300">{job.jobError}</p>}
          {job.status === "error" && (
            <label className="mt-4 flex items-center gap-2 text-xs text-zinc-600 dark:text-zinc-300">
              <span className="font-semibold">Retry with</span>
              <select
                aria-label={`Summary translator for ${job.title}`}
                value={summaryModel}
                onChange={(event) => setSummaryModel(event.target.value)}
                disabled={isActionPending(`retry-summary:${job.id}`)}
                className="min-h-9 rounded-lg border border-zinc-200 bg-white px-2 text-xs text-zinc-700 focus:border-indigo-500 focus:outline-none focus:ring-1 focus:ring-indigo-500 dark:border-zinc-700 dark:bg-zinc-800 dark:text-zinc-200"
              >
                {summaryModelOptions.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}
              </select>
            </label>
          )}
          <div className="mt-4 flex flex-wrap gap-2">
            {job.status === "ready" && (
              <button type="button" onClick={() => onOpen(job)} className="inline-flex min-h-10 items-center gap-1.5 rounded-lg bg-indigo-600 px-3 py-2 text-xs font-semibold text-white hover:bg-indigo-500 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500">
                <Icon icon="carbon:document-sentiment" className="h-3.5 w-3.5" /> Open summary
              </button>
            )}
            {job.status === "generating" && onPause && (
              <button type="button" onClick={() => runAction(`pause-summary:${job.id}`, "Pausing…", () => onPause(job))} disabled={isActionPending(`pause-summary:${job.id}`)} className="inline-flex min-h-10 items-center gap-1.5 rounded-lg border border-zinc-200 px-3 py-2 text-xs font-semibold text-zinc-700 hover:bg-white disabled:opacity-60 dark:border-zinc-700 dark:text-zinc-200 dark:hover:bg-zinc-800">
                <Icon icon={isActionPending(`pause-summary:${job.id}`) ? "carbon:renew" : "carbon:pause"} className={`h-3.5 w-3.5 ${isActionPending(`pause-summary:${job.id}`) ? "animate-spin" : ""}`} /> Pause
              </button>
            )}
            {job.status === "paused" && onResume && (
              <button type="button" onClick={() => runAction(`resume-summary:${job.id}`, "Resuming…", () => onResume(job))} disabled={isActionPending(`resume-summary:${job.id}`)} className="inline-flex min-h-10 items-center gap-1.5 rounded-lg bg-indigo-600 px-3 py-2 text-xs font-semibold text-white hover:bg-indigo-500 disabled:opacity-60">
                <Icon icon={isActionPending(`resume-summary:${job.id}`) ? "carbon:renew" : "carbon:play"} className={`h-3.5 w-3.5 ${isActionPending(`resume-summary:${job.id}`) ? "animate-spin" : ""}`} /> Resume
              </button>
            )}
            {(job.status === "generating" || job.status === "paused" || job.status === "queued") && onStop && (
              <button type="button" onClick={() => runAction(`stop-summary:${job.id}`, "Stopping…", () => onStop(job))} disabled={isActionPending(`stop-summary:${job.id}`)} className="inline-flex min-h-10 items-center gap-1.5 rounded-lg border border-rose-200 bg-rose-50 px-3 py-2 text-xs font-semibold text-rose-700 hover:bg-rose-100 disabled:opacity-60 dark:border-rose-900/60 dark:bg-rose-950/40 dark:text-rose-300">
                <Icon icon={isActionPending(`stop-summary:${job.id}`) ? "carbon:renew" : "carbon:stop-filled-alt"} className={`h-3.5 w-3.5 ${isActionPending(`stop-summary:${job.id}`) ? "animate-spin" : ""}`} /> Stop
              </button>
            )}
            {job.status === "error" && (
              <>
                <button
                  type="button"
                  onClick={() => runAction(`retry-summary:${job.id}`, "Retrying…", () => onRetry(job, summaryModel, false))}
                  disabled={isActionPending(`retry-summary:${job.id}`) || isActionPending(`retry-summary-start:${job.id}`)}
                  className="inline-flex min-h-10 items-center gap-1.5 rounded-lg bg-indigo-600 px-3 py-2 text-xs font-semibold text-white hover:bg-indigo-500 disabled:opacity-60"
                  title="Retry synopsis generation using cached text"
                >
                  <Icon icon={isActionPending(`retry-summary:${job.id}`) ? "carbon:renew" : "carbon:renew"} className={`h-3.5 w-3.5 ${isActionPending(`retry-summary:${job.id}`) ? "animate-spin" : ""}`} />
                  <span>{isActionPending(`retry-summary:${job.id}`) ? "Retrying…" : "Retry"}</span>
                </button>
                <button
                  type="button"
                  onClick={() => runAction(`retry-summary-start:${job.id}`, "Retrying from start…", () => onRetry(job, summaryModel, true))}
                  disabled={isActionPending(`retry-summary:${job.id}`) || isActionPending(`retry-summary-start:${job.id}`)}
                  className="inline-flex min-h-10 items-center gap-1.5 rounded-lg border border-indigo-200 bg-indigo-50 px-3 py-2 text-xs font-semibold text-indigo-700 hover:bg-indigo-100 disabled:opacity-60 dark:border-indigo-800 dark:bg-indigo-950/50 dark:text-indigo-300 dark:hover:bg-indigo-900/60"
                  title="Run OCR and text extraction from scratch from the beginning, then generate summary"
                >
                  <Icon icon={isActionPending(`retry-summary-start:${job.id}`) ? "carbon:renew" : "carbon:reset"} className={`h-3.5 w-3.5 ${isActionPending(`retry-summary-start:${job.id}`) ? "animate-spin" : ""}`} />
                  <span>{isActionPending(`retry-summary-start:${job.id}`) ? "Retrying from start…" : "Retry from beginning"}</span>
                </button>
              </>
            )}
          </div>
        </div>
      )}
    </article>
    </RenderProfiler>
  );
});

export default SummaryJobRow;
