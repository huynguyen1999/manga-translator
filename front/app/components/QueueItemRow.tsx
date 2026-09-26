import React, { useEffect, useState } from "react";
import { Icon } from "@iconify/react";
import type { QueuedImage, TranslationSettings } from "@/types";
import PreviewImage from "./PreviewImage";
import { apiUrl } from "@/utils/api";
import { formatStage } from "@/utils/serverBatches";

const QueueItemRow: React.FC<{
  item: QueuedImage;
  onRemove: (id: string) => void;
  onOpenLightbox?: (
    file: File,
    result: Blob | File | string | null,
    onRetry?: () => void | Promise<void>,
    sourceType?: "original" | "translated",
    options?: {
      folder?: string;
      settings?: Partial<TranslationSettings>;
      mangaTitle?: string;
      offlineModel?: string;
      geminiModel?: string;
    },
  ) => void;
  isColorizerActive?: boolean;
  onToggleExcludeColor?: (id: string) => void;
  onEditMangaTitle?: (item: QueuedImage) => void;
}> = ({ item, onRemove, onOpenLightbox, isColorizerActive, onToggleExcludeColor, onEditMangaTitle }) => {
  const [downloadUrl, setDownloadUrl] = useState<string | null>(null);

  useEffect(() => {
    if (typeof item.result === "string" && item.result) {
      setDownloadUrl(apiUrl(item.result));
      return;
    }
    if (item.result instanceof Blob) {
      if (item.result.size < 1000 && item.folder) {
        setDownloadUrl(apiUrl(`/result/${item.folder}/final.png`));
        return;
      }
      const url = URL.createObjectURL(item.result);
      setDownloadUrl(url);
      return () => URL.revokeObjectURL(url);
    }
    if (item.folder) {
      setDownloadUrl(apiUrl(`/result/${item.folder}/final.png`));
      return;
    }
    setDownloadUrl(null);
  }, [item.result, item.folder]);

  const isFinished = item.status === "finished";
  const isError = item.status === "error";
  const isProcessing = item.status === "processing";
  const previewUrl = item.batchPreviewUrl || item.resultUrl || (item.folder ? `/result/${item.folder}/batch.webp` : null);

  return (
    <div className="flex items-center space-x-3 p-3 rounded-xl border border-zinc-200 dark:border-zinc-800 bg-white dark:bg-zinc-900 shadow-2xs hover:border-zinc-300 dark:hover:border-zinc-700 transition-colors">
      {/* Thumbnail */}
      <div
        className="relative w-16 h-20 flex-shrink-0 rounded-lg overflow-hidden bg-zinc-100 dark:bg-zinc-950 border border-zinc-200 dark:border-zinc-800 flex items-center justify-center cursor-pointer group"
        onClick={() => {
          if (isFinished && onOpenLightbox) {
            onOpenLightbox(
              item.file,
              item.result instanceof Blob && item.result.size < 1000 && item.folder
                ? apiUrl(`/result/${item.folder}/final.png`)
                : (typeof item.result === "string" ? apiUrl(item.result) : (item.result || (item.folder ? apiUrl(`/result/${item.folder}/final.png`) : null))),
              undefined,
              undefined,
              {
                folder: item.folder,
                mangaTitle: item.mangaTitle,
                offlineModel: item.offlineModel,
                geminiModel: item.geminiModel,
              },
            );
          }
        }}
      >
        <PreviewImage
          file={item.file}
          result={previewUrl ? apiUrl(previewUrl) :
            item.result instanceof Blob && item.result.size < 1000 && item.folder
              ? apiUrl(`/result/${item.folder}/final.png`)
              : (typeof item.result === "string" ? apiUrl(item.result) : (item.result || (item.folder ? apiUrl(`/result/${item.folder}/final.png`) : null)))
          }
          showComparisonControls={false}
        />
        {isFinished && (
          <div className="absolute inset-0 bg-black/40 opacity-0 group-hover:opacity-100 flex items-center justify-center transition-opacity">
            <Icon icon="carbon:view" className="h-5 w-5 text-white" />
          </div>
        )}
      </div>

      {/* Info */}
      <div className="flex-1 min-w-0">
        <div className="flex flex-wrap items-center gap-2">
          <span className="text-sm font-medium text-zinc-900 dark:text-zinc-100 truncate max-w-[200px] sm:max-w-xs" title={item.file.name}>
            {item.file.name}
          </span>
          <span className="text-xs text-zinc-400 dark:text-zinc-500">
            {(item.file.size / (1024 * 1024)).toFixed(2)} MB
          </span>
          {/* Target Manga Tag */}
          <button
            type="button"
            onClick={(e) => {
              e.stopPropagation();
              onEditMangaTitle?.(item);
            }}
            disabled={isProcessing || isFinished}
            className={`inline-flex items-center space-x-1 rounded-md px-2 py-0.5 text-[11px] font-medium transition-all ${
              onEditMangaTitle && !isProcessing && !isFinished
                ? "cursor-pointer hover:border-indigo-400 dark:hover:border-indigo-500 hover:bg-indigo-50/50 dark:hover:bg-indigo-950/40"
                : "cursor-default"
            } ${
              item.mangaTitle && item.mangaTitle !== "Ungrouped"
                ? "bg-indigo-50 dark:bg-indigo-950/60 text-indigo-700 dark:text-indigo-300 border border-indigo-200 dark:border-indigo-800"
                : "bg-zinc-100 dark:bg-zinc-800 text-zinc-600 dark:text-zinc-400 border border-zinc-200 dark:border-zinc-700"
            }`}
            title={
              !isProcessing && !isFinished
                ? "Click to change manga group for this page"
                : `Assigned to: ${item.mangaTitle || "Ungrouped"}`
            }
          >
            <Icon icon="carbon:book" className="h-3 w-3 flex-shrink-0 text-zinc-400 dark:text-zinc-400" />
            <span className="truncate max-w-[130px] sm:max-w-[180px]">
              {item.mangaTitle || "Ungrouped"}
            </span>
            {onEditMangaTitle && !isProcessing && !isFinished && (
              <Icon icon="carbon:edit" className="h-2.5 w-2.5 opacity-60 ml-0.5" />
            )}
          </button>
        </div>

        <div className="flex items-center space-x-3 text-xs text-zinc-500 dark:text-zinc-400 mt-1">
          <span>Added: {item.addedAt.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}</span>
          {item.error && (
            <span className="text-rose-600 dark:text-rose-400 truncate max-w-xs font-mono" title={item.error}>
              {item.error}
            </span>
          )}
        </div>
      </div>

      {/* Status & Actions Pill Container */}
      <div className="flex items-center space-x-2 flex-shrink-0">
        {/* Per-page Color Exclusion Toggle */}
        {isColorizerActive && !isFinished && !isProcessing && onToggleExcludeColor && (
          <button
            type="button"
            onClick={() => onToggleExcludeColor(item.id)}
            className={`inline-flex items-center space-x-1 px-2 py-0.5 rounded-full text-[10px] font-medium transition-all ${
              item.excludeColor
                ? "bg-zinc-100 text-zinc-500 hover:bg-zinc-200 dark:bg-zinc-800 dark:text-zinc-400 dark:hover:bg-zinc-700 line-through decoration-zinc-400"
                : item.isAutoColorDetected
                ? "bg-purple-100 text-purple-700 dark:bg-purple-950/60 dark:text-purple-300 border border-purple-300 dark:border-purple-800"
                : "bg-purple-50 text-purple-700 hover:bg-purple-100 dark:bg-purple-950/40 dark:text-purple-300 dark:hover:bg-purple-900/40"
            }`}
            title={
              item.excludeColor
                ? item.isAutoColorDetected
                  ? "Already colored (auto-skipped). Click to force re-coloring."
                  : "Coloring excluded. Click to enable coloring for this page."
                : "Coloring enabled. Click to exclude from coloring."
            }
          >
            <Icon
              icon={item.excludeColor ? "carbon:circle-dash" : "carbon:color-palette"}
              className="h-3 w-3"
            />
            <span>
              {item.excludeColor
                ? item.isAutoColorDetected
                  ? "Already Colored"
                  : "No Color"
                : "Colorize"}
            </span>
          </button>
        )}

        <span
          className={`inline-flex items-center space-x-1 rounded-full px-2.5 py-0.5 text-xs font-medium ${
            item.step === "awaiting_translation"
              ? "bg-sky-50 dark:bg-sky-950/60 text-sky-700 dark:text-sky-300 border border-sky-200 dark:border-sky-800"
              : isProcessing
              ? "bg-indigo-50 dark:bg-indigo-950/60 text-indigo-700 dark:text-indigo-300 border border-indigo-200 dark:border-indigo-800"
              : isFinished
              ? "bg-emerald-50 dark:bg-emerald-950/60 text-emerald-700 dark:text-emerald-300 border border-emerald-200 dark:border-emerald-800"
              : isError
              ? "bg-rose-50 dark:bg-rose-950/60 text-rose-700 dark:text-rose-300 border border-rose-200 dark:border-rose-800"
              : "bg-amber-50 dark:bg-amber-950/60 text-amber-700 dark:text-amber-300 border border-amber-200 dark:border-amber-800"
          }`}
        >
          {item.step === "awaiting_translation" ? (
            <Icon icon="carbon:hourglass" className="h-3 w-3 mr-1 text-sky-600 dark:text-sky-400" />
          ) : isProcessing ? (
            <Icon icon="carbon:renew" className="h-3 w-3 animate-spin mr-1" />
          ) : isFinished ? (
            <Icon icon="carbon:checkmark" className="h-3 w-3 mr-1" />
          ) : isError ? (
            <Icon icon="carbon:warning" className="h-3 w-3 mr-1" />
          ) : null}
          <span>
            {item.step === "awaiting_translation"
              ? "Awaiting translation"
              : isProcessing && item.step
              ? formatStage(item.step)
              : item.status}
          </span>
        </span>

        {/* Download result */}
        {isFinished && downloadUrl && (
          <a
            href={downloadUrl}
            download={`translated_${item.file.name}`}
            className="p-1.5 rounded-lg text-zinc-400 hover:text-indigo-600 dark:hover:text-indigo-400 hover:bg-zinc-100 dark:hover:bg-zinc-800 transition-colors"
            title="Download translated page"
          >
            <Icon icon="carbon:download" className="h-4 w-4" />
          </a>
        )}

        {/* Cancel / Remove button */}
        {isProcessing ? (
          <button
            type="button"
            onClick={() => onRemove(item.id)}
            className="p-1.5 rounded-lg text-rose-500 hover:text-rose-700 dark:hover:text-rose-400 hover:bg-rose-50 dark:hover:bg-rose-950/40 transition-colors"
            title="Cancel translation"
          >
            <Icon icon="carbon:close-outline" className="h-4 w-4" />
          </button>
        ) : (
          <button
            type="button"
            onClick={() => onRemove(item.id)}
            className="p-1.5 rounded-lg text-zinc-400 hover:text-rose-600 hover:bg-zinc-100 dark:hover:bg-zinc-800 transition-colors"
            title="Remove from queue"
          >
            <Icon icon="carbon:trash-can" className="h-4 w-4" />
          </button>
        )}
      </div>
    </div>
  );
};


export default QueueItemRow;
