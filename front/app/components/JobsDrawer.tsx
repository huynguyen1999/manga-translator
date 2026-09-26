import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Icon } from "@iconify/react";
import { Link } from "react-router";
import type { FinishedImage, MangaSummary, SummaryJob, TranslationBatch, TranslationSettings, TranslatorKey } from "@/types";
import { BatchCard } from "./TranslatingSection";
import { apiUrl } from "@/utils/api";
import { buildMangaDetailIdUrl, mangaIdForTitle } from "@/utils/routeState";
import { summaryModelOptions } from "@/config";
import { AppOverlayPortal } from "./AppOverlayPortal";
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
