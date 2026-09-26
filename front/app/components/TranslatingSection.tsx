import React, { useMemo, useState } from "react";
import { Icon } from "@iconify/react";
import type { FinishedImage, TranslationBatch, TranslationSettings, TranslatorKey } from "@/types";
import { sortBatchesLatestFirst } from "@/utils/serverBatches";
export { canChangeBatchTranslator, jobThumbnailCandidates, resultFor, sortBatchesLatestFirst } from "@/utils/serverBatches";
import { BatchCard } from "@/features/batches/BatchCard";
export { BatchCard };

type AsyncAction = () => void | Promise<void>;
type ImageSourceType = "original" | "translated";

interface TranslatingSectionProps {
  batches: TranslationBatch[];
  onLoadBatchDetails: (batchId: string) => Promise<void>;
  onPause: (batchId: string) => void | Promise<void>;
  onResume: (batchId: string) => void | Promise<void>;
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
    sourceType?: ImageSourceType,
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
  onNavigate?: () => void;
}

export const TranslatingSection: React.FC<TranslatingSectionProps> = ({
  batches,
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
  onNavigate,
}) => {
  const [pendingActions, setPendingActions] = useState<Record<string, string>>({});
  const [actionError, setActionError] = useState<string | null>(null);

  const isActionPending = (key: string) => Boolean(pendingActions[key]);

  const runAction = (key: string, label: string, action: AsyncAction) => {
    if (isActionPending(key)) return;
    setActionError(null);
    setPendingActions((current) => ({ ...current, [key]: label }));
    void Promise.resolve()
      .then(action)
      .catch((error) => {
        console.warn(`Translation action failed (${key}):`, error);
        setActionError("Couldn’t complete that action. Try again.");
      })
      .finally(() => {
        setPendingActions((current) => {
          const next = { ...current };
          delete next[key];
          return next;
        });
      });
  };

  const visibleBatches = useMemo(
    () => sortBatchesLatestFirst(batches),
    [batches]
  );
  const completedBatches = visibleBatches.filter((batch) => batch.status === "completed");
  const queuedBatches = visibleBatches.filter((batch) => batch.status === "waiting" || batch.status === "paused");

  const activeBatch = useMemo(
    () => visibleBatches.find((batch) => ["processing", "paused", "stopping"].includes(batch.status)),
    [visibleBatches]
  );

  return (
    <section className="translation-controls space-y-3" aria-labelledby="translating-heading">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <div className="flex items-center gap-2">
            <h2 id="translating-heading" className="text-base font-semibold text-zinc-900 dark:text-zinc-100">
              Translating
            </h2>
            <span className="rounded-full bg-zinc-100 px-2 py-0.5 text-xs font-medium text-zinc-600 dark:bg-zinc-800 dark:text-zinc-300">
              {visibleBatches.length} {visibleBatches.length === 1 ? "batch" : "batches"}
            </span>
          </div>
          <p className="mt-1 text-xs text-zinc-500 dark:text-zinc-400">
            {visibleBatches.length > 0
              ? "Prioritized batches run next. The latest submissions appear on top."
              : "New translation batches will appear here."}
          </p>
        </div>

        <div className="flex flex-wrap items-center gap-2">
          <button
            type="button"
            onClick={() => runAction("clear-completed", "Clearing…", () => {
              completedBatches.forEach((batch) => onDismissBatch(batch.id));
            })}
            disabled={completedBatches.length === 0 || isActionPending("clear-completed")}
            aria-busy={isActionPending("clear-completed")}
            className="inline-flex min-h-11 items-center gap-1.5 rounded-lg border border-zinc-200 px-3 py-2 text-xs font-semibold text-zinc-700 hover:bg-zinc-100 disabled:cursor-not-allowed disabled:opacity-50 dark:border-zinc-700 dark:text-zinc-200 dark:hover:bg-zinc-800"
            title="Clear all completed batches"
          >
            {isActionPending("clear-completed") && <Icon icon="carbon:renew" className="h-3.5 w-3.5 animate-spin" />}
            {isActionPending("clear-completed") ? "Clearing…" : "Clear completed"}
          </button>
          <button
            type="button"
            onClick={() => runAction("remove-queued", "Removing…", async () => {
              await Promise.all(queuedBatches.map((batch) => onRemoveBatch(batch.id)));
            })}
            disabled={queuedBatches.length === 0 || isActionPending("remove-queued")}
            aria-busy={isActionPending("remove-queued")}
            className="inline-flex min-h-11 items-center gap-1.5 rounded-lg border border-zinc-200 px-3 py-2 text-xs font-semibold text-zinc-700 hover:bg-zinc-100 disabled:cursor-not-allowed disabled:opacity-50 dark:border-zinc-700 dark:text-zinc-200 dark:hover:bg-zinc-800"
            title="Remove all queued and paused batches"
          >
            {isActionPending("remove-queued") ? <Icon icon="carbon:renew" className="h-3.5 w-3.5 animate-spin" /> : <Icon icon="carbon:trash-can" className="h-3.5 w-3.5" />}
            {isActionPending("remove-queued") ? "Removing…" : "Remove queued"}
          </button>
          {activeBatch && activeBatch.status === "paused" && (
            <button
              type="button"
              onClick={() => runAction(`control:${activeBatch.id}`, "Resuming…", () => onResume(activeBatch.id))}
              disabled={isActionPending(`control:${activeBatch.id}`)}
              aria-busy={isActionPending(`control:${activeBatch.id}`)}
              className="inline-flex min-h-11 items-center gap-1.5 rounded-lg bg-indigo-600 px-3 py-2 text-xs font-semibold text-white hover:bg-indigo-500 disabled:cursor-wait disabled:opacity-70"
            >
              {isActionPending(`control:${activeBatch.id}`) ? <Icon icon="carbon:renew" className="h-3.5 w-3.5 animate-spin" /> : <Icon icon="carbon:play" className="h-3.5 w-3.5" />}
              {isActionPending(`control:${activeBatch.id}`) ? "Resuming…" : "Resume"}
            </button>
          )}
          {activeBatch && activeBatch.status === "processing" && (
            <button
              type="button"
              onClick={() => runAction(`control:${activeBatch.id}`, "Pausing…", () => onPause(activeBatch.id))}
              disabled={isActionPending(`control:${activeBatch.id}`)}
              aria-busy={isActionPending(`control:${activeBatch.id}`)}
              className="inline-flex min-h-11 items-center gap-1.5 rounded-lg border border-zinc-200 px-3 py-2 text-xs font-semibold text-zinc-700 hover:bg-zinc-100 disabled:cursor-wait disabled:opacity-70 dark:border-zinc-700 dark:text-zinc-200 dark:hover:bg-zinc-800"
            >
              {isActionPending(`control:${activeBatch.id}`) ? <Icon icon="carbon:renew" className="h-3.5 w-3.5 animate-spin" /> : <Icon icon="carbon:pause" className="h-3.5 w-3.5" />}
              {isActionPending(`control:${activeBatch.id}`) ? "Pausing…" : "Pause"}
            </button>
          )}
          {activeBatch && (
            <button
              type="button"
              onClick={() => runAction(`stop:${activeBatch.id}`, "Stopping…", () => onRemoveBatch(activeBatch.id))}
              disabled={activeBatch.status === "stopping" || isActionPending(`stop:${activeBatch.id}`)}
              aria-busy={isActionPending(`stop:${activeBatch.id}`)}
              className="inline-flex min-h-11 items-center gap-1.5 rounded-lg border border-rose-200 bg-rose-50 px-3 py-2 text-xs font-semibold text-rose-700 hover:bg-rose-100 disabled:cursor-wait disabled:opacity-70 dark:border-rose-900/60 dark:bg-rose-950/40 dark:text-rose-300 dark:hover:bg-rose-950/70 transition-colors"
              title="Stop and remove batch from process"
            >
              {(activeBatch.status === "stopping" || isActionPending(`stop:${activeBatch.id}`)) ? <Icon icon="carbon:renew" className="h-3.5 w-3.5 animate-spin" /> : <Icon icon="carbon:stop-filled-alt" className="h-3.5 w-3.5" />}
              {(activeBatch.status === "stopping" || isActionPending(`stop:${activeBatch.id}`)) ? "Stopping…" : "Stop"}
            </button>
          )}
        </div>
      </div>

      {actionError && (
        <p role="status" aria-live="polite" className="rounded-lg border border-rose-200 bg-rose-50 px-3 py-2 text-xs font-medium text-rose-700 dark:border-rose-900/60 dark:bg-rose-950/30 dark:text-rose-300">
          {actionError}
        </p>
      )}

      <div className="space-y-3">
        {visibleBatches.length === 0
          ? <div className="rounded-2xl border border-dashed border-zinc-200 px-4 py-6 text-center text-sm text-zinc-500 dark:border-zinc-800 dark:text-zinc-400">
              No translation batches yet.
            </div>
          : visibleBatches.map((batch) => (
            <BatchCard
              key={batch.id}
              batch={batch}
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
              onNavigate={onNavigate}
            />
          ))}
      </div>
    </section>
  );
};
