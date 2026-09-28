import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Icon } from "@iconify/react";
import { Link } from "react-router";
import type { FinishedImage, MangaSummary, SummaryJob, TranslationBatch, TranslationSettings, TranslatorKey } from "@/types";
import { BatchCard } from "./TranslatingSection";
import { apiUrl } from "@/utils/api";
import { buildMangaDetailIdUrl, mangaIdForTitle } from "@/utils/routeState";
import { summaryModelOptions } from "@/config";
import SummaryJobRow from "./SummaryJobRow";

type AsyncAction = () => void | Promise<void>;
type JobSection = "active" | "queued" | "attention" | "completed";

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
  onRemoveItem: (batchId: string, itemId: string) => void | Promise<void>;
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
      images?: FinishedImage[];
      currentIndex?: number;
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
  const summaryCloseRef = useRef<HTMLButtonElement>(null);
  const attachCloseButton = useCallback((element: HTMLButtonElement | null) => {
    closeRef.current = element;
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
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape" && shouldCloseJobsDrawer(event.target, drawerRef.current)) {
        onClose();
      }
    };
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
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
    <>
      <aside ref={drawerRef} aria-labelledby="jobs-drawer-title" className="flex w-full shrink-0 flex-col overflow-hidden rounded-2xl border border-zinc-200 bg-white dark:border-zinc-800 dark:bg-zinc-900 lg:sticky lg:top-20 lg:max-h-[calc(100dvh-6rem)] lg:w-[420px] xl:w-[460px] 2xl:w-[480px]">
        <header className="relative z-20 flex items-center gap-1 border-b border-zinc-200 px-3 py-2.5 dark:border-zinc-800">
          <div className="min-w-0 flex-1 pl-1">
            <h2 id="jobs-drawer-title" className="text-sm font-semibold text-zinc-900 dark:text-zinc-100">Jobs</h2>
            <p className="truncate text-[11px] text-zinc-600 dark:text-zinc-400">Translation · Rerender · Upload · Summary</p>
          </div>
          {controlBatch?.status === "paused" && <button type="button" onClick={() => runAction(`control:${controlBatch.id}`, "Resuming…", () => onResume(controlBatch.id))} disabled={isActionPending(`control:${controlBatch.id}`)} className="inline-flex size-9 shrink-0 items-center justify-center rounded-lg text-indigo-600 hover:bg-indigo-50 disabled:opacity-50 dark:text-indigo-400 dark:hover:bg-indigo-950/50" aria-label={`Resume ${controlBatch.mangaTitle}`} title="Resume job"><Icon icon="carbon:play" className="size-4" /></button>}
          {controlBatch?.status === "processing" && <button type="button" onClick={() => runAction(`control:${controlBatch.id}`, "Pausing…", () => onPause(controlBatch.id))} disabled={isActionPending(`control:${controlBatch.id}`)} className="inline-flex size-9 shrink-0 items-center justify-center rounded-lg text-zinc-600 hover:bg-zinc-100 disabled:opacity-50 dark:text-zinc-400 dark:hover:bg-zinc-800" aria-label={`Pause ${controlBatch.mangaTitle}`} title="Pause job"><Icon icon="carbon:pause" className="size-4" /></button>}
          {controlSummary?.status === "paused" && onResumeSummary && <button type="button" onClick={() => runAction(`control-summary:${controlSummary.id}`, "Resuming…", () => onResumeSummary(controlSummary))} disabled={isActionPending(`control-summary:${controlSummary.id}`)} className="inline-flex size-9 shrink-0 items-center justify-center rounded-lg text-indigo-600 hover:bg-indigo-50 disabled:opacity-50 dark:text-indigo-400 dark:hover:bg-indigo-950/50" aria-label={`Resume summary for ${controlSummary.title}`} title="Resume job"><Icon icon="carbon:play" className="size-4" /></button>}
          {controlSummary?.status === "generating" && onPauseSummary && <button type="button" onClick={() => runAction(`control-summary:${controlSummary.id}`, "Pausing…", () => onPauseSummary(controlSummary))} disabled={isActionPending(`control-summary:${controlSummary.id}`)} className="inline-flex size-9 shrink-0 items-center justify-center rounded-lg text-zinc-600 hover:bg-zinc-100 disabled:opacity-50 dark:text-zinc-400 dark:hover:bg-zinc-800" aria-label={`Pause summary for ${controlSummary.title}`} title="Pause job"><Icon icon="carbon:pause" className="size-4" /></button>}
          {controlBatch && <button type="button" onClick={() => runAction(`stop:${controlBatch.id}`, "Stopping…", () => onRemoveBatch(controlBatch.id))} disabled={controlBatch.status === "stopping" || isActionPending(`stop:${controlBatch.id}`)} className="inline-flex size-9 shrink-0 items-center justify-center rounded-lg text-rose-600 hover:bg-rose-50 disabled:opacity-50 dark:text-rose-400 dark:hover:bg-rose-950/40" aria-label={`Stop ${controlBatch.mangaTitle}`} title="Stop job"><Icon icon="carbon:stop" className="size-4" /></button>}
          {controlSummary && onStopSummary && <button type="button" onClick={() => runAction(`stop-summary:${controlSummary.id}`, "Stopping…", () => onStopSummary(controlSummary))} disabled={isActionPending(`stop-summary:${controlSummary.id}`)} className="inline-flex size-9 shrink-0 items-center justify-center rounded-lg text-rose-600 hover:bg-rose-50 disabled:opacity-50 dark:text-rose-400 dark:hover:bg-rose-950/40" aria-label={`Stop summary for ${controlSummary.title}`} title="Stop job"><Icon icon="carbon:stop" className="size-4" /></button>}
          <details className="group relative shrink-0">
            <summary className="flex size-9 cursor-pointer list-none items-center justify-center rounded-lg text-zinc-600 hover:bg-zinc-100 dark:text-zinc-400 dark:hover:bg-zinc-800 [&::-webkit-details-marker]:hidden" aria-label="More job actions" title="More job actions"><Icon icon="carbon:overflow-menu-horizontal" className="size-4" /></summary>
            <div className="absolute right-0 top-10 z-30 w-48 rounded-xl border border-zinc-200 bg-white p-1 shadow-lg dark:border-zinc-700 dark:bg-zinc-800" onClick={(event) => { if ((event.target as HTMLElement).closest("button")) event.currentTarget.parentElement?.removeAttribute("open"); }}>
              <button type="button" onClick={() => runAction("clear-completed", "Clearing…", async () => { completedBatches.forEach((batch) => onDismissBatch(batch.id)); await Promise.all(completedSummaries.map((job) => onDismissSummary(job))); })} disabled={!completedBatches.length && !completedSummaries.length || isActionPending("clear-completed")} className="w-full rounded-lg px-3 py-2 text-left text-xs text-zinc-700 hover:bg-zinc-100 disabled:opacity-40 dark:text-zinc-200 dark:hover:bg-zinc-700">Clear completed</button>
              <button type="button" onClick={() => runAction("clear-attention", "Clearing…", async () => { attentionBatches.forEach((batch) => onDismissBatch(batch.id)); await Promise.all(attentionSummaries.map((job) => onDismissSummary(job))); })} disabled={!attentionBatches.length && !attentionSummaries.length || isActionPending("clear-attention")} className="w-full rounded-lg px-3 py-2 text-left text-xs text-zinc-700 hover:bg-zinc-100 disabled:opacity-40 dark:text-zinc-200 dark:hover:bg-zinc-700">Clear needs-attention</button>
              <button type="button" onClick={() => runAction("remove-queued", "Removing…", async () => { await Promise.all(queuedBatches.map((batch) => onRemoveBatch(batch.id))); await Promise.all(queuedSummaries.map((job) => onDismissSummary(job))); })} disabled={!queuedBatches.length && !queuedSummaries.length || isActionPending("remove-queued")} className="w-full rounded-lg px-3 py-2 text-left text-xs text-rose-600 hover:bg-rose-50 disabled:opacity-40 dark:text-rose-400 dark:hover:bg-rose-950/40">Remove queued</button>
            </div>
          </details>
          <button ref={attachCloseButton} type="button" onClick={onClose} className="inline-flex size-9 shrink-0 items-center justify-center rounded-lg text-zinc-600 hover:bg-zinc-100 dark:text-zinc-400 dark:hover:bg-zinc-800" aria-label="Hide jobs sidebar" title="Hide jobs sidebar"><Icon icon="carbon:close" className="size-4" /></button>
        </header>

        {actionError && <p role="alert" className="mx-4 mt-3 rounded-lg border border-rose-200 bg-rose-50 px-3 py-2 text-xs text-rose-700 dark:border-rose-900/60 dark:bg-rose-950/30 dark:text-rose-300 sm:mx-6">{actionError}</p>}
        <div className="min-h-0 flex-1 overflow-y-auto px-2.5 py-3">
          {(Object.keys(sectionLabels) as JobSection[]).map((section) => {
            const entries = groups[section];
            if (!entries.length) return null;
            const isCollapsed = Boolean(collapsedSections[section]);
            return (
              <section key={section} className="mb-4 last:mb-0" aria-labelledby={`jobs-${section}`}>
                <div className="mb-1.5 flex items-center justify-between px-1">
                  <button
                    type="button"
                    onClick={() => setCollapsedSections((prev) => ({ ...prev, [section]: !prev[section] }))}
                    aria-expanded={!isCollapsed}
                    className="flex flex-1 items-center justify-between rounded-lg px-1 py-1 text-left hover:bg-zinc-100 dark:hover:bg-zinc-800"
                  >
                    <span className="flex items-center gap-1.5">
                      <Icon
                        icon={isCollapsed ? "carbon:chevron-right" : "carbon:chevron-down"}
                        className="h-3.5 w-3.5 text-zinc-400 dark:text-zinc-500"
                        aria-hidden="true"
                      />
                      <span id={`jobs-${section}`} className="text-xs font-medium text-zinc-600 dark:text-zinc-400">
                        {sectionLabels[section]}
                      </span>
                    </span>
                    <span className="text-xs tabular-nums text-zinc-600 dark:text-zinc-400">
                      {entries.length}
                    </span>
                  </button>
                </div>
                {!isCollapsed && (
                  <div className="space-y-2">
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
          {!Object.values(groups).some((entries) => entries.length) && <div className="rounded-xl border border-dashed border-zinc-200 px-4 py-10 text-center text-sm text-zinc-600 dark:border-zinc-700 dark:text-zinc-400">No jobs yet. New translation and summary work will appear here.</div>}
        </div>
      </aside>

      {summaryModal && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-xs p-4 isolate" onClick={() => setSummaryModal(null)}>
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
    </>
  );
};
