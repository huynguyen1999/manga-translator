import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Icon } from "@iconify/react";
import { Link } from "react-router";
import type { MangaSummary, SummaryJob, TranslationBatch, TranslationSettings, TranslatorKey } from "@/types";
import { BatchCard } from "./TranslatingSection";
import { summaryJobProgress } from "@/utils/summaryJobs";
import { apiUrl } from "@/utils/api";
import { buildMangaDetailIdUrl, mangaIdForTitle } from "@/utils/routeState";
import { summaryModelOptions } from "@/config";
import { AppOverlayPortal } from "./AppOverlayPortal";
import { RenderProfiler } from "@/utils/renderPerformance";

type AsyncAction = () => void | Promise<void>;
type JobSection = "active" | "queued" | "attention" | "completed";

export const shouldCloseJobsDrawer = (eventTarget: EventTarget | null, drawer: HTMLElement | null): boolean =>
  Boolean(drawer && eventTarget && drawer.contains(eventTarget as Node));

interface JobsDrawerProps {
  open: boolean;
  onClose: () => void;
  batches: TranslationBatch[];
  summaryJobs: SummaryJob[];
  onLoadBatchDetails: (id: string) => Promise<void>;
  onPause: (id: string) => void | Promise<void>;
  onResume: (id: string) => void | Promise<void>;
  onDismissBatch: (id: string) => void;
  onRemoveBatch: (id: string) => void | Promise<void>;
  onRetryItem: (batchId: string, itemId: string, keepFailedPagesForEditing?: boolean) => void | Promise<void>;
  onRemoveItem: (batchId: string, itemId: string) => void;
  onTranslatorChange: (batchId: string, translator: TranslatorKey) => void;
  onManualReviewChange: (batchId: string, enabled: boolean) => void;
  onPriorityChange: (batchId: string, priority: boolean) => void | Promise<void>;
  onMangaTitleChange?: (batchId: string, title: string) => void;
  onOpenLightbox?: (
    file: File | string,
    result: Blob | File | string | null,
    onRetry?: () => void | Promise<void>,
    sourceType?: "original" | "translated",
    options?: {
      folder?: string;
      fileName?: string;
      settings?: Partial<TranslationSettings>;
      mangaTitle?: string;
      offlineModel?: string;
      geminiModel?: string;
    },
  ) => void;
  onOpenPageEdit?: (folder: string) => void;
  isColorizerActive?: boolean;
  onToggleExcludeColor?: (batchId: string, itemId: string) => void;
  onDismissSummary: (job: SummaryJob) => void | Promise<void>;
  onRetrySummary: (job: SummaryJob, summaryModel: string, refreshText?: boolean) => void | Promise<void>;
  onPauseSummary?: (job: SummaryJob) => void | Promise<void>;
  onResumeSummary?: (job: SummaryJob) => void | Promise<void>;
  onStopSummary?: (job: SummaryJob) => void | Promise<void>;
}

const stages = [
  ["detecting", "Detection"],
  ["ocr", "OCR"],
  ["textline_merge", "Merge text lines"],
  ["concatenating", "Combine transcript"],
  ["summarizing", "Generate synopsis"],
] as const;

export const batchSection = (batch: TranslationBatch): JobSection => {
  if (batch.status === "error" || (batch.status === "completed" && Boolean(batch.failedCount))) return "attention";
  if (batch.status === "completed") return "completed";
  if (batch.status === "waiting" || batch.status === "paused") return "queued";
  return "active";
};

export const summarySection = (job: SummaryJob): JobSection =>
  job.status === "error"
    ? "attention"
    : job.status === "ready"
    ? "completed"
    : job.status === "queued" || job.status === "paused"
    ? "queued"
    : "active";

export const sectionLabels: Record<JobSection, string> = {
  active: "Active",
  queued: "Queued / Paused",
  attention: "Needs attention",
  completed: "Completed",
};

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
              const current = job.status === "generating" && index === stageIndex;
              return (
                <li key={stage} className={`flex items-center gap-2 text-xs ${complete ? "text-indigo-600 dark:text-indigo-300" : current ? "font-semibold text-zinc-900 dark:text-zinc-100" : "text-zinc-400 dark:text-zinc-600"}`}>
                  <span className={`flex h-5 w-5 items-center justify-center rounded-full border text-xs ${complete ? "border-indigo-500 bg-indigo-50 dark:bg-indigo-950/70" : current ? "border-indigo-500" : "border-zinc-300 dark:border-zinc-700"}`}>
                    {complete ? <Icon icon="carbon:checkmark" className="h-3.5 w-3.5" /> : index + 1}
                  </span>
                  <span>{!extractionRequired && index < 3 ? `${label} · cached` : label}</span>
                </li>
              );
            })}
          </ol>
          {job.jobPageCount ? (
            <p className="mt-3 text-xs text-zinc-500 dark:text-zinc-400">
              {job.jobCurrentPage ?? job.jobPageCount} of {job.jobPageCount} pages · {job.jobPagesWithText ?? 0} with text
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

export const JobsDrawer: React.FC<JobsDrawerProps> = ({
  open,
  onClose,
  batches,
  summaryJobs,
  onLoadBatchDetails,
  onPause,
  onResume,
  onDismissBatch,
  onRemoveBatch,
  onRetryItem,
  onRemoveItem,
  onTranslatorChange,
  onManualReviewChange,
  onPriorityChange,
  onMangaTitleChange,
  onOpenLightbox,
  onOpenPageEdit,
  isColorizerActive,
  onToggleExcludeColor,
  onDismissSummary,
  onRetrySummary,
  onPauseSummary,
  onResumeSummary,
  onStopSummary,
}) => {
  const closeRef = useRef<HTMLButtonElement>(null);
  const drawerRef = useRef<HTMLElement>(null);
  const restoreFocusRef = useRef<HTMLElement | null>(null);
  const summaryCloseRef = useRef<HTMLButtonElement>(null);
  const attachCloseButton = useCallback((element: HTMLButtonElement | null) => {
    closeRef.current = element;
    element?.focus();
  }, []);
  const [, setPendingActions] = useState<Record<string, string>>({});
  const pendingActionsRef = useRef<Record<string, string>>({});
  const [actionError, setActionError] = useState<string | null>(null);
  const [collapsedSections, setCollapsedSections] = useState<Record<JobSection, boolean>>({
    active: false,
    queued: false,
    attention: false,
    completed: false,
  });
  const [summaryModal, setSummaryModal] = useState<{
    job: SummaryJob;
    data: MangaSummary | null;
    loading: boolean;
    error: string | null;
  } | null>(null);
  const summaryRequestRef = useRef(0);

  useEffect(() => {
    if (!open) {
      summaryRequestRef.current += 1;
      setSummaryModal(null);
    }
  }, [open]);

  useEffect(() => {
    if (!open) return;
    restoreFocusRef.current = document.activeElement as HTMLElement | null;
    closeRef.current?.focus();
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        if (shouldCloseJobsDrawer(event.target, drawerRef.current)) onClose();
        return;
      }
      if (event.key !== "Tab") return;
      const focusable = drawerRef.current?.querySelectorAll<HTMLElement>("button, a, input, select, textarea, [tabindex]:not([tabindex='-1'])");
      if (!focusable?.length) return;
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    };
    window.addEventListener("keydown", handleKeyDown);
    return () => {
      window.removeEventListener("keydown", handleKeyDown);
      restoreFocusRef.current?.focus();
    };
  }, [open, onClose]);

  useEffect(() => {
    if (!summaryModal) return;
    summaryCloseRef.current?.focus();
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") setSummaryModal(null);
    };
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [summaryModal]);

  const isActionPending = useCallback((key: string) => Boolean(pendingActionsRef.current[key]), []);
  const runAction = useCallback((key: string, label: string, action: AsyncAction) => {
    if (pendingActionsRef.current[key]) return;
    setActionError(null);
    const started = { ...pendingActionsRef.current, [key]: label };
    pendingActionsRef.current = started;
    setPendingActions(started);
    void Promise.resolve().then(action).catch((error) => {
      console.warn(`Jobs action failed (${key}):`, error);
      setActionError(error instanceof Error ? error.message : "Couldn’t complete that action.");
    }).finally(() => {
      const next = { ...pendingActionsRef.current };
      delete next[key];
      pendingActionsRef.current = next;
      setPendingActions(next);
    });
  }, []);

  const openSummary = useCallback(async (job: SummaryJob) => {
    const requestId = ++summaryRequestRef.current;
    setSummaryModal({ job, data: null, loading: true, error: null });
    try {
      const query = new URLSearchParams({ title: job.title });
      if (job.groupId) query.set("groupId", job.groupId);
      const response = await fetch(apiUrl(`/api/results/group/summary?${query.toString()}`));
      const payload = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(payload.detail || "Could not load the manga summary.");
      if (requestId === summaryRequestRef.current) {
        setSummaryModal({ job, data: payload as MangaSummary, loading: false, error: null });
      }
    } catch (error) {
      if (requestId === summaryRequestRef.current) {
        setSummaryModal({
          job,
          data: null,
          loading: false,
          error: error instanceof Error ? error.message : "Could not load the manga summary.",
        });
      }
    }
  }, []);

  const groups = useMemo(() => {
    const result: Record<JobSection, { type: "batch" | "summary"; value: TranslationBatch | SummaryJob }[]> = { active: [], queued: [], attention: [], completed: [] };
    batches.filter((batch) => !batch.dismissed).forEach((batch) => result[batchSection(batch)].push({ type: "batch", value: batch }));
    summaryJobs.forEach((job) => result[summarySection(job)].push({ type: "summary", value: job }));
    for (const section of Object.keys(result) as JobSection[]) {
      result[section].sort((a, b) => {
        const aTime = a.type === "batch" ? (a.value as TranslationBatch).updatedAt?.getTime() || 0 : Date.parse((a.value as SummaryJob).updatedAt || "") || 0;
        const bTime = b.type === "batch" ? (b.value as TranslationBatch).updatedAt?.getTime() || 0 : Date.parse((b.value as SummaryJob).updatedAt || "") || 0;
        return bTime - aTime;
      });
    }
    result.completed = result.completed.slice(0, 20);
    return result;
  }, [batches, summaryJobs]);

  if (!open) return null;

  const completedBatches = batches.filter((batch) => !batch.dismissed && batchSection(batch) === "completed");
  const completedSummaries = summaryJobs.filter((job) => summarySection(job) === "completed");
  const attentionBatches = batches.filter((batch) => !batch.dismissed && batchSection(batch) === "attention");
  const attentionSummaries = summaryJobs.filter((job) => summarySection(job) === "attention");
  const queuedBatches = batches.filter((batch) => !batch.dismissed && batchSection(batch) === "queued");
  const queuedSummaries = summaryJobs.filter((job) => summarySection(job) === "queued");
  const controlBatch = [...groups.active, ...groups.queued].find((job) => job.type === "batch" && ["processing", "paused", "stopping"].includes((job.value as TranslationBatch).status))?.value as TranslationBatch | undefined;
  const controlSummary = !controlBatch ? [...groups.active, ...groups.queued].find((job) => job.type === "summary" && ["generating", "paused"].includes((job.value as SummaryJob).status))?.value as SummaryJob | undefined : undefined;

  return (
    <AppOverlayPortal>
    <div data-app-overlay="jobs" className="fixed inset-0 z-50" role="presentation">
      <button type="button" aria-label="Close jobs" onClick={onClose} className="absolute inset-0 bg-zinc-950/40" />
      <aside ref={drawerRef} role="dialog" aria-modal="true" aria-labelledby="jobs-drawer-title" className="absolute inset-y-0 right-0 flex w-full max-w-xl flex-col border-l border-zinc-200 bg-zinc-50 shadow-2xl dark:border-zinc-800 dark:bg-zinc-950">
        <div className="flex items-start justify-between gap-4 border-b border-zinc-200 px-4 py-4 dark:border-zinc-800 sm:px-6">
          <div>
            <h2 id="jobs-drawer-title" className="text-lg font-semibold text-zinc-900 dark:text-zinc-100">Jobs</h2>
            <p className="mt-1 text-xs text-zinc-500 dark:text-zinc-400">Translation, layout rerender, manga upload, and summary work</p>
          </div>
          <button ref={attachCloseButton} type="button" onClick={onClose} className="rounded-lg p-2 text-zinc-500 hover:bg-zinc-200 hover:text-zinc-900 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500 dark:hover:bg-zinc-800 dark:hover:text-zinc-100" aria-label="Close jobs">
            <Icon icon="carbon:close" className="h-5 w-5" />
          </button>
        </div>

        <div className="flex flex-wrap items-center gap-2 border-b border-zinc-200 px-4 py-3 dark:border-zinc-800 sm:px-6">
          <button type="button" onClick={() => runAction("clear-completed", "Clearing…", async () => { completedBatches.forEach((batch) => onDismissBatch(batch.id)); await Promise.all(completedSummaries.map((job) => onDismissSummary(job))); })} disabled={!completedBatches.length && !completedSummaries.length} className="inline-flex min-h-10 items-center gap-1.5 rounded-lg border border-zinc-200 px-3 py-2 text-xs font-semibold text-zinc-700 hover:bg-white disabled:cursor-not-allowed disabled:opacity-50 dark:border-zinc-700 dark:text-zinc-200 dark:hover:bg-zinc-900">
            <Icon icon="carbon:checkmark-outline" className="h-3.5 w-3.5" /> Clear completed
          </button>
          <button type="button" onClick={() => runAction("clear-attention", "Clearing…", async () => { attentionBatches.forEach((batch) => onDismissBatch(batch.id)); await Promise.all(attentionSummaries.map((job) => onDismissSummary(job))); })} disabled={!attentionBatches.length && !attentionSummaries.length} className="inline-flex min-h-10 items-center gap-1.5 rounded-lg border border-zinc-200 px-3 py-2 text-xs font-semibold text-zinc-700 hover:bg-white disabled:cursor-not-allowed disabled:opacity-50 dark:border-zinc-700 dark:text-zinc-200 dark:hover:bg-zinc-900">
            <Icon icon="carbon:clean" className="h-3.5 w-3.5" /> Clear attention
          </button>
          <button type="button" onClick={() => runAction("remove-queued", "Removing…", async () => { await Promise.all(queuedBatches.map((batch) => onRemoveBatch(batch.id))); await Promise.all(queuedSummaries.map((job) => onDismissSummary(job))); })} disabled={!queuedBatches.length && !queuedSummaries.length} className="inline-flex min-h-10 items-center gap-1.5 rounded-lg border border-zinc-200 px-3 py-2 text-xs font-semibold text-zinc-700 hover:bg-white disabled:cursor-not-allowed disabled:opacity-50 dark:border-zinc-700 dark:text-zinc-200 dark:hover:bg-zinc-900">
            <Icon icon="carbon:trash-can" className="h-3.5 w-3.5" /> Remove queued
          </button>
          {controlBatch?.status === "paused" && <button type="button" onClick={() => runAction(`control:${controlBatch.id}`, "Resuming…", () => onResume(controlBatch.id))} className="inline-flex min-h-10 items-center gap-1.5 rounded-lg bg-indigo-600 px-3 py-2 text-xs font-semibold text-white hover:bg-indigo-500"><Icon icon="carbon:play" className="h-3.5 w-3.5" /> Resume</button>}
          {controlBatch?.status === "processing" && <button type="button" onClick={() => runAction(`control:${controlBatch.id}`, "Pausing…", () => onPause(controlBatch.id))} className="inline-flex min-h-10 items-center gap-1.5 rounded-lg border border-zinc-200 px-3 py-2 text-xs font-semibold text-zinc-700 hover:bg-white dark:border-zinc-700 dark:text-zinc-200 dark:hover:bg-zinc-900"><Icon icon="carbon:pause" className="h-3.5 w-3.5" /> Pause</button>}
          {controlBatch && <button type="button" onClick={() => runAction(`stop:${controlBatch.id}`, "Stopping…", () => onRemoveBatch(controlBatch.id))} className="inline-flex min-h-10 items-center gap-1.5 rounded-lg border border-rose-200 bg-rose-50 px-3 py-2 text-xs font-semibold text-rose-700 hover:bg-rose-100 dark:border-rose-900/60 dark:bg-rose-950/40 dark:text-rose-300"><Icon icon="carbon:stop-filled-alt" className="h-3.5 w-3.5" /> Stop</button>}
          {controlSummary?.status === "paused" && onResumeSummary && <button type="button" onClick={() => runAction(`control-summary:${controlSummary.id}`, "Resuming…", () => onResumeSummary(controlSummary))} className="inline-flex min-h-10 items-center gap-1.5 rounded-lg bg-indigo-600 px-3 py-2 text-xs font-semibold text-white hover:bg-indigo-500"><Icon icon="carbon:play" className="h-3.5 w-3.5" /> Resume</button>}
          {controlSummary?.status === "generating" && onPauseSummary && <button type="button" onClick={() => runAction(`control-summary:${controlSummary.id}`, "Pausing…", () => onPauseSummary(controlSummary))} className="inline-flex min-h-10 items-center gap-1.5 rounded-lg border border-zinc-200 px-3 py-2 text-xs font-semibold text-zinc-700 hover:bg-white dark:border-zinc-700 dark:text-zinc-200 dark:hover:bg-zinc-900"><Icon icon="carbon:pause" className="h-3.5 w-3.5" /> Pause</button>}
          {controlSummary && onStopSummary && <button type="button" onClick={() => runAction(`stop-summary:${controlSummary.id}`, "Stopping…", () => onStopSummary(controlSummary))} className="inline-flex min-h-10 items-center gap-1.5 rounded-lg border border-rose-200 bg-rose-50 px-3 py-2 text-xs font-semibold text-rose-700 hover:bg-rose-100 dark:border-rose-900/60 dark:bg-rose-950/40 dark:text-rose-300"><Icon icon="carbon:stop-filled-alt" className="h-3.5 w-3.5" /> Stop</button>}
        </div>

        {actionError && <p role="alert" className="mx-4 mt-3 rounded-lg border border-rose-200 bg-rose-50 px-3 py-2 text-xs text-rose-700 dark:border-rose-900/60 dark:bg-rose-950/30 dark:text-rose-300 sm:mx-6">{actionError}</p>}
        <div className="min-h-0 flex-1 overflow-y-auto px-4 py-4 sm:px-6">
          {(Object.keys(sectionLabels) as JobSection[]).map((section) => {
            const entries = groups[section];
            if (!entries.length) return null;
            const isCollapsed = Boolean(collapsedSections[section]);
            return (
              <section key={section} className="mb-6 last:mb-0" aria-labelledby={`jobs-${section}`}>
                <div className="mb-2 flex items-center justify-between">
                  <button
                    type="button"
                    onClick={() => setCollapsedSections((prev) => ({ ...prev, [section]: !prev[section] }))}
                    aria-expanded={!isCollapsed}
                    className="flex flex-1 items-center justify-between rounded-lg p-1.5 -mx-1.5 text-left transition-colors hover:bg-zinc-200/50 dark:hover:bg-zinc-800/50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500"
                  >
                    <span className="flex items-center gap-1.5">
                      <Icon
                        icon={isCollapsed ? "carbon:chevron-right" : "carbon:chevron-down"}
                        className="h-3.5 w-3.5 text-zinc-400 dark:text-zinc-500"
                        aria-hidden="true"
                      />
                      <span id={`jobs-${section}`} className="text-xs font-semibold uppercase tracking-wider text-zinc-500 dark:text-zinc-400">
                        {sectionLabels[section]}
                      </span>
                    </span>
                    <span className="rounded-full bg-zinc-200/70 px-2 py-0.5 text-xs font-semibold tabular-nums text-zinc-600 dark:bg-zinc-800 dark:text-zinc-400">
                      {entries.length}
                    </span>
                  </button>
                  {section === "queued" && (
                    <button
                      type="button"
                      onClick={(e) => {
                        e.stopPropagation();
                        runAction("remove-queued", "Removing…", async () => {
                          await Promise.all(queuedBatches.map((batch) => onRemoveBatch(batch.id)));
                          await Promise.all(queuedSummaries.map((job) => onDismissSummary(job)));
                        });
                      }}
                      className="ml-2 rounded px-2 py-0.5 text-xs font-medium text-rose-600 hover:bg-rose-50 hover:text-rose-700 dark:text-rose-400 dark:hover:bg-rose-950/40 dark:hover:text-rose-300"
                    >
                      Remove all
                    </button>
                  )}
                  {section === "attention" && (
                    <button
                      type="button"
                      onClick={(e) => {
                        e.stopPropagation();
                        runAction("clear-attention", "Clearing…", async () => {
                          attentionBatches.forEach((batch) => onDismissBatch(batch.id));
                          await Promise.all(attentionSummaries.map((job) => onDismissSummary(job)));
                        });
                      }}
                      className="ml-2 rounded px-2 py-0.5 text-xs font-medium text-zinc-500 hover:bg-zinc-200 hover:text-zinc-800 dark:text-zinc-400 dark:hover:bg-zinc-800 dark:hover:text-zinc-200"
                    >
                      Clear all
                    </button>
                  )}
                  {section === "completed" && (
                    <button
                      type="button"
                      onClick={(e) => {
                        e.stopPropagation();
                        runAction("clear-completed", "Clearing…", async () => {
                          completedBatches.forEach((batch) => onDismissBatch(batch.id));
                          await Promise.all(completedSummaries.map((job) => onDismissSummary(job)));
                        });
                      }}
                      className="ml-2 rounded px-2 py-0.5 text-xs font-medium text-zinc-500 hover:bg-zinc-200 hover:text-zinc-800 dark:text-zinc-400 dark:hover:bg-zinc-800 dark:hover:text-zinc-200"
                    >
                      Clear all
                    </button>
                  )}
                </div>
                {!isCollapsed && (
                  <div className="space-y-3">
                    {entries.map((entry) => entry.type === "summary" ? (
                      <SummaryJobRow
                        key={entry.value.id}
                        job={entry.value as SummaryJob}
                        onDismiss={onDismissSummary}
                        onRetry={onRetrySummary}
                        onPause={onPauseSummary}
                        onResume={onResumeSummary}
                        onStop={onStopSummary}
                        onOpen={openSummary}
                        isActionPending={isActionPending}
                        runAction={runAction}
                      />
                    ) : (
                      <BatchCard
                        key={entry.value.id}
                        batch={entry.value as TranslationBatch}
                        onLoadDetails={onLoadBatchDetails}
                        onDismiss={onDismissBatch}
                        onRemove={onRemoveBatch}
                        onRetryItem={onRetryItem}
                        onRemoveItem={onRemoveItem}
                        onTranslatorChange={onTranslatorChange}
                        onManualReviewChange={onManualReviewChange}
                        onPriorityChange={onPriorityChange}
                        onMangaTitleChange={onMangaTitleChange}
                        onOpenLightbox={onOpenLightbox}
                        onOpenPageEdit={onOpenPageEdit}
                        isColorizerActive={isColorizerActive}
                        onToggleExcludeColor={onToggleExcludeColor}
                        isActionPending={isActionPending}
                        runAction={runAction}
                        onNavigate={onClose}
                      />
                    ))}
                  </div>
                )}
              </section>
            );
          })}
          {!Object.values(groups).some((entries) => entries.length) && <div className="rounded-2xl border border-dashed border-zinc-200 px-4 py-12 text-center text-sm text-zinc-500 dark:border-zinc-800 dark:text-zinc-400">No jobs yet.</div>}
        </div>
      </aside>
      {summaryModal && (
        <div className="absolute inset-0 z-10 flex items-center justify-center bg-black/60 p-4 isolate" onClick={() => setSummaryModal(null)}>
          <div
            role="dialog"
            aria-modal="true"
            aria-labelledby="jobs-summary-title"
            className="w-full max-w-2xl overflow-hidden rounded-2xl border border-zinc-200 bg-white shadow-2xl dark:border-zinc-700 dark:bg-zinc-900 transform-gpu"
            onClick={(event) => event.stopPropagation()}
          >
            <div className="flex items-start justify-between gap-4 border-b border-zinc-200 px-5 py-4 dark:border-zinc-800">
              <div className="min-w-0">
                <p className="text-xs font-semibold uppercase tracking-wide text-indigo-600 dark:text-indigo-400">Manga synopsis</p>
                <h3 id="jobs-summary-title" className="mt-1 truncate text-lg font-semibold text-zinc-900 dark:text-zinc-100">{summaryModal.job.title}</h3>
              </div>
              <button ref={summaryCloseRef} type="button" onClick={() => setSummaryModal(null)} className="rounded-lg p-1.5 text-zinc-400 hover:bg-zinc-100 hover:text-zinc-700 dark:hover:bg-zinc-800 dark:hover:text-zinc-200" title="Close summary" aria-label="Close summary">
                <Icon icon="carbon:close" className="h-5 w-5" />
              </button>
            </div>
            <div className="max-h-[60vh] overflow-y-auto px-5 py-5 synopsis-scroll-container overscroll-contain">
              {summaryModal.loading ? (
                <div className="flex items-center justify-center gap-2 py-16 text-sm text-zinc-500 dark:text-zinc-400" aria-live="polite">
                  <Icon icon="carbon:renew" className="h-5 w-5 animate-spin text-indigo-500" /> Loading synopsis…
                </div>
              ) : summaryModal.error ? (
                <p className="rounded-lg border border-rose-200 bg-rose-50 px-3 py-2 text-sm text-rose-700 dark:border-rose-900/60 dark:bg-rose-950/30 dark:text-rose-300" role="alert">{summaryModal.error}</p>
              ) : summaryModal.data?.summary ? (
                <>
                  <p className="whitespace-pre-wrap text-sm leading-7 text-zinc-700 dark:text-zinc-200">{summaryModal.data.summary}</p>
                  <div className="mt-5 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-zinc-500 dark:text-zinc-400">
                    <span>{summaryModal.data.pageCount} pages · {summaryModal.data.textPageCount} with original text</span>
                    {summaryModal.data.language && <span>{summaryModal.data.language}</span>}
                    {summaryModal.data.model && <span>{summaryModal.data.model}</span>}
                  </div>
                </>
              ) : (
                <p className="py-8 text-sm text-zinc-600 dark:text-zinc-300">No synopsis is available yet.</p>
              )}
            </div>
            <div className="flex flex-wrap items-center justify-between gap-2 border-t border-zinc-200 px-5 py-4 dark:border-zinc-800">
              <Link
                to={buildMangaDetailIdUrl(summaryModal.job.groupId || summaryModal.data?.groupId || mangaIdForTitle(summaryModal.job.title))}
                onClick={onClose}
                className="inline-flex min-h-10 items-center gap-1.5 rounded-lg border border-zinc-200 px-3 py-2 text-xs font-semibold text-zinc-700 hover:bg-zinc-100 dark:border-zinc-700 dark:text-zinc-200 dark:hover:bg-zinc-800"
              >
                <Icon icon="carbon:launch" className="h-3.5 w-3.5" /> Open manga details
              </Link>
              <div className="flex flex-wrap items-center gap-2">
                <button
                  type="button"
                  onClick={() => {
                    const targetJob = summaryModal.job;
                    setSummaryModal(null);
                    void onRetrySummary(targetJob, summaryModal.data?.model || summaryModelOptions[0]?.value || "deepseek-flash", false);
                  }}
                  className="inline-flex min-h-10 items-center gap-1.5 rounded-lg bg-indigo-600 px-3 py-2 text-xs font-semibold text-white hover:bg-indigo-500"
                >
                  <Icon icon="carbon:renew" className="h-3.5 w-3.5" /> Retry
                </button>
                <button
                  type="button"
                  onClick={() => {
                    const targetJob = summaryModal.job;
                    setSummaryModal(null);
                    void onRetrySummary(targetJob, summaryModal.data?.model || summaryModelOptions[0]?.value || "deepseek-flash", true);
                  }}
                  className="inline-flex min-h-10 items-center gap-1.5 rounded-lg border border-indigo-200 bg-indigo-50 px-3 py-2 text-xs font-semibold text-indigo-700 hover:bg-indigo-100 dark:border-indigo-800 dark:bg-indigo-950/50 dark:text-indigo-300 dark:hover:bg-indigo-900/60"
                  title="Re-run detection & OCR from scratch, then regenerate synopsis"
                >
                  <Icon icon="carbon:reset" className="h-3.5 w-3.5" /> Retry from beginning
                </button>
                <button type="button" onClick={() => setSummaryModal(null)} className="inline-flex min-h-10 items-center rounded-lg border border-zinc-200 px-3 py-2 text-xs font-semibold text-zinc-700 hover:bg-zinc-100 dark:border-zinc-700 dark:text-zinc-200 dark:hover:bg-zinc-800">Done</button>
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
    </AppOverlayPortal>
  );
};
