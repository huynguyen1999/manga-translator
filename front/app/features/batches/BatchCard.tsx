import React, { useCallback, useEffect, useMemo, useState } from "react";
import { Icon } from "@iconify/react";
import { Link } from "react-router";
import type { FinishedImage, QueuedImage, TranslationBatch, TranslationSettings, TranslatorKey } from "@/types";
import { validTranslators } from "@/types";
import { getTranslatorName } from "@/utils/getTranslatorName";
import { canChangeBatchTranslator, formatStage, getBatchKind, resultFor } from "@/utils/serverBatches";
import { MANGA_TITLE_MAX_LENGTH } from "@/config";
import { buildMangaDetailIdUrl, mangaIdForTitle } from "@/utils/routeState";
import { RenderProfiler } from "@/utils/renderPerformance";
import { ItemRow } from "@/features/batches/BatchItemRow";
import { BatchActions } from "@/features/batches/BatchActions";
import { VirtualizedJobItems } from "@/features/batches/VirtualizedJobItems";

type AsyncAction = () => void | Promise<void>;
type ImageSourceType = "original" | "translated";

export const BatchCard: React.FC<{
  batch: TranslationBatch;
  onLoadDetails: (batchId: string) => Promise<void>;
  onDismiss: (batchId: string) => void;
  onRemove: (batchId: string) => void | Promise<void>;
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
  isActionPending: (key: string) => boolean;
  runAction: (key: string, label: string, action: AsyncAction) => void;
  onNavigate?: () => void;
}> = React.memo(({
  batch,
  onLoadDetails,
  onDismiss,
  onRemove,
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
  isActionPending,
  runAction,
  onNavigate,
}) => {
  const [expanded, setExpanded] = useState(batch.status === "processing" || batch.status === "error");
  const [detailsLoading, setDetailsLoading] = useState(false);
  const [detailsError, setDetailsError] = useState(false);
  const [clock, setClock] = useState(Date.now());
  const hasDetails = Boolean(batch.detailsLoaded || batch.items.length > 0);
  const onLoadDetailsRef = React.useRef(onLoadDetails);
  onLoadDetailsRef.current = onLoadDetails;
  const handleRetryItem = useCallback((itemId: string, keep?: boolean) => onRetryItem(batch.id, itemId, keep), [batch.id, onRetryItem]);
  const handleRemoveItem = useCallback((itemId: string) => onRemoveItem(batch.id, itemId), [batch.id, onRemoveItem]);
  const handleToggleExcludeColor = useCallback((itemId: string) => onToggleExcludeColor?.(batch.id, itemId), [batch.id, onToggleExcludeColor]);
  const [isEditingTitle, setIsEditingTitle] = useState(false);
  const [titleInput, setTitleInput] = useState(batch.mangaTitle);
  const isPendingStage = (step?: string) => step === "awaiting_translation" || step === "reserved";
  const waiting = hasDetails ? batch.items.filter((item) => item.status === "queued" || isPendingStage(item.step)).length : (batch.queuedCount || 0);
  const processing = hasDetails ? batch.items.filter((item) => item.status === "processing" && !isPendingStage(item.step)).length : (batch.processingCount || 0);
  const hasActiveStage = batch.items.some((item) => item.status === "processing" && item.stepStartedAt);
  const completed = batch.completedCount;
  const failedItems = hasDetails ? batch.items.filter((item) => item.status === "error") : [];
  const failed = hasDetails ? failedItems.length : (batch.failedCount || 0);
  const isUploading = batch.status === "uploading";
  const sourceType: ImageSourceType =
    batch.settings.translator === "none" && batch.settings.inpainter === "original"
      ? "original"
      : "translated";
  const modalImages = useMemo(() => batch.items.flatMap((item): FinishedImage[] => {
    if (item.status !== "finished" && item.status !== "queued" && item.status !== "processing") return [];
    const input = item.inputUrl || (item.file.size > 0 ? item.file : null);
    const result = item.status === "finished" ? resultFor(item) || input : input;
    if (!result) return [];
    return [{
      id: item.id,
      groupId: item.mangaGroupId,
      pageOrder: item.pageOrder,
      sourcePath: item.sourcePath,
      originalName: item.file.name,
      result,
      batchPreviewUrl: item.batchPreviewUrl,
      coverUrl: item.coverUrl,
      detailPreviewUrl: item.detailPreviewUrl,
      readerUrl: item.readerUrl,
      fullUrl: item.fullUrl,
      sourceType: item.status === "finished" ? sourceType : "original",
      inputUrl: item.inputUrl || (item.file.size > 0 ? item.file : null),
      inpaintedUrl: item.folder ? `/result/${item.folder}/inpainted.jpg` : undefined,
      textRegionsUrl: item.folder ? `/result/${item.folder}/text_regions.json` : undefined,
      hasTextRegions: item.status === "finished" && Boolean(item.folder),
      folder: item.folder,
      mangaTitle: item.mangaTitle || batch.mangaTitle,
      finishedAt: item.status === "finished" ? (item.finishedAt || item.stepStartedAt || undefined) : undefined,
      settings: {
        ...batch.settings,
        offlineModel: item.offlineModel || batch.settings.offlineModel,
        geminiModel: item.geminiModel || batch.settings.geminiModel,
      },
    }];
  }), [batch.items, batch.mangaTitle, batch.settings, sourceType]);
  const retryAllActionKey = `retry-all:${batch.id}`;
  const stopActionKey = `stop:${batch.id}`;
  const needsReview = hasDetails ? batch.items.filter((item) => item.needsReview).length : (batch.needsReviewCount || 0);
  const progress = batch.totalItems ? Math.round((completed / batch.totalItems) * 100) : 100;
  const batchKind = getBatchKind(batch);
  const canChangeTranslator = batchKind !== "rerender" && batchKind !== "pipeline-rerun" && canChangeBatchTranslator(batch);
  const canKeepFailedPages = batch.status !== "completed" && !isUploading && batchKind !== "rerender" && batchKind !== "pipeline-rerun";
  const rerunMode = batch.rerunMode;
  const rerunModeLabel = rerunMode === "full" ? "Full" : rerunMode === "translation_typesetting" ? "Retranslation" : rerunMode === "reprocess_text" ? "Reprocess text" : "Typesetting";
  const batchKindLabel = batchKind === "manga-upload" ? "Manga upload" : batchKind === "rerender" ? "Layout rerender" : batchKind === "pipeline-rerun" ? `Pipeline rerun · ${rerunModeLabel}` : "Translation";
  const statusLabel = isUploading ? "Uploading" : batch.status === "processing" ? "Processing" : batch.status === "waiting" ? "Waiting" : batch.status === "paused" ? "Paused" : batch.status === "stopping" ? "Stopping…" : failed ? `${failed} failed` : batch.status === "error" ? "Failed" : "Complete";

  useEffect(() => {
    if (!expanded || !hasActiveStage) return;
    const timer = window.setInterval(() => setClock(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [expanded, hasActiveStage]);

  useEffect(() => {
    setTitleInput(batch.mangaTitle);
  }, [batch.mangaTitle]);

  const handleSaveTitle = () => {
    setIsEditingTitle(false);
    const clean = titleInput.trim() || "Ungrouped";
    if (clean !== batch.mangaTitle) {
      onMangaTitleChange?.(batch.id, clean);
    }
  };

  const handleCancelTitle = () => {
    setIsEditingTitle(false);
    setTitleInput(batch.mangaTitle);
  };

  useEffect(() => {
    if (!expanded || hasDetails) return;
    setDetailsLoading(true);
    setDetailsError(false);
    void onLoadDetailsRef.current(batch.id)
      .catch(() => setDetailsError(true))
      .finally(() => setDetailsLoading(false));
  }, [expanded, hasDetails]);

  return (
    <RenderProfiler id={`BatchCard:${batch.id}`}>
    <article className="job-card job-offscreen-row relative overflow-hidden rounded-xl border border-zinc-200 bg-zinc-50/70 dark:border-zinc-700 dark:bg-zinc-800/70">
      {(batch.status === "completed" || batch.status === "error" || batch.status === "waiting") && (
        <button
          type="button"
          onClick={() => batch.status === "completed" ? onDismiss(batch.id) : onRemove(batch.id)}
          className="absolute right-2 top-2 z-10 inline-flex size-9 items-center justify-center rounded-lg text-zinc-600 hover:bg-zinc-200 dark:text-zinc-400 dark:hover:bg-zinc-700"
          title={
            batch.status === "completed"
              ? "Dismiss batch summary"
              : isUploading
              ? "Cancel upload"
              : batch.status === "error"
              ? "Remove batch"
              : "Remove waiting batch"
          }
          aria-label={
            batch.status === "completed"
              ? "Dismiss batch summary"
              : isUploading
              ? "Cancel upload"
              : batch.status === "error"
              ? `Remove ${batch.mangaTitle}`
              : `Remove waiting batch ${batch.mangaTitle}`
          }
        >
          <Icon icon="carbon:close" className="h-4 w-4" />
        </button>
      )}
      {(batch.status === "processing" || batch.status === "paused" || batch.status === "stopping" || isUploading) && (
        <button type="button" onClick={() => runAction(stopActionKey, isUploading ? "Cancelling…" : "Stopping…", () => onRemove(batch.id))} disabled={batch.status === "stopping" || isActionPending(stopActionKey)} aria-busy={isActionPending(stopActionKey)} className="absolute right-2 top-2 z-10 inline-flex size-9 items-center justify-center rounded-lg text-rose-600 hover:bg-rose-100 disabled:opacity-50 dark:text-rose-400 dark:hover:bg-rose-950/50" title={isUploading ? "Cancel upload" : "Stop job"} aria-label={isUploading ? `Cancel upload ${batch.mangaTitle}` : `Stop ${batch.mangaTitle}`}>
          <Icon icon={isActionPending(stopActionKey) ? "carbon:renew" : "carbon:stop"} className={`size-4 ${isActionPending(stopActionKey) ? "animate-spin" : ""}`} />
        </button>
      )}
      <div className="flex flex-col gap-1.5 px-3 py-2.5">
        {isEditingTitle ? (
          <div className="flex min-w-0 flex-1 flex-col gap-1 pr-10" onClick={(e) => e.stopPropagation()}>
            <div className="flex items-center gap-1.5 min-w-0">
              <input
                type="text"
                value={titleInput}
                autoFocus
                onChange={(event) => setTitleInput(event.target.value)}
                maxLength={MANGA_TITLE_MAX_LENGTH}
                onKeyDown={(event) => {
                  if (event.key === "Enter") {
                    event.preventDefault();
                    handleSaveTitle();
                  } else if (event.key === "Escape") {
                    event.preventDefault();
                    handleCancelTitle();
                  }
                }}
                onBlur={handleSaveTitle}
                className="min-w-0 max-w-sm flex-1 rounded-md border border-indigo-400 bg-white px-2 py-1 text-xs font-semibold text-zinc-900 shadow-xs outline-none focus:ring-1 focus:ring-indigo-500 dark:border-indigo-600 dark:bg-zinc-800 dark:text-zinc-100"
                placeholder="Destination manga name..."
                aria-label="Edit destination manga name"
              />
              <button
                type="button"
                onMouseDown={(e) => e.preventDefault()}
                onClick={handleSaveTitle}
                className="inline-flex min-h-11 min-w-11 items-center justify-center rounded p-1 text-emerald-600 hover:bg-emerald-50 dark:text-emerald-400 dark:hover:bg-emerald-950/40"
                title="Save destination name (Enter)"
                aria-label="Save destination name"
              >
                <Icon icon="carbon:checkmark" className="h-4 w-4" />
              </button>
              <button
                type="button"
                onMouseDown={(e) => e.preventDefault()}
                onClick={handleCancelTitle}
                className="inline-flex min-h-11 min-w-11 items-center justify-center rounded p-1 text-zinc-400 hover:bg-zinc-100 hover:text-zinc-700 dark:hover:bg-zinc-800 dark:hover:text-zinc-200"
                title="Cancel editing (Esc)"
                aria-label="Cancel editing"
              >
                <Icon icon="carbon:close" className="h-4 w-4" />
              </button>
            </div>
            <span className="text-xs text-zinc-500 dark:text-zinc-400">
              {waiting > 0
                ? `Applies to ${waiting} waiting ${waiting === 1 ? "page" : "pages"}.`
                : "No waiting pages to update."}
            </span>
          </div>
        ) : (
          <div className="flex min-w-0 flex-1 items-center gap-1 pr-10">
            <button
              type="button"
              onClick={() => setExpanded((value) => !value)}
              className="flex min-w-0 flex-1 items-center gap-1.5 rounded-md text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500"
              aria-label={expanded ? "Collapse batch" : "Expand batch"}
            >
              <Icon
                icon={expanded ? "carbon:chevron-down" : "carbon:chevron-right"}
                className="h-3.5 w-3.5 shrink-0 text-zinc-500 dark:text-zinc-400"
              />
              <span
                className="min-w-0 flex-1 truncate text-sm font-semibold text-zinc-900 dark:text-zinc-100"
                title={batch.mangaTitle}
              >
                {batch.mangaTitle}
              </span>
            </button>
            {batch.status === "completed" && (
              <Link
                to={buildMangaDetailIdUrl(batch.mangaGroupId || mangaIdForTitle(batch.mangaTitle))}
                onClick={(event) => {
                  event.stopPropagation();
                  onNavigate?.();
                }}
                className="inline-flex min-h-10 min-w-10 shrink-0 items-center justify-center rounded-lg p-2 text-zinc-400 hover:bg-zinc-100 hover:text-indigo-600 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500 dark:hover:bg-zinc-800 dark:hover:text-indigo-400"
                title="Open manga details"
                aria-label={`Open manga details for ${batch.mangaTitle}`}
              >
                <Icon icon="carbon:launch" className="h-4 w-4" />
              </Link>
            )}
            {onMangaTitleChange && (batch.status === "processing" || batch.status === "waiting" || batch.status === "paused") && (
              <button
                type="button"
                onClick={(e) => {
                  e.stopPropagation();
                  setIsEditingTitle(true);
                }}
                className="inline-flex size-8 shrink-0 items-center justify-center rounded-lg text-zinc-500 hover:bg-zinc-200 hover:text-indigo-600 dark:text-zinc-400 dark:hover:bg-zinc-700 dark:hover:text-indigo-300"
                title="Edit destination manga name"
                aria-label={`Edit destination name for ${batch.mangaTitle}`}
              >
                <Icon icon="carbon:edit" className="h-3.5 w-3.5" />
              </button>
            )}
          </div>
        )}

        <div className="flex min-w-0 flex-wrap items-center gap-x-2 gap-y-1 pl-5 text-xs">
          <span className={`inline-flex shrink-0 items-center gap-1 rounded-full px-2 py-0.5 font-semibold ${batchKind === "manga-upload"
            ? "bg-emerald-50 text-emerald-700 dark:bg-emerald-950/60 dark:text-emerald-300"
            : batchKind === "rerender" || batchKind === "pipeline-rerun"
            ? "bg-violet-50 text-violet-700 dark:bg-violet-950/60 dark:text-violet-300"
            : "bg-indigo-50 text-indigo-700 dark:bg-indigo-950/60 dark:text-indigo-300"
          }`} title={`${batchKindLabel} batch`}>
            <Icon icon={batchKind === "manga-upload" ? "carbon:cloud-upload" : batchKind === "rerender" || batchKind === "pipeline-rerun" ? "carbon:reset" : "carbon:translate"} className="h-3 w-3" aria-hidden="true" />
            {batchKindLabel}
          </span>
          {batch.currentStage && ["processing", "paused", "stopping"].includes(batch.status) && (
            <span className="min-w-0 truncate text-zinc-600 dark:text-zinc-400" title={`${formatStage(batch.currentStage)} · ${batch.currentStagePassedCount ?? 0}/${batch.totalItems} pages passed`}>
              {formatStage(batch.currentStage)}
            </span>
          )}
          <span className={`inline-flex items-center gap-1 rounded-full px-2 py-0.5 font-medium ${batch.status === "error" || failed ? "bg-rose-100 text-rose-700 dark:bg-rose-950/60 dark:text-rose-300" : batch.status === "completed" ? "bg-emerald-100 text-emerald-700 dark:bg-emerald-950/60 dark:text-emerald-300" : batch.status === "waiting" ? "bg-amber-100 text-amber-700 dark:bg-amber-950/60 dark:text-amber-300" : "bg-indigo-100 text-indigo-700 dark:bg-indigo-950/60 dark:text-indigo-300"}`}>
            {batch.status === "processing" && <span className="size-1.5 rounded-full bg-indigo-500 motion-safe:animate-pulse" aria-hidden="true" />}
            {statusLabel}
          </span>
          {needsReview > 0 && <span className="font-medium text-amber-700 dark:text-amber-300">{needsReview} needs review</span>}
        </div>
      </div>

      <div className="px-3 pb-2.5">
        <div className="h-1.5 overflow-hidden rounded-full bg-zinc-200 dark:bg-zinc-700" role="progressbar" aria-label={`Progress for ${batch.mangaTitle}`} aria-valuemin={0} aria-valuemax={100} aria-valuenow={isUploading ? undefined : progress}>
          <div
            className={`h-full rounded-full bg-indigo-500 ${isUploading ? "w-1/3 motion-safe:animate-pulse" : "transition-[width] duration-300"}`}
            style={isUploading ? undefined : { width: `${progress}%` }}
          />
        </div>
        <div className="mt-1 flex items-start justify-between gap-2 text-[11px] tabular-nums text-zinc-600 dark:text-zinc-400">
          <span>{isUploading ? `Importing ${batch.totalItems} pages · keep this tab open` : <><b className="font-medium text-zinc-900 dark:text-zinc-100">{completed}</b> of {batch.totalItems} pages{processing > 0 ? ` · ${processing} active` : ""}{waiting > 0 ? ` · ${waiting} waiting` : ""}</>}</span>
          {!isUploading && <span className="shrink-0">{progress}%</span>}
        </div>
      </div>

      {(canChangeTranslator || canKeepFailedPages) && (
        <div className="flex flex-wrap items-center gap-2 border-t border-zinc-200 px-3 py-2 dark:border-zinc-700">
          {canChangeTranslator && <select value={batch.settings.translator} onChange={(event) => onTranslatorChange(batch.id, event.target.value as TranslatorKey)} className="min-h-8 min-w-28 flex-1 rounded-md border border-zinc-300 bg-white px-2 text-xs text-zinc-800 dark:border-zinc-600 dark:bg-zinc-900 dark:text-zinc-200" aria-label={`Translator service for ${batch.mangaTitle}`} title="Applies to pages that have not started yet">
            {validTranslators.map((translator) => <option key={translator} value={translator}>{getTranslatorName(translator)}</option>)}
          </select>}
          {canKeepFailedPages && <label className="inline-flex min-h-8 cursor-pointer items-center gap-1.5 text-[11px] text-zinc-700 dark:text-zinc-300" title="Keep failed pages for editing"><input type="checkbox" checked={Boolean(batch.settings.keepFailedPagesForEditing)} onChange={(event) => onManualReviewChange(batch.id, event.target.checked)} className="size-3.5 accent-indigo-600" />Keep failed pages</label>}
        </div>
      )}
      <div className="flex flex-wrap items-center gap-1 border-t border-zinc-200 px-2 py-1 empty:hidden dark:border-zinc-700">
        <BatchActions batch={batch} failedItems={failedItems} failed={failed} hasDetails={hasDetails} retryAllActionKey={retryAllActionKey} isActionPending={isActionPending} runAction={runAction} onRetryItem={onRetryItem} onPriorityChange={onPriorityChange} />
      </div>

      {expanded && !hasDetails && (
        <div className="border-t border-zinc-100 px-4 py-3 text-xs text-zinc-500 dark:border-zinc-800 dark:text-zinc-400">
          {detailsLoading ? (
            <span className="inline-flex items-center gap-2">
              <Icon icon="carbon:renew" className="h-3.5 w-3.5 animate-spin" />
              Loading pages…
            </span>
          ) : detailsError ? (
            <button
              type="button"
              onClick={() => {
                setDetailsError(false);
                setDetailsLoading(true);
                void onLoadDetails(batch.id)
                  .catch(() => setDetailsError(true))
                  .finally(() => setDetailsLoading(false));
              }}
              className="font-semibold text-indigo-600 hover:text-indigo-500 dark:text-indigo-400"
            >
              Couldn’t load pages. Retry
            </button>
          ) : null}
        </div>
      )}

      {expanded && hasDetails && batch.items.length > 0 && (
        <VirtualizedJobItems items={batch.items} renderItem={(item) => (
            <ItemRow
              key={item.id}
              item={item}
              now={item.status === "processing" && item.stepStartedAt ? clock : 0}
              batchSettings={batch.settings}
              batchMangaTitle={batch.mangaTitle}
              onRetryItem={handleRetryItem}
              onRemoveItem={handleRemoveItem}
              retryActionKey={`retry:${batch.id}:${item.id}`}
              removeActionKey={`remove-item:${batch.id}:${item.id}`}
              isActionPending={isActionPending}
              runAction={runAction}
              onOpenLightbox={onOpenLightbox}
              onOpenPageEdit={onOpenPageEdit}
              isColorizerActive={isUploading ? false : isColorizerActive}
              onToggleExcludeColor={handleToggleExcludeColor}
              sourceType={sourceType}
              modalImages={modalImages}
            />
        )} />
      )}
    </article>
    </RenderProfiler>
  );
});
