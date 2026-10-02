import React, { useEffect, useState } from "react";
import { Icon } from "@iconify/react";
import type { FinishedImage, QueuedImage, TranslationSettings } from "@/types";
import { apiUrl } from "@/utils/api";
import { formatStage, formatStageElapsed, jobThumbnailCandidates, resultFor } from "@/utils/serverBatches";
import { RenderProfiler } from "@/utils/renderPerformance";

type AsyncAction = () => void | Promise<void>;
type ImageSourceType = "original" | "translated";

const itemStatusLabel: Record<QueuedImage["status"], string> = {
  queued: "Waiting",
  processing: "Processing",
  finished: "Done",
  error: "Failed",
};

const JobThumbnail: React.FC<{ item: QueuedImage }> = ({ item }) => {
  const result = resultFor(item);
  const [blobResultUrl, setBlobResultUrl] = useState<string | null>(null);
  const [sourceIndex, setSourceIndex] = useState(0);
  useEffect(() => {
    if (!(result instanceof Blob)) {
      setBlobResultUrl(null);
      return;
    }
    const url = URL.createObjectURL(result);
    setBlobResultUrl(url);
    return () => URL.revokeObjectURL(url);
  }, [result]);
  const candidates = [...jobThumbnailCandidates(item), ...(blobResultUrl ? [blobResultUrl] : [])];
  useEffect(() => setSourceIndex(0), [candidates.join("\n")]);

  return candidates[sourceIndex] ? (
    <img
      src={candidates[sourceIndex]}
      alt=""
      aria-hidden="true"
      loading="lazy"
      decoding="async"
      onError={() => setSourceIndex((index) => Math.min(index + 1, candidates.length))}
      className="absolute inset-0 h-full w-full object-cover"
    />
  ) : null;
};

export const ItemRow: React.FC<{
  item: QueuedImage;
  now: number;
  batchSettings?: Partial<TranslationSettings>;
  batchMangaTitle?: string; canMutateItems: boolean;
  onRetryItem: (itemId: string, keepFailedPagesForEditing?: boolean) => void | Promise<void>;
  onRemoveItem: (itemId: string) => void | Promise<void>;
  removeActionKey: string;
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
      images?: FinishedImage[];
      currentIndex?: number;
    },
  ) => void;
  onOpenPageEdit?: (folder: string) => void;
  isColorizerActive?: boolean;
  onToggleExcludeColor?: (itemId: string) => void;
  sourceType?: ImageSourceType;
  modalImages?: FinishedImage[];
}> = React.memo(({
  item,
  now,
  batchSettings,
  batchMangaTitle, canMutateItems,
  onRetryItem,
  onRemoveItem,
  removeActionKey,
  retryActionKey,
  isActionPending,
  runAction,
  onOpenLightbox,
  onOpenPageEdit,
  isColorizerActive,
  onToggleExcludeColor,
  sourceType,
  modalImages,
}) => {
  const result = resultFor(item);
  const isFinished = item.status === "finished";
  const isProcessing = item.status === "processing";
  const isError = item.status === "error";
  const isAwaitingTranslation = item.step === "awaiting_translation";
  const canSkip = canMutateItems && (item.status === "queued" || (isProcessing && (isAwaitingTranslation || ["initialize", "colorization", "textline_merge", "mask_generation", "layout", "rendering"].includes(item.step || ""))));
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
  const [originalDownloadUrl, setOriginalDownloadUrl] = useState<string | null>(null);

  useEffect(() => {
    if (typeof result === "string") { setDownloadUrl(result); return; }
    if (result instanceof Blob) {
      const url = URL.createObjectURL(result);
      setDownloadUrl(url);
      return () => URL.revokeObjectURL(url);
    }
    setDownloadUrl(null);
  }, [result]);

  useEffect(() => {
    const original = item.inputUrl || (item.file.size > 0 ? item.file : null);
    if (typeof original === "string") { setOriginalDownloadUrl(apiUrl(original)); return; }
    if (original instanceof Blob) {
      const url = URL.createObjectURL(original);
      setOriginalDownloadUrl(url);
      return () => URL.revokeObjectURL(url);
    }
    setOriginalDownloadUrl(null);
  }, [item.file, item.inputUrl]);

  return (
  <RenderProfiler id={`JobItem:${item.id}`}>
  <div className={`job-card job-offscreen-row grid grid-cols-[2rem_minmax(0,1fr)_auto] items-center gap-x-2 gap-y-1.5 rounded-lg px-2 py-1.5 hover:bg-zinc-100 dark:hover:bg-zinc-700/50 ${
      isError
        ? "bg-rose-50/60 dark:bg-rose-950/20"
        : isAwaitingTranslation
        ? "bg-sky-50/50 dark:bg-sky-950/20"
        : ""
    }`}>
      <button
        type="button"
        className={`group relative h-10 w-8 shrink-0 overflow-hidden rounded border border-zinc-200 bg-zinc-200 dark:border-zinc-600 dark:bg-zinc-700 ${canOpenPreview ? "cursor-zoom-in" : ""}`}
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
              images: modalImages,
              currentIndex: modalImages?.findIndex((image) => image.id === item.id),
            },
          )
        }
        disabled={!canOpenPreview}
        title={canOpenPreview ? `View ${isFinished && sourceType !== "original" ? "translated" : "original"} page` : undefined}
      >
        <JobThumbnail item={item} />
        {canOpenPreview && (
          <span className="pointer-events-none absolute inset-0 flex items-center justify-center bg-black/35 opacity-0 transition-opacity group-hover:opacity-100 group-focus-visible:opacity-100">
            <Icon icon="carbon:zoom-in" className="h-4 w-4 text-white" aria-hidden="true" />
          </span>
        )}
      </button>

      <div className="min-w-0 flex-1">
        <div className="flex min-w-0 items-center gap-2">
          <span className="min-w-0 truncate text-xs font-medium text-zinc-900 dark:text-zinc-100" title={item.file.name}>
            {item.file.name}
          </span>
        </div>
        <div className="mt-0.5 flex min-w-0 flex-wrap items-center gap-x-2 text-[11px] text-zinc-600 dark:text-zinc-400">
          {item.status === "queued" ? (
            <span className="truncate">{queuedStatus}</span>
          ) : isAwaitingTranslation ? (
            <span className="truncate text-sky-700 dark:text-sky-300">Prepared · Waiting for batch translation</span>
          ) : isProcessing && (
            <span>{item.stepStartedAt ? formatStageElapsed(item.stepStartedAt, now) : "In progress"}</span>
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
          <div className="mt-1 flex items-start gap-1.5">
            <Icon icon="carbon:warning-alt" className="mt-0.5 h-3.5 w-3.5 shrink-0 text-rose-600 dark:text-rose-400" aria-hidden="true" />
            <p className="min-w-0 flex-1 break-words text-xs leading-relaxed text-rose-700 dark:text-rose-300" title={item.error}>
              {item.error || "Translation failed"}
            </p>
          </div>
        )}
      </div>

      <div className={`flex shrink-0 flex-wrap items-center justify-end gap-1 ${isError ? "col-span-3 border-t border-rose-200 pt-1.5 dark:border-rose-900/50" : ""}`}>
        <span className={`inline-flex items-center gap-1 text-[11px] font-medium whitespace-nowrap ${needsReview ? "text-amber-700 dark:text-amber-300" : isError ? "text-rose-700 dark:text-rose-300" : isProcessing && !isAwaitingTranslation ? "text-indigo-700 dark:text-indigo-300" : isFinished ? "text-emerald-700 dark:text-emerald-300" : "text-zinc-600 dark:text-zinc-400"}`}>
          <span className="size-1.5 rounded-full bg-current" aria-hidden="true" />
          {needsReview ? "Review" : isAwaitingTranslation ? "Waiting" : isProcessing ? formatStage(item.step) : itemStatusLabel[item.status]}
        </span>
        {canSkip && (
          <button
            type="button"
            onClick={() => runAction(removeActionKey, "Skipping…", () => onRemoveItem(item.id))}
            disabled={isActionPending(removeActionKey)}
            aria-busy={isActionPending(removeActionKey)}
            aria-label={`Skip ${item.file.name}`}
            className="inline-flex min-h-8 items-center gap-1 rounded-lg px-1.5 text-[11px] font-medium text-zinc-600 hover:bg-zinc-200 disabled:cursor-wait disabled:opacity-60 dark:text-zinc-300 dark:hover:bg-zinc-600"
            title={isProcessing ? "Skip this page after its current step" : "Skip this page in the current batch"}
          >
            <Icon icon={isActionPending(removeActionKey) ? "carbon:renew" : "carbon:close-outline"} className={`h-3.5 w-3.5 ${isActionPending(removeActionKey) ? "animate-spin" : ""}`} />
            {isActionPending(removeActionKey) ? "Skipping…" : "Skip"}
          </button>
        )}
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
            {canMutateItems && <button
              type="button"
              onClick={() => runAction(retryActionKey, "Retrying…", () => onRetryItem(item.id, true))}
              disabled={isActionPending(retryActionKey)}
              aria-busy={isActionPending(retryActionKey)}
              className="order-2 inline-flex min-h-11 items-center gap-1.5 rounded-lg border border-amber-300 bg-amber-50 px-3.5 py-2 text-xs font-semibold text-amber-900 transition-colors hover:bg-amber-100 disabled:cursor-wait disabled:opacity-70 dark:border-amber-700/70 dark:bg-amber-950/40 dark:text-amber-200 dark:hover:bg-amber-950/70"
              title="Keep the translated page and mark failed regions for editing"
            >
              <Icon icon="carbon:edit" className="h-3.5 w-3.5" />
              {isActionPending(retryActionKey) ? "Retrying…" : "Pass & edit"}
            </button>}
            {canMutateItems && <button
              type="button"
              onClick={() => runAction(removeActionKey, "Removing…", () => onRemoveItem(item.id))}
              disabled={isActionPending(removeActionKey)}
              aria-busy={isActionPending(removeActionKey)}
              className="order-3 inline-flex min-h-11 min-w-11 items-center justify-center rounded-lg p-2 text-rose-600/80 transition-colors hover:bg-rose-50 hover:text-rose-700 disabled:cursor-wait disabled:opacity-60 dark:text-rose-400/80 dark:hover:bg-rose-950/40 dark:hover:text-rose-300"
              title="Remove failed page"
              aria-label={`Remove failed page ${item.file.name}`}
            >
              <Icon icon={isActionPending(removeActionKey) ? "carbon:renew" : "carbon:trash-can"} className={`h-4 w-4 ${isActionPending(removeActionKey) ? "animate-spin" : ""}`} />
            </button>}
          </>
        )}
        {isFinished && downloadUrl && (
          <a href={downloadUrl} download={`${sourceType === "original" ? "original" : "translated"}_${item.file.name}`}
            className="inline-flex size-8 items-center justify-center rounded-lg text-zinc-600 hover:bg-zinc-200 hover:text-indigo-600 dark:text-zinc-400 dark:hover:bg-zinc-600 dark:hover:text-indigo-300"
            title={`Download ${sourceType === "original" ? "original" : "translated"} page`}><Icon icon="carbon:download" className="h-4 w-4" /></a>
        )}
        {originalDownloadUrl && (
          <a href={originalDownloadUrl} download={item.file.name} aria-label={`Download original image ${item.file.name}`}
            className="inline-flex size-8 items-center justify-center rounded-lg text-zinc-600 hover:bg-zinc-200 hover:text-indigo-600 dark:text-zinc-400 dark:hover:bg-zinc-600 dark:hover:text-indigo-300"
            title="Download original image"><Icon icon="carbon:image" className="h-4 w-4" /></a>
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
