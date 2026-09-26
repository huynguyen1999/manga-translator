import React, { useEffect, useState } from "react";
import { Icon } from "@iconify/react";
import type { FinishedImage, QueuedImage, TranslationSettings } from "@/types";
import { formatStage, formatStageElapsed, jobThumbnailCandidates, resultFor } from "@/utils/serverBatches";
import { RenderProfiler } from "@/utils/renderPerformance";

type AsyncAction = () => void | Promise<void>;
type ImageSourceType = "original" | "translated";

const itemStatusLabel: Record<QueuedImage["status"], string> = {
  queued: "Waiting",
  processing: "Processing",
  finished: "Complete",
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
  batchMangaTitle?: string;
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
  batchMangaTitle,
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
  const canSkip = item.status === "queued" || (isProcessing && (isAwaitingTranslation || ["initialize", "colorization", "textline_merge", "mask_generation", "layout", "rendering"].includes(item.step || "")));
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
        {canSkip && (
          <button
            type="button"
            onClick={() => runAction(removeActionKey, "Skipping…", () => onRemoveItem(item.id))}
            disabled={isActionPending(removeActionKey)}
            aria-busy={isActionPending(removeActionKey)}
            aria-label={`Skip ${item.file.name}`}
            className="inline-flex min-h-10 items-center gap-1.5 rounded-lg border border-zinc-200 px-3 py-2 text-xs font-semibold text-zinc-600 hover:bg-zinc-100 disabled:cursor-wait disabled:opacity-60 dark:border-zinc-700 dark:text-zinc-300 dark:hover:bg-zinc-800"
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
              onClick={() => runAction(removeActionKey, "Removing…", () => onRemoveItem(item.id))}
              disabled={isActionPending(removeActionKey)}
              aria-busy={isActionPending(removeActionKey)}
              className="order-3 inline-flex min-h-11 min-w-11 items-center justify-center rounded-lg p-2 text-rose-600/80 transition-colors hover:bg-rose-50 hover:text-rose-700 disabled:cursor-wait disabled:opacity-60 dark:text-rose-400/80 dark:hover:bg-rose-950/40 dark:hover:text-rose-300"
              title="Remove failed page"
              aria-label={`Remove failed page ${item.file.name}`}
            >
              <Icon icon={isActionPending(removeActionKey) ? "carbon:renew" : "carbon:trash-can"} className={`h-4 w-4 ${isActionPending(removeActionKey) ? "animate-spin" : ""}`} />
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
