import React, { useCallback, useEffect, useMemo, useState } from "react";
import { Icon } from "@iconify/react";
import { Link } from "react-router";
import type { QueuedImage, TranslationBatch, TranslationSettings, TranslatorKey } from "@/types";
import { validTranslators } from "@/types";
import { getTranslatorName } from "@/utils/getTranslatorName";
import { formatStage, formatStageElapsed, getBatchKind } from "@/utils/serverBatches";
import PreviewImage from "./PreviewImage";
import { apiUrl } from "@/utils/api";
import { MANGA_TITLE_MAX_LENGTH } from "@/config";
import { buildMangaDetailIdUrl, mangaIdForTitle } from "@/utils/routeState";
import { RenderProfiler } from "@/utils/renderPerformance";

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
  onRemoveItem: (batchId: string, itemId: string) => void;
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
    },
  ) => void;
  onOpenPageEdit?: (folder: string) => void;
  isColorizerActive?: boolean;
  onToggleExcludeColor?: (batchId: string, itemId: string) => void;
  onNavigate?: () => void;
}

const itemStatusLabel: Record<QueuedImage["status"], string> = {
  queued: "Waiting",
  processing: "Translating",
  finished: "Complete",
  error: "Failed",
};

export const resultFor = (item: QueuedImage) =>
  item.status !== "finished"
    ? null
    : item.result instanceof Blob && item.result.size < 1000 && item.folder
    ? apiUrl(`/result/${item.folder}/final.png`)
    : typeof item.result === "string" ? apiUrl(item.result) : (item.result || (item.folder ? apiUrl(`/result/${item.folder}/final.png`) : null));

const ItemRow: React.FC<{
  item: QueuedImage;
  now: number;
  batchSettings?: Partial<TranslationSettings>;
  batchMangaTitle?: string;
  onRetryItem: (itemId: string, keepFailedPagesForEditing?: boolean) => void | Promise<void>;
  onRemoveItem: (itemId: string) => void;
  retryActionKey: string;
  isActionPending: (key: string) => boolean;
  runAction: (key: string, label: string, action: AsyncAction) => void;
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
    },
  ) => void;
  onOpenPageEdit?: (folder: string) => void;
  isColorizerActive?: boolean;
  onToggleExcludeColor?: (itemId: string) => void;
  sourceType?: ImageSourceType;
}> = React.memo(({
  item,
  now,
  batchSettings,
  batchMangaTitle,
  onRetryItem,
  onRemoveItem,
  retryActionKey,
  isActionPending,
  runAction,
  onOpenLightbox,
  onOpenPageEdit,
  isColorizerActive,
  onToggleExcludeColor,
  sourceType,
}) => {
  const result = resultFor(item);
  const isFinished = item.status === "finished";
  const isProcessing = item.status === "processing";
  const isError = item.status === "error";
  const isAwaitingTranslation = item.step === "awaiting_translation";
  const queuedStatus =
    item.step && !["reserved", "initialize", "starting"].includes(item.step)
      ? `Waiting · Next: ${formatStage(item.step)}`
      : item.step === "reserved"
        ? "Waiting · Batch slot"
        : "Waiting to start";
  const needsReview = isFinished && item.needsReview;
  const previewFile = item.inputUrl || item.file;
  const previewSource = item.inputUrl || (item.file.size > 0 ? item.file : null);
  const canOpenPreview = Boolean(
    onOpenLightbox && previewSource && (isFinished ? result : item.status === "queued" || isProcessing),
  );
  const [downloadUrl, setDownloadUrl] = useState<string | null>(null);

  useEffect(() => {
    if (typeof result === "string") {
      setDownloadUrl(result);
      return;
    }
    if (result instanceof Blob) {
      const url = URL.createObjectURL(result);
      setDownloadUrl(url);
      return () => URL.revokeObjectURL(url);
    }
    setDownloadUrl(null);
  }, [result]);

  return (
  <RenderProfiler id={`JobItem:${item.id}`}>
  <div className={`job-card job-offscreen-row flex flex-wrap items-start gap-3 rounded-xl border bg-white p-3 shadow-2xs dark:bg-zinc-900 ${
      isError
        ? "border-rose-200 dark:border-rose-900/70"
        : isAwaitingTranslation
        ? "border-sky-200 dark:border-sky-900/70"
        : "border-zinc-200 dark:border-zinc-800"
    }`}>
      <button
        type="button"
        className={`group relative h-16 w-12 shrink-0 overflow-hidden rounded-lg border border-zinc-200 bg-zinc-100 dark:border-zinc-800 dark:bg-zinc-950 ${canOpenPreview ? "cursor-zoom-in" : ""}`}
        onClick={() =>
          canOpenPreview &&
          onOpenLightbox?.(
            previewFile,
            isFinished ? result : previewSource,
            isFinished ? () => onRetryItem(item.id) : undefined,
            isFinished ? sourceType : "original",
            {
              folder: item.folder,
              fileName: item.file.name,
              settings: {
                ...batchSettings,
                offlineModel: item.offlineModel || batchSettings?.offlineModel,
                geminiModel: item.geminiModel || batchSettings?.geminiModel,
              },
              mangaTitle: batchMangaTitle,
              offlineModel: item.offlineModel,
              geminiModel: item.geminiModel,
            },
          )
        }
        disabled={!canOpenPreview}
        title={canOpenPreview ? `View ${isFinished && sourceType !== "original" ? "translated" : "original"} page` : undefined}
      >
        <PreviewImage
          file={item.inputUrl || item.file}
          result={result}
          resultLabel={sourceType === "original" ? "Original" : "Translated"}
          viewMode={sourceType === "original" ? "original" : undefined}
          showComparisonControls={false}
          loading="lazy"
          preloadImages={false}
        />
        {canOpenPreview && (
          <span className="pointer-events-none absolute inset-0 flex items-center justify-center bg-black/35 opacity-0 transition-opacity group-hover:opacity-100 group-focus-visible:opacity-100">
            <Icon icon="carbon:zoom-in" className="h-4 w-4 text-white" aria-hidden="true" />
          </span>
        )}
      </button>

      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-center gap-2">
          <span className="truncate text-sm font-medium text-zinc-900 dark:text-zinc-100" title={item.file.name}>
            {item.file.name}
          </span>
          <span className={`text-xs ${
            needsReview
              ? "text-amber-600 dark:text-amber-400"
              : isAwaitingTranslation
              ? "font-medium text-sky-600 dark:text-sky-400"
              : "text-zinc-400 dark:text-zinc-500"
          }`}>
            {needsReview
              ? "Needs review"
              : isAwaitingTranslation
              ? "Awaiting translation"
              : item.status === "queued"
              ? queuedStatus
              : itemStatusLabel[item.status]}
          </span>
        </div>
        <div className="mt-1 flex flex-wrap items-center gap-2 text-xs text-zinc-500 dark:text-zinc-400">
          {isAwaitingTranslation ? (
            <span className="inline-flex items-center gap-1 rounded-full border border-sky-200/80 bg-sky-50 px-2 py-0.5 text-xs font-medium text-sky-700 dark:border-sky-800/60 dark:bg-sky-950/40 dark:text-sky-300">
              <Icon icon="carbon:hourglass" className="h-3 w-3 text-sky-600 dark:text-sky-400" />
              Prepared · Waiting for batch translation
            </span>
          ) : isProcessing && (
            <span>{formatStage(item.step)}{item.stepStartedAt ? ` · ${formatStageElapsed(item.stepStartedAt, now)}` : ""}</span>
          )}
          {isProcessing && item.offlineModel && (
            <span className="truncate" title={item.offlineModel}>
              Offline model: {item.offlineModel}
            </span>
          )}
          {isProcessing && item.geminiModel && (
            <span className="truncate text-indigo-500 dark:text-indigo-400" title={item.geminiModel}>
              Gemini model: {item.geminiModel}
            </span>
          )}
          {isColorizerActive && !isFinished && !isProcessing && onToggleExcludeColor && (
            <button
              type="button"
              onClick={() => onToggleExcludeColor(item.id)}
              className={`rounded-full px-2 py-0.5 text-xs font-medium ${
                item.excludeColor
                  ? "bg-zinc-100 text-zinc-500 dark:bg-zinc-800 dark:text-zinc-400"
                  : "bg-purple-50 text-purple-700 dark:bg-purple-950/40 dark:text-purple-300"
              }`}
            >
              {item.excludeColor ? "No Color" : "Colorize"}
            </button>
          )}
        </div>
        {isError && (
          <div className="mt-2 flex items-start gap-2 rounded-lg border border-rose-200/80 bg-rose-50/80 px-2.5 py-2 dark:border-rose-900/60 dark:bg-rose-950/30">
            <Icon icon="carbon:warning-alt" className="mt-0.5 h-3.5 w-3.5 shrink-0 text-rose-600 dark:text-rose-400" aria-hidden="true" />
            <p className="min-w-0 flex-1 break-words text-xs leading-relaxed text-rose-700 dark:text-rose-300" title={item.error}>
              {item.error || "Translation failed"}
            </p>
          </div>
        )}
      </div>

      <div className={`flex w-full shrink-0 flex-wrap items-center justify-end gap-2 ${isError ? "border-t border-rose-100 pt-3 dark:border-rose-900/50" : "sm:w-auto"}`}>
        {isError && (
          <>
            <button
              type="button"
              onClick={() => runAction(retryActionKey, "Retrying…", () => onRetryItem(item.id))}
              disabled={isActionPending(retryActionKey)}
              aria-busy={isActionPending(retryActionKey)}
              className="order-1 inline-flex min-h-11 items-center gap-1.5 rounded-lg bg-indigo-600 px-3.5 py-2 text-xs font-semibold text-white shadow-sm transition-colors hover:bg-indigo-500 disabled:cursor-wait disabled:opacity-70"
            >
              <Icon icon="carbon:renew" className={`h-3.5 w-3.5 ${isActionPending(retryActionKey) ? "animate-spin" : ""}`} />
              {isActionPending(retryActionKey) ? "Retrying…" : "Retry"}
            </button>
            <button
              type="button"
              onClick={() => runAction(retryActionKey, "Retrying…", () => onRetryItem(item.id, true))}
              disabled={isActionPending(retryActionKey)}
              aria-busy={isActionPending(retryActionKey)}
              className="order-2 inline-flex min-h-11 items-center gap-1.5 rounded-lg border border-amber-300 bg-amber-50 px-3.5 py-2 text-xs font-semibold text-amber-900 transition-colors hover:bg-amber-100 disabled:cursor-wait disabled:opacity-70 dark:border-amber-700/70 dark:bg-amber-950/40 dark:text-amber-200 dark:hover:bg-amber-950/70"
              title="Keep the translated page and mark failed regions for editing"
            >
              <Icon icon="carbon:edit" className="h-3.5 w-3.5" />
              {isActionPending(retryActionKey) ? "Retrying…" : "Pass & edit"}
            </button>
            <button
              type="button"
              onClick={() => onRemoveItem(item.id)}
              className="order-3 inline-flex min-h-11 min-w-11 items-center justify-center rounded-lg p-2 text-rose-600/80 transition-colors hover:bg-rose-50 hover:text-rose-700 dark:text-rose-400/80 dark:hover:bg-rose-950/40 dark:hover:text-rose-300"
              title="Remove failed page"
            >
              <Icon icon="carbon:trash-can" className="h-4 w-4" />
            </button>
          </>
        )}
        {isFinished && downloadUrl && (
          <a
            href={downloadUrl}
            download={`${sourceType === "original" ? "original" : "translated"}_${item.file.name}`}
            className="inline-flex min-h-11 min-w-11 items-center justify-center rounded-lg p-2 text-zinc-400 hover:bg-zinc-100 hover:text-indigo-600 dark:hover:bg-zinc-800 dark:hover:text-indigo-400"
            title={`Download ${sourceType === "original" ? "original" : "translated"} page`}
          >
            <Icon icon="carbon:download" className="h-4 w-4" />
          </a>
        )}
        {needsReview && downloadUrl && (onOpenPageEdit || onOpenLightbox) && (
          <button
            type="button"
            onClick={() => {
              if (onOpenPageEdit && item.folder) {
                onOpenPageEdit(item.folder);
              } else {
                onOpenLightbox?.(item.inputUrl || item.file, result, undefined, undefined, {
                  folder: item.folder,
                  settings: {
                    ...batchSettings,
                    offlineModel: item.offlineModel || batchSettings?.offlineModel,
                    geminiModel: item.geminiModel || batchSettings?.geminiModel,
                  },
                  mangaTitle: batchMangaTitle,
                  offlineModel: item.offlineModel,
                  geminiModel: item.geminiModel,
                });
              }
            }}
            className="inline-flex min-h-11 items-center rounded-lg bg-amber-500 px-3 py-2 text-xs font-semibold text-black hover:bg-amber-400"
          >
            Edit
          </button>
        )}
      </div>
    </div>
    </RenderProfiler>
  );
});

export const BatchCard: React.FC<{
  batch: TranslationBatch;
  onLoadDetails: (batchId: string) => Promise<void>;
  onDismiss: (batchId: string) => void;
  onRemove: (batchId: string) => void | Promise<void>;
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
    sourceType?: ImageSourceType,
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
  const [expanded, setExpanded] = useState(false);
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
  const retryAllActionKey = `retry-all:${batch.id}`;
  const stopActionKey = `stop:${batch.id}`;
  const needsReview = hasDetails ? batch.items.filter((item) => item.needsReview).length : (batch.needsReviewCount || 0);
  const progress = batch.totalItems ? Math.round((completed / batch.totalItems) * 100) : 100;
  const isFinishedSummary = batch.status === "completed" && failed === 0;
  const batchKind = getBatchKind(batch);
  const canChangeTranslator = batchKind !== "rerender" && batchKind !== "pipeline-rerun" && canChangeBatchTranslator(batch);
  const rerunMode = (batch as any).rerunMode || ((batch as any).items?.[0] as any)?.rerunMode;
  const rerunModeLabel = rerunMode === "full" ? "Full" : rerunMode === "translation_typesetting" ? "Retranslation" : rerunMode === "reprocess_text" ? "Reprocess text" : "Typesetting";
  const batchKindLabel = batchKind === "manga-upload" ? "Manga upload" : batchKind === "rerender" ? "Layout rerender" : batchKind === "pipeline-rerun" ? `Pipeline rerun · ${rerunModeLabel}` : "Translation";

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
    <div className="job-card job-offscreen-row relative overflow-hidden rounded-2xl border border-zinc-200 bg-white shadow-xs dark:border-zinc-800 dark:bg-zinc-900">
      {(batch.status === "completed" || isUploading || batch.status === "error" || batch.status === "waiting") && (
        <button
          type="button"
          onClick={() => batch.status === "completed" ? onDismiss(batch.id) : onRemove(batch.id)}
          className="absolute right-3 top-3 z-10 inline-flex min-h-11 min-w-11 items-center justify-center rounded-lg p-2 text-zinc-400 hover:bg-zinc-100 hover:text-zinc-700 dark:hover:bg-zinc-800 dark:hover:text-zinc-200"
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
      <div className="flex flex-col items-stretch gap-3 border-b border-zinc-100 px-4 py-3 dark:border-zinc-800">
        {isEditingTitle ? (
          <div className="flex min-w-0 flex-1 flex-col gap-1 pr-12" onClick={(e) => e.stopPropagation()}>
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
          <div className="flex min-w-0 flex-1 items-center gap-2 pr-12">
            <button
              type="button"
              onClick={() => setExpanded((value) => !value)}
              className="flex min-w-0 flex-1 items-start gap-2 text-left"
              aria-label={expanded ? "Collapse batch" : "Expand batch"}
            >
              <Icon
                icon={expanded ? "carbon:chevron-down" : "carbon:chevron-right"}
                className="mt-0.5 h-4 w-4 shrink-0 text-zinc-400"
              />
              <span
                className="min-w-0 flex-1 break-words text-sm font-semibold text-zinc-900 dark:text-zinc-100"
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
                className="rounded-md p-1 text-zinc-400 hover:bg-zinc-100 hover:text-indigo-600 dark:hover:bg-zinc-800 dark:hover:text-indigo-400 transition-colors"
                title="Edit destination manga name"
                aria-label={`Edit destination name for ${batch.mangaTitle}`}
              >
                <Icon icon="carbon:edit" className="h-3.5 w-3.5" />
              </button>
            )}
          </div>
        )}

        <div className="flex min-w-0 flex-wrap items-center gap-2 pl-6 text-xs">
          <span className={`inline-flex shrink-0 items-center gap-1 rounded-full px-2 py-0.5 font-semibold ${batchKind === "manga-upload"
            ? "bg-emerald-50 text-emerald-700 dark:bg-emerald-950/60 dark:text-emerald-300"
            : batchKind === "rerender" || batchKind === "pipeline-rerun"
            ? "bg-violet-50 text-violet-700 dark:bg-violet-950/60 dark:text-violet-300"
            : "bg-indigo-50 text-indigo-700 dark:bg-indigo-950/60 dark:text-indigo-300"
          }`} title={`${batchKindLabel} batch`}>
            <Icon icon={batchKind === "manga-upload" ? "carbon:cloud-upload" : batchKind === "rerender" || batchKind === "pipeline-rerun" ? "carbon:reset" : "carbon:translate"} className="h-3 w-3" aria-hidden="true" />
            {batchKindLabel}
          </span>
          <span className="shrink-0 text-zinc-500 dark:text-zinc-400">
            {completed}/{batch.totalItems} complete
          </span>
        </div>

        <div className="flex min-w-0 flex-wrap items-center gap-2 text-xs">
          {canChangeTranslator && (
            <label className="inline-flex min-w-0 max-w-full items-center gap-1.5 rounded-lg border border-zinc-200 bg-zinc-50 px-2 py-1 dark:border-zinc-700 dark:bg-zinc-800/80">
              <span className="text-zinc-500 dark:text-zinc-400">Service</span>
              <select
                value={batch.settings.translator}
                onChange={(event) => onTranslatorChange(batch.id, event.target.value as TranslatorKey)}
                className="min-w-0 max-w-40 bg-transparent text-xs font-medium text-zinc-800 outline-none dark:text-zinc-200"
                aria-label={`Translator service for ${batch.mangaTitle}`}
                title="Applies to pages that have not started yet"
              >
                {validTranslators.map((translator) => (
                  <option key={translator} value={translator}>
                    {getTranslatorName(translator)}
                  </option>
                ))}
              </select>
            </label>
          )}
          {batch.status !== "completed" && !isUploading && batchKind !== "rerender" && batchKind !== "pipeline-rerun" && (

            <label className="inline-flex min-w-0 max-w-full items-center gap-1.5 rounded-lg border border-amber-200 bg-amber-50 px-2 py-1 text-amber-800 dark:border-amber-900/60 dark:bg-amber-950/30 dark:text-amber-200">
              <input
                type="checkbox"
                checked={Boolean(batch.settings.keepFailedPagesForEditing)}
                onChange={(event) => onManualReviewChange(batch.id, event.target.checked)}
                className="accent-amber-500"
              />
              <span>Keep failed pages for editing</span>
            </label>
          )}
          {batch.status === "processing" && (
            <span className="inline-flex items-center gap-1.5 rounded-full bg-indigo-50 px-2.5 py-1 font-medium text-indigo-700 dark:bg-indigo-950/60 dark:text-indigo-300">
              <Icon icon="carbon:renew" className="h-3 w-3 animate-spin" />
              {processing ? `${processing} active` : "Processing"}
            </span>
          )}
          {isUploading && (
            <span className="inline-flex items-center gap-1.5 rounded-full bg-indigo-50 px-2.5 py-1 font-medium text-indigo-700 dark:bg-indigo-950/60 dark:text-indigo-300">
              <Icon icon="carbon:renew" className="h-3 w-3 animate-spin" />
              Preparing originals for Gallery
            </span>
          )}
          {batch.status === "waiting" && (
            <span className="rounded-full bg-amber-50 px-2.5 py-1 font-medium text-amber-700 dark:bg-amber-950/60 dark:text-amber-300">
              Waiting
            </span>
          )}
          {batch.status === "paused" && (
            <span className="rounded-full bg-zinc-100 px-2.5 py-1 font-medium text-zinc-600 dark:bg-zinc-800 dark:text-zinc-300">
              Paused
            </span>
          )}
          {batch.status === "stopping" && (
            <span className="inline-flex items-center gap-1.5 rounded-full bg-rose-50 px-2.5 py-1 font-medium text-rose-700 dark:bg-rose-950/50 dark:text-rose-300">
              <Icon icon="carbon:renew" className="h-3 w-3 animate-spin" />
              Stopping…
            </span>
          )}
          {batch.status === "error" && (
            <span className="rounded-full bg-rose-50 px-2.5 py-1 font-medium text-rose-700 dark:bg-rose-950/50 dark:text-rose-300">
              {failed} failed
            </span>
          )}
          {needsReview > 0 && (
            <span className="rounded-full bg-amber-50 px-2.5 py-1 font-medium text-amber-700 dark:bg-amber-950/50 dark:text-amber-300">
              {needsReview} needs review
            </span>
          )}
          {isFinishedSummary && (
            <span className="rounded-full bg-emerald-50 px-2.5 py-1 font-medium text-emerald-700 dark:bg-emerald-950/50 dark:text-emerald-300">
              Complete
            </span>
          )}
          {failed > 0 && hasDetails && (
            <button
              type="button"
              onClick={() => runAction(retryAllActionKey, "Retrying failed pages…", async () => {
                for (const item of failedItems) {
                  await onRetryItem(batch.id, item.id);
                }
              })}
              disabled={isActionPending(retryAllActionKey)}
              aria-busy={isActionPending(retryAllActionKey)}
              className="inline-flex min-h-11 items-center gap-1.5 rounded-lg border border-indigo-200 bg-indigo-50 px-3 py-2 text-xs font-semibold text-indigo-700 hover:bg-indigo-100 disabled:cursor-wait disabled:opacity-70 dark:border-indigo-900/60 dark:bg-indigo-950/40 dark:text-indigo-300 dark:hover:bg-indigo-950/70 transition-colors"
              title={`Retry all ${failed} failed ${failed === 1 ? "page" : "pages"}`}
              aria-label={`Retry all failed pages in ${batch.mangaTitle}`}
            >
              <Icon icon="carbon:renew" className={`h-3.5 w-3.5 ${isActionPending(retryAllActionKey) ? "animate-spin" : ""}`} />
              {isActionPending(retryAllActionKey) ? "Retrying…" : "Retry failed"}
            </button>
          )}
          {(batch.status === "processing" || batch.status === "paused" || batch.status === "stopping") && (
            <button
              type="button"
              onClick={() => runAction(stopActionKey, "Stopping…", () => onRemove(batch.id))}
              disabled={batch.status === "stopping" || isActionPending(stopActionKey)}
              aria-busy={isActionPending(stopActionKey)}
              className="inline-flex min-h-11 items-center gap-1.5 rounded-lg border border-rose-200 bg-rose-50 px-3 py-2 text-xs font-semibold text-rose-700 hover:bg-rose-100 disabled:cursor-wait disabled:opacity-70 dark:border-rose-900/60 dark:bg-rose-950/40 dark:text-rose-300 dark:hover:bg-rose-950/70 transition-colors"
              title="Stop and remove batch from process"
            >
              {(batch.status === "stopping" || isActionPending(stopActionKey)) ? <Icon icon="carbon:renew" className="h-3.5 w-3.5 animate-spin" /> : <Icon icon="carbon:stop-filled-alt" className="h-3.5 w-3.5" />}
              <span>{batch.status === "stopping" || isActionPending(stopActionKey) ? "Stopping…" : "Stop"}</span>
            </button>
          )}
          {isUploading && (
            <button
              type="button"
              onClick={() => runAction(`cancel:${batch.id}`, "Cancelling…", () => onRemove(batch.id))}
              disabled={isActionPending(`cancel:${batch.id}`)}
              aria-busy={isActionPending(`cancel:${batch.id}`)}
              className="inline-flex min-h-11 items-center gap-1.5 rounded-lg border border-rose-200 bg-rose-50 px-3 py-2 text-xs font-semibold text-rose-700 hover:bg-rose-100 disabled:cursor-wait disabled:opacity-70 dark:border-rose-900/60 dark:bg-rose-950/40 dark:text-rose-300 dark:hover:bg-rose-950/70 transition-colors"
              title="Cancel and remove upload"
            >
              {isActionPending(`cancel:${batch.id}`) ? <Icon icon="carbon:renew" className="h-3.5 w-3.5 animate-spin" /> : <Icon icon="carbon:close" className="h-3.5 w-3.5" />}
              <span>{isActionPending(`cancel:${batch.id}`) ? "Cancelling…" : "Cancel upload"}</span>
            </button>
          )}
          {batch.status === "waiting" && (
            <button
              type="button"
              onClick={() => runAction(`priority:${batch.id}`, batch.priority ? "Removing priority…" : "Prioritizing…", () => onPriorityChange(batch.id, !batch.priority))}
              disabled={isActionPending(`priority:${batch.id}`)}
              aria-busy={isActionPending(`priority:${batch.id}`)}
              className={`inline-flex min-h-11 items-center gap-1.5 rounded-lg border px-3 py-2 text-xs font-semibold transition-colors disabled:cursor-wait disabled:opacity-70 ${batch.priority
                ? "border-indigo-200 bg-indigo-50 text-indigo-700 hover:bg-indigo-100 dark:border-indigo-900/60 dark:bg-indigo-950/40 dark:text-indigo-300 dark:hover:bg-indigo-950/70"
                : "border-zinc-200 text-zinc-600 hover:bg-zinc-100 dark:border-zinc-700 dark:text-zinc-300 dark:hover:bg-zinc-800"
              }`}
              title={batch.priority ? "Remove priority from batch" : "Move batch to the front of the queue"}
              aria-label={batch.priority ? `Remove priority from ${batch.mangaTitle}` : `Prioritize ${batch.mangaTitle}`}
            >
              <Icon icon={isActionPending(`priority:${batch.id}`) ? "carbon:renew" : batch.priority ? "carbon:star-filled" : "carbon:star"} className={`h-3.5 w-3.5 ${isActionPending(`priority:${batch.id}`) ? "animate-spin" : ""}`} />
              {isActionPending(`priority:${batch.id}`) ? (batch.priority ? "Removing…" : "Prioritizing…") : (batch.priority ? "Prioritized" : "Prioritize")}
            </button>
          )}
          {(batch.status === "waiting" || batch.status === "error") && (
            <button
              type="button"
              onClick={() => runAction(`remove:${batch.id}`, "Removing…", () => onRemove(batch.id))}
              disabled={isActionPending(`remove:${batch.id}`)}
              aria-busy={isActionPending(`remove:${batch.id}`)}
              className="inline-flex min-h-11 min-w-11 items-center justify-center rounded-lg p-2 text-zinc-400 hover:bg-zinc-100 hover:text-rose-600 disabled:cursor-wait disabled:opacity-70 dark:hover:bg-zinc-800 transition-colors"
              title={batch.status === "error" ? "Remove failed batch" : "Remove waiting batch"}
              aria-label={batch.status === "error" ? `Remove failed batch ${batch.mangaTitle}` : `Remove waiting batch ${batch.mangaTitle}`}
            >
              <Icon icon={isActionPending(`remove:${batch.id}`) ? "carbon:renew" : "carbon:trash-can"} className={`h-4 w-4 ${isActionPending(`remove:${batch.id}`) ? "animate-spin" : ""}`} />
            </button>
          )}
        </div>
      </div>

      <div className="px-4 py-3">
        <div className="mb-2 flex items-center justify-between text-xs text-zinc-500 dark:text-zinc-400">
          <span>
            {isUploading
              ? `Importing ${batch.totalItems} ${batch.totalItems === 1 ? "page" : "pages"} · keep this tab open`
              : waiting
              ? `${waiting} waiting`
              : "No pages waiting"}
            {failed ? ` · ${failed} failed` : ""}
          </span>
          {!isUploading && <span>{progress}%</span>}
        </div>
        <div className="h-1.5 overflow-hidden rounded-full bg-zinc-100 dark:bg-zinc-800">
          <div
            className={`h-full rounded-full bg-indigo-600 ${isUploading ? "w-1/3 animate-pulse" : "transition-all"}`}
            style={isUploading ? undefined : { width: `${progress}%` }}
          />
        </div>
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
        <div className="space-y-2 border-t border-zinc-100 p-4 dark:border-zinc-800">
          {batch.items.map((item) => (
            <ItemRow
              key={item.id}
              item={item}
              now={item.status === "processing" && item.stepStartedAt ? clock : 0}
              batchSettings={batch.settings}
              batchMangaTitle={batch.mangaTitle}
              onRetryItem={handleRetryItem}
              onRemoveItem={handleRemoveItem}
              retryActionKey={`retry:${batch.id}:${item.id}`}
              isActionPending={isActionPending}
              runAction={runAction}
              onOpenLightbox={onOpenLightbox}
              onOpenPageEdit={onOpenPageEdit}
              isColorizerActive={isUploading ? false : isColorizerActive}
              onToggleExcludeColor={handleToggleExcludeColor}
              sourceType={sourceType}
            />
          ))}
        </div>
      )}
    </div>
    </RenderProfiler>
  );
});

const getBatchTimestamp = (batch: TranslationBatch): number => {
  const t = batch.addedAt instanceof Date ? batch.addedAt.getTime() : typeof batch.addedAt === "number" ? batch.addedAt : 0;
  if (!Number.isNaN(t) && t > 0) return t;
  const u = batch.updatedAt instanceof Date ? batch.updatedAt.getTime() : typeof batch.updatedAt === "number" ? batch.updatedAt : 0;
  if (!Number.isNaN(u) && u > 0) return u;
  return 0;
};

const getStatusRank = (status: TranslationBatch["status"]): number => {
  switch (status) {
    case "processing":
    case "uploading":
    case "stopping":
      return 0; // Active working batches first
    case "waiting":
    case "paused":
      return 1; // Queued/paused next
    case "error":
      return 2; // Errors
    case "completed":
    default:
      return 3; // Completed summaries
  }
};

export const sortBatchesLatestFirst = (batches: TranslationBatch[]): TranslationBatch[] =>
  batches
    .filter((batch) => !batch.dismissed)
    .slice()
    .sort((a, b) => {
      // 1. Priority batches on top
      const prioDiff = (b.priority ? 1 : 0) - (a.priority ? 1 : 0);
      if (prioDiff !== 0) return prioDiff;

      // 2. Active batches (uploading/processing/waiting) before completed
      const rankDiff = getStatusRank(a.status) - getStatusRank(b.status);
      if (rankDiff !== 0) return rankDiff;

      // 3. Latest submission on top
      const timeDiff = getBatchTimestamp(b) - getBatchTimestamp(a);
      if (timeDiff !== 0) return timeDiff;

      return b.id.localeCompare(a.id);
    });

export const canChangeBatchTranslator = (batch: TranslationBatch): boolean =>
  ["waiting", "processing", "paused"].includes(batch.status) ||
  (batch.status === "error" && (
    (batch.failedCount ?? 0) > 0 || batch.items.some((item) => item.status === "error")
  ));

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
