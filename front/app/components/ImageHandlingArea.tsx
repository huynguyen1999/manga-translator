import React, { useEffect, useRef, useState } from "react";
import { Icon } from "@iconify/react";
import { fetchStatusText } from "@/utils/fetchStatusText";
import { type FileStatus, type PendingStudioFile, type StudioFile, processingStatuses } from "@/types";
import { MANGA_TITLE_MAX_LENGTH } from "@/config";
import { SUPPORTED_UPLOAD_ACCEPT } from "@/utils/zipUtils";
import PreviewImage from "./PreviewImage";

export interface ImageHandlingAreaProps {
  files: StudioFile[];
  pendingFiles: PendingStudioFile[];
  fileStatuses: Map<string, FileStatus>;
  isProcessing: boolean;
  isProcessingAllFinished: boolean;
  selectedFiles: Set<string>;
  mangaTitle?: string;
  onMangaTitleChange?: (title: string) => void;

  handleFileChange: (e: React.ChangeEvent<HTMLInputElement>) => void;
  handleDrop: (e: React.DragEvent<HTMLLabelElement>) => void;
  onConfirmPendingFiles: () => void;
  onRemovePendingFile: (fileId: string) => void;
  onReorderPendingFiles: (sourceId: string, targetId: string) => void;
  onClearPendingFiles: () => void;
  onUploadManga?: (files: StudioFile[]) => void;
  uploadMangaError?: string | null;
  handleSubmit: () => void;
  clearForm: () => void;
  removeFile: (fileId: string) => void;
  onRemoveSelectedFiles?: () => void;
  onToggleFile: (fileId: string, shiftKey?: boolean) => void;
  onSelectAll: () => void;
  onDeselectAll: () => void;
  onRetryFile?: (file: File) => void;
  onOpenLightbox?: (file: StudioFile, result: Blob | File | string | null) => void;
  excludedColorFiles?: Set<string>;
  autoDetectedColorFiles?: Set<string>;
  onToggleExcludeColor?: (fileId: string) => void;
  onSetExcludeColorForSelected?: (exclude: boolean) => void;
  isColorizerActive?: boolean;
  isExtractingArchive?: boolean;
  archiveError?: string | null;
  onDismissArchiveError?: () => void;
}

const pipelineSteps = [
  { key: "upload", label: "Upload" },
  { key: "colorizing", label: "Colorize" },
  { key: "detection", label: "Detection" },
  { key: "ocr", label: "OCR" },
  { key: "inpainting", label: "Inpaint" },
  { key: "translating", label: "Translate" },
  { key: "rendering", label: "Render" },
];

export const ImageHandlingArea: React.FC<ImageHandlingAreaProps> = ({
  files,
  pendingFiles,
  fileStatuses,
  isProcessing,
  isProcessingAllFinished,
  selectedFiles,
  mangaTitle,
  onMangaTitleChange,
  handleFileChange,
  handleDrop,
  onConfirmPendingFiles,
  onRemovePendingFile,
  onReorderPendingFiles,
  onClearPendingFiles,
  onUploadManga,
  uploadMangaError,
  handleSubmit,
  clearForm,
  removeFile,
  onRemoveSelectedFiles,
  onToggleFile,
  onSelectAll,
  onDeselectAll,
  onOpenLightbox,
  excludedColorFiles,
  autoDetectedColorFiles,
  onToggleExcludeColor,
  onSetExcludeColorForSelected,
  isColorizerActive,
  isExtractingArchive,
  archiveError,
  onDismissArchiveError,
}) => {
  const [isDragOver, setIsDragOver] = useState(false);
  const [draggedPendingFileId, setDraggedPendingFileId] = useState<string | null>(null);
  const [pendingDragOverId, setPendingDragOverId] = useState<string | null>(null);
  const pendingFileListRef = useRef<HTMLDivElement>(null);
  const pendingAutoScrollFrameRef = useRef<number | null>(null);
  const pendingAutoScrollDirectionRef = useRef<-1 | 0 | 1>(0);

  const stopPendingAutoScroll = () => {
    pendingAutoScrollDirectionRef.current = 0;
    if (pendingAutoScrollFrameRef.current !== null) {
      cancelAnimationFrame(pendingAutoScrollFrameRef.current);
      pendingAutoScrollFrameRef.current = null;
    }
  };

  const runPendingAutoScroll = () => {
    const list = pendingFileListRef.current;
    const direction = pendingAutoScrollDirectionRef.current;
    if (!list || direction === 0) {
      pendingAutoScrollFrameRef.current = null;
      return;
    }
    list.scrollTop += direction * 12;
    pendingAutoScrollFrameRef.current = requestAnimationFrame(runPendingAutoScroll);
  };

  const updatePendingAutoScroll = (event: React.DragEvent<HTMLDivElement>) => {
    event.preventDefault();
    const list = pendingFileListRef.current;
    if (!list) return;

    const bounds = list.getBoundingClientRect();
    const edgeSize = Math.min(120, bounds.height / 3);
    pendingAutoScrollDirectionRef.current =
      event.clientY <= bounds.top + edgeSize ? -1 : event.clientY >= bounds.bottom - edgeSize ? 1 : 0;

    if (pendingAutoScrollDirectionRef.current === 0) {
      stopPendingAutoScroll();
    } else if (pendingAutoScrollFrameRef.current === null) {
      pendingAutoScrollFrameRef.current = requestAnimationFrame(runPendingAutoScroll);
    }
  };

  useEffect(() => stopPendingAutoScroll, []);

  const selectedCount = files.filter((entry) => selectedFiles.has(entry.id)).length;
  const allSelected = files.length > 0 && selectedCount === files.length;
  const noneSelected = selectedCount === 0;
  const duplicateNames = new Map<string, number>();
  files.forEach((entry) => {
    const key = entry.file.name.toLocaleLowerCase();
    duplicateNames.set(key, (duplicateNames.get(key) || 0) + 1);
  });

  return (
    <div className="space-y-6">
      {/* Upload Drop Zone */}
      <div className="space-y-2">
        <label
          htmlFor="file-upload"
          onDrop={(e) => { setIsDragOver(false); handleDrop(e); }}
          onDragOver={(e) => { e.preventDefault(); setIsDragOver(true); }}
          onDragEnter={(e) => { e.preventDefault(); setIsDragOver(true); }}
          onDragLeave={(e) => { e.preventDefault(); setIsDragOver(false); }}
          className={`relative block w-full rounded-2xl border-2 border-dashed text-center cursor-pointer transition-all duration-200 ${
            isDragOver
              ? "border-indigo-500 bg-indigo-50/50 dark:bg-indigo-950/20 scale-[1.005]"
              : "border-zinc-300 dark:border-zinc-700 hover:border-indigo-400 dark:hover:border-indigo-500/70 bg-white/50 dark:bg-zinc-900/30"
          } ${files.length > 0 ? "p-4 sm:p-5" : "p-8"}`}
        >
          <div className="mx-auto flex max-w-md flex-col items-center justify-center space-y-2">
            <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-indigo-50 dark:bg-indigo-950/60 text-indigo-600 dark:text-indigo-400 shadow-xs">
              <Icon
                icon={
                  isExtractingArchive
                    ? "carbon:renew"
                    : isProcessing
                    ? "carbon:add-alt"
                    : "carbon:cloud-upload"
                }
                className={`h-5 w-5 ${isExtractingArchive ? "animate-spin" : ""}`}
              />
            </div>
            <div className="space-y-0.5 text-center">
              <div className="text-sm font-semibold text-zinc-800 dark:text-zinc-200">
                {isExtractingArchive
                  ? "Extracting manga pages from comic archive..."
                  : isProcessing
                  ? "Drop additional manga pages to prepare another batch"
                  : "Drop manga pages or click to upload"}
              </div>
              <p className="text-xs text-zinc-500 dark:text-zinc-400">
                Supports PNG, JPEG, WEBP, BMP, CBZ, ZIP (⌘V / Ctrl+V to paste)
              </p>
            </div>
          </div>
          <input
            id="file-upload"
            type="file"
            multiple
            accept={SUPPORTED_UPLOAD_ACCEPT}
            className="hidden"
            onChange={handleFileChange}
          />
        </label>
      </div>

      {/* Archive extraction error banner */}
      {archiveError && (
        <div className="flex items-center justify-between rounded-xl border border-rose-200 bg-rose-50 px-4 py-3 text-xs text-rose-800 dark:border-rose-900/60 dark:bg-rose-950/40 dark:text-rose-300">
          <div className="flex items-center space-x-2">
            <Icon icon="carbon:warning-filled" className="h-4 w-4 shrink-0 text-rose-500" />
            <span>{archiveError}</span>
          </div>
          {onDismissArchiveError && (
            <button
              type="button"
              onClick={onDismissArchiveError}
              className="text-rose-600 hover:text-rose-800 dark:text-rose-400 dark:hover:text-rose-200 cursor-pointer"
            >
              <Icon icon="carbon:close" className="h-4 w-4" />
            </button>
          )}
        </div>
      )}

      {pendingFiles.length > 0 && (
        <section
          aria-labelledby="pending-file-order-title"
          className="space-y-3 rounded-xl border border-indigo-200 bg-indigo-50/50 p-3 dark:border-indigo-900/70 dark:bg-indigo-950/20 sm:p-4"
        >
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div>
              <h2 id="pending-file-order-title" className="flex items-center gap-2 text-sm font-semibold text-zinc-900 dark:text-zinc-100">
                <Icon icon="carbon:sort-ascending" className="h-4 w-4 text-indigo-600 dark:text-indigo-400" />
                Arrange original files before loading
              </h2>
              <p className="mt-1 text-xs text-indigo-900/80 dark:text-indigo-200/80">
                Drag the original files into manga order. Pages inside each archive keep their internal order.
              </p>
            </div>
            <span className="rounded-full bg-white px-2.5 py-1 text-xs font-semibold text-indigo-700 shadow-2xs dark:bg-zinc-900 dark:text-indigo-300">
              {pendingFiles.length} {pendingFiles.length === 1 ? "file" : "files"} waiting
            </span>
          </div>

          <div
            ref={pendingFileListRef}
            onDragOver={updatePendingAutoScroll}
            onDragLeave={(event) => {
              const relatedTarget = event.relatedTarget as Node | null;
              if (!relatedTarget || !event.currentTarget.contains(relatedTarget)) stopPendingAutoScroll();
            }}
            className="max-h-[min(56rem,80vh)] space-y-2 overflow-y-auto overscroll-contain pr-1"
          >
            {pendingFiles.map((source, index) => (
              <div
                key={source.id}
                draggable
                onDragStart={(event) => {
                  setDraggedPendingFileId(source.id);
                  event.dataTransfer.effectAllowed = "move";
                  event.dataTransfer.setData("text/plain", source.id);
                }}
                onDragOver={(event) => {
                  event.preventDefault();
                  event.dataTransfer.dropEffect = "move";
                  setPendingDragOverId(source.id);
                }}
                onDrop={(event) => {
                  event.preventDefault();
                  stopPendingAutoScroll();
                  onReorderPendingFiles(event.dataTransfer.getData("text/plain") || draggedPendingFileId || "", source.id);
                }}
                onDragEnd={() => {
                  stopPendingAutoScroll();
                  setDraggedPendingFileId(null);
                  setPendingDragOverId(null);
                }}
                className={`flex items-center gap-2 rounded-lg border bg-white p-2 transition-all dark:bg-zinc-900 sm:gap-3 sm:p-2.5 ${
                  pendingDragOverId === source.id
                    ? "border-indigo-500 ring-2 ring-indigo-200 dark:ring-indigo-900/60"
                    : "border-zinc-200/80 dark:border-zinc-800"
                } ${draggedPendingFileId === source.id ? "opacity-40" : ""}`}
              >
                <span className="cursor-grab select-none text-base leading-none text-zinc-400 active:cursor-grabbing" title="Drag to reorder" aria-hidden="true">
                  ⋮⋮
                </span>
                <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-md bg-zinc-100 text-xs font-bold text-zinc-500 dark:bg-zinc-800 dark:text-zinc-400">
                  {index + 1}
                </span>
                <div className="h-16 w-12 shrink-0 overflow-hidden rounded-md border border-zinc-200 bg-zinc-100 dark:border-zinc-700 dark:bg-zinc-950">
                  <PreviewImage file={source.pages[0]?.file ?? null} result={null} showComparisonControls={false} className="h-full w-full" />
                </div>
                <div className="min-w-0 flex-1">
                  <span className="block whitespace-normal break-words text-xs font-medium leading-snug text-zinc-800 dark:text-zinc-200" title={source.file.name}>
                    {source.file.name}
                  </span>
                  {source.pages.length > 1 && (
                    <span className="mt-0.5 block text-xs text-zinc-500 dark:text-zinc-400">
                      {source.pages.length} pages inside
                    </span>
                  )}
                </div>
                <div className="flex shrink-0 items-center gap-0.5">
                  <button
                    type="button"
                    onClick={() => {
                      if (index > 0) onReorderPendingFiles(source.id, pendingFiles[index - 1].id);
                    }}
                    disabled={index === 0}
                    className="flex min-h-8 min-w-8 items-center justify-center rounded-lg p-1.5 text-zinc-500 hover:bg-zinc-100 hover:text-zinc-800 disabled:cursor-not-allowed disabled:opacity-30 dark:text-zinc-400 dark:hover:bg-zinc-800 dark:hover:text-zinc-100"
                    aria-label={`Move ${source.file.name} up`}
                    title="Move up"
                  >
                    <Icon icon="carbon:chevron-up" className="h-4 w-4" />
                  </button>
                  <button
                    type="button"
                    onClick={() => {
                      if (index < pendingFiles.length - 1) onReorderPendingFiles(source.id, pendingFiles[index + 1].id);
                    }}
                    disabled={index === pendingFiles.length - 1}
                    className="flex min-h-8 min-w-8 items-center justify-center rounded-lg p-1.5 text-zinc-500 hover:bg-zinc-100 hover:text-zinc-800 disabled:cursor-not-allowed disabled:opacity-30 dark:text-zinc-400 dark:hover:bg-zinc-800 dark:hover:text-zinc-100"
                    aria-label={`Move ${source.file.name} down`}
                    title="Move down"
                  >
                    <Icon icon="carbon:chevron-down" className="h-4 w-4" />
                  </button>
                  <button
                    type="button"
                    onClick={() => onRemovePendingFile(source.id)}
                    className="flex min-h-8 min-w-8 items-center justify-center rounded-lg p-1.5 text-rose-500/70 hover:bg-rose-50 hover:text-rose-600 dark:text-rose-400/70 dark:hover:bg-rose-950/40 dark:hover:text-rose-400"
                    aria-label={`Remove ${source.file.name} from pending files`}
                    title="Remove file"
                  >
                    <Icon icon="carbon:close" className="h-4 w-4" />
                  </button>
                </div>
              </div>
            ))}
          </div>

          <div className="flex flex-wrap items-center justify-end gap-2 border-t border-indigo-200/70 pt-3 dark:border-indigo-900/60">
            <button
              type="button"
              onClick={onClearPendingFiles}
              className="rounded-lg px-3 py-1.5 text-xs font-medium text-zinc-600 hover:bg-white hover:text-rose-600 dark:text-zinc-400 dark:hover:bg-zinc-900 dark:hover:text-rose-400"
            >
              Discard waiting files
            </button>
            <button
              type="button"
              onClick={onConfirmPendingFiles}
              className="flex items-center gap-1.5 rounded-lg bg-indigo-600 px-3.5 py-1.5 text-xs font-semibold text-white shadow-xs transition-colors hover:bg-indigo-500 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500"
            >
              <Icon icon="carbon:checkmark" className="h-4 w-4" />
              Load files in this order
            </button>
          </div>
        </section>
      )}

      {/* Main Translation Cards Area */}
      {files.length > 0 && (
        <div className="space-y-4">
          {/* Action Header Bar */}
          <div className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-zinc-200 bg-white p-3 shadow-xs dark:border-zinc-800 dark:bg-zinc-900/80 sm:p-3.5">
            <div className="flex min-w-0 flex-wrap items-center gap-2 sm:gap-3">
              <span className="font-semibold text-sm text-zinc-900 dark:text-zinc-100">
                {files.length} {files.length === 1 ? "page" : "pages"} loaded
              </span>
              {mangaTitle !== undefined && onMangaTitleChange && (
                <div className="flex items-center gap-1.5 rounded-lg border border-indigo-200/80 dark:border-indigo-800/80 bg-indigo-50/60 dark:bg-indigo-950/40 px-2.5 py-1 text-xs">
                  <Icon icon="carbon:book" className="h-3.5 w-3.5 text-indigo-600 dark:text-indigo-400 shrink-0" />
                  <span className="text-zinc-500 dark:text-zinc-400 font-medium shrink-0">Manga:</span>
                  <input
                    type="text"
                    value={mangaTitle}
                    onChange={(e) => onMangaTitleChange(e.target.value)}
                    maxLength={MANGA_TITLE_MAX_LENGTH}
                    placeholder="Manga title (optional)"
                    className="bg-transparent font-semibold text-zinc-900 dark:text-zinc-100 outline-none w-32 sm:w-48 placeholder:text-zinc-400 dark:placeholder:text-zinc-500 truncate focus:w-60 transition-all"
                    title="Manga title for this batch"
                    aria-label="Manga Title"
                  />
                </div>
              )}
              {isProcessing && (
                <span className="flex items-center space-x-1.5 rounded-full bg-indigo-50 dark:bg-indigo-950/60 border border-indigo-200 dark:border-indigo-800 px-2.5 py-0.5 text-xs font-medium text-indigo-700 dark:text-indigo-300">
                  <Icon icon="carbon:renew" className="h-3.5 w-3.5 animate-spin" />
                  <span>Translating in parallel...</span>
                </span>
              )}
              {isProcessingAllFinished && (
                <span className="flex items-center space-x-1.5 rounded-full bg-emerald-50 dark:bg-emerald-950/60 border border-emerald-200 dark:border-emerald-800 px-2.5 py-0.5 text-xs font-medium text-emerald-700 dark:text-emerald-300">
                  <Icon icon="carbon:checkmark" className="h-3.5 w-3.5" />
                  <span>All finished</span>
                </span>
              )}
            </div>

            <div className="flex w-full flex-wrap items-center gap-2 sm:w-auto">
              {/* Select all / deselect all */}
              <button
                type="button"
                onClick={allSelected ? onDeselectAll : onSelectAll}
                className="flex items-center space-x-1 rounded-lg border border-zinc-200 dark:border-zinc-700 bg-zinc-50 dark:bg-zinc-800 px-2.5 py-1.5 text-xs font-medium text-zinc-700 dark:text-zinc-200 hover:bg-zinc-100 dark:hover:bg-zinc-700 cursor-pointer transition-colors shadow-2xs"
              >
                <Icon
                  icon={allSelected ? "carbon:checkbox-checked" : "carbon:checkbox"}
                  className="h-4 w-4"
                />
                <span>{allSelected ? "Deselect All" : "Select All"}</span>
              </button>

              {/* Toggle Color for selected files */}
              {isColorizerActive && !isProcessing && (
                <button
                  type="button"
                  onClick={() => {
                    const anyColoring = files.some(
                      (entry) => selectedFiles.has(entry.id) && !excludedColorFiles?.has(entry.id)
                    );
                    onSetExcludeColorForSelected?.(anyColoring);
                  }}
                  disabled={noneSelected}
                  className="flex items-center space-x-1.5 rounded-lg border border-zinc-200 dark:border-zinc-700 bg-zinc-50 dark:bg-zinc-800 px-2.5 py-1.5 text-xs font-medium text-zinc-700 dark:text-zinc-200 hover:bg-zinc-100 dark:hover:bg-zinc-700 cursor-pointer transition-colors shadow-2xs disabled:opacity-40"
                  title="Toggle colorization exclusion for selected pages"
                >
                  <Icon icon="carbon:color-palette" className="h-4 w-4 text-purple-600 dark:text-purple-400" />
                  <span>Toggle Color</span>
                </button>
              )}

              {/* Add more pages button */}
              <label
                htmlFor="file-upload-more"
                className="flex items-center space-x-1 rounded-lg border border-zinc-200 dark:border-zinc-700 bg-zinc-50 dark:bg-zinc-800 px-3 py-1.5 text-xs font-medium text-zinc-700 dark:text-zinc-200 hover:bg-zinc-100 dark:hover:bg-zinc-700 cursor-pointer transition-colors shadow-2xs"
              >
                <Icon icon="carbon:add" className="h-4 w-4" />
                <span>Add More</span>
                <input
                  id="file-upload-more"
                  type="file"
                  multiple
                  accept={SUPPORTED_UPLOAD_ACCEPT}
                  className="hidden"
                  onChange={handleFileChange}
                />
              </label>

              {/* Upload original manga without translation */}
              {onUploadManga && (
                <button
                  type="button"
                  onClick={() => onUploadManga(files)}
                  className="flex items-center space-x-1.5 rounded-lg border border-emerald-200 bg-emerald-50 px-3 py-1.5 text-xs font-semibold text-emerald-700 shadow-2xs transition-colors hover:bg-emerald-100 dark:border-emerald-900/60 dark:bg-emerald-950/30 dark:text-emerald-300 dark:hover:bg-emerald-950/50 cursor-pointer"
                  title="Upload original manga pages or CBZ/ZIP archive without translating"
                >
                  <Icon icon="carbon:book" className="h-4 w-4" />
                  <span>Upload Manga</span>
                </button>
              )}

              {/* Translate Selected */}
              {!isProcessing && !isProcessingAllFinished && (
                <button
                  type="button"
                  onClick={handleSubmit}
                  disabled={noneSelected}
                  className="flex items-center space-x-1.5 rounded-lg bg-indigo-600 px-4 py-1.5 text-xs font-semibold text-white shadow-xs hover:bg-indigo-500 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500 transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
                >
                  <Icon icon="carbon:translate" className="h-4 w-4" />
                  <span>
                    Translate{" "}
                    {selectedCount > 0
                      ? selectedCount === files.length
                        ? "All"
                        : `${selectedCount} Selected`
                      : ""}
                  </span>
                </button>
              )}

              {isProcessingAllFinished && (
                <button
                  type="button"
                  onClick={clearForm}
                  className="flex items-center space-x-1.5 rounded-lg bg-indigo-600 px-4 py-1.5 text-xs font-semibold text-white shadow-xs hover:bg-indigo-500 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500 transition-colors"
                >
                  <Icon icon="carbon:reset" className="h-4 w-4" />
                  <span>Start New Batch</span>
                </button>
              )}

              {/* Remove Selected */}
              {!isProcessing && selectedCount > 0 && onRemoveSelectedFiles && (
                <button
                  type="button"
                  onClick={onRemoveSelectedFiles}
                  className="flex items-center space-x-1 rounded-lg border border-zinc-200 dark:border-zinc-700 bg-zinc-50 dark:bg-zinc-800 px-2.5 py-1.5 text-xs font-medium text-zinc-700 dark:text-zinc-200 hover:text-red-600 dark:hover:text-red-400 hover:bg-zinc-100 dark:hover:bg-zinc-700 cursor-pointer transition-colors shadow-2xs"
                  title="Remove selected files from batch"
                >
                  <Icon icon="carbon:trash-can" className="h-4 w-4" />
                  <span>Remove {selectedCount > 1 ? `${selectedCount} Selected` : "Selected"}</span>
                </button>
              )}

              {/* Clear All */}
              <button
                type="button"
                onClick={clearForm}
                disabled={isProcessing}
                className="flex items-center space-x-1 rounded-lg px-2.5 py-1.5 text-xs font-medium text-zinc-500 dark:text-zinc-400 hover:text-red-600 dark:hover:text-red-400 hover:bg-zinc-100 dark:hover:bg-zinc-800 transition-colors disabled:opacity-40"
              >
                <Icon icon="carbon:trash-can" className="h-4 w-4" />
                <span>Clear All</span>
              </button>
            </div>
          </div>

          {uploadMangaError && (
            <p className="text-right text-xs text-rose-600 dark:text-rose-400" role="alert">
              {uploadMangaError}
            </p>
          )}

          {/* Cards Grid */}
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-5">
            {files.map((entry) => {
              const file = entry.file;
              const fileId = entry.id;
              const status = fileStatuses.get(fileId);
              const isFinished = status?.status === "finished";
              const isError = status?.status === "error" || Boolean(status?.error);
              const isCurrentFileActive = Boolean(status?.status && processingStatuses.includes(status.status));
              const currentStatusKey = status?.status;
              const isSelected = selectedFiles.has(fileId);
              const isSelectableOnImageClick = !isSelected && !isCurrentFileActive;

              return (
                <div
                  key={fileId}
                  className={`flex flex-col rounded-xl border overflow-hidden shadow-xs hover:shadow-md transition-all ${
                    isSelected
                      ? "border-indigo-400 dark:border-indigo-600 bg-white dark:bg-zinc-900 ring-1 ring-indigo-400/40 dark:ring-indigo-600/40"
                      : "border-zinc-200 dark:border-zinc-800 bg-white dark:bg-zinc-900 opacity-60 hover:opacity-85"
                  }`}
                >
                  {/* Card Header Bar */}
                  <div className="flex flex-wrap items-center justify-between gap-2 border-b border-zinc-100 px-3 py-2.5 text-xs dark:border-zinc-800 sm:flex-nowrap sm:px-3.5">
                    <div className="flex min-w-0 flex-1 flex-wrap items-center gap-2">
                      {/* Selection Checkbox */}
                      {!isCurrentFileActive ? (
                        <button
                          type="button"
                          onClick={(e) => onToggleFile(fileId, e.shiftKey)}
                          className="flex-shrink-0 flex items-center justify-center text-indigo-600 dark:text-indigo-400 hover:opacity-80 transition-opacity cursor-pointer select-none"
                          title={
                            isSelected
                              ? "Deselect for translation (Shift+click for range)"
                              : "Select for translation (Shift+click for range)"
                          }
                        >
                          <Icon
                            icon={isSelected ? "carbon:checkbox-checked-filled" : "carbon:checkbox"}
                            className="h-4 w-4"
                          />
                        </button>
                      ) : (
                        <span className="flex-shrink-0 flex items-center justify-center text-indigo-500" title="Translating...">
                          <Icon icon="carbon:renew" className="h-3.5 w-3.5 animate-spin" />
                        </span>
                      )}
                      <div
                        className={`flex items-center space-x-2 min-w-0 flex-1 ${
                          !isCurrentFileActive ? "cursor-pointer select-none" : ""
                        }`}
                        onClick={!isCurrentFileActive ? (e) => onToggleFile(fileId, e.shiftKey) : undefined}
                        title={
                          !isCurrentFileActive
                            ? isSelected
                              ? "Click or Shift+click to deselect"
                              : "Click or Shift+click to select"
                            : undefined
                        }
                      >
                        <Icon icon="carbon:document-blank" className="h-4 w-4 flex-shrink-0 text-zinc-400" />
                        <span className="font-medium text-zinc-800 dark:text-zinc-200 truncate" title={file.name}>
                          {file.name}
                        </span>
                        {(duplicateNames.get(file.name.toLocaleLowerCase()) || 0) > 1 && (
                          <span
                            className="max-w-40 truncate text-zinc-400 dark:text-zinc-500"
                            title={entry.sourcePath}
                          >
                            {entry.sourcePath}
                          </span>
                        )}
                        <span className="text-xs text-zinc-400 dark:text-zinc-500 flex-shrink-0">
                          ({(file.size / (1024 * 1024)).toFixed(2)} MB)
                        </span>
                      </div>

                      {/* Per-page Color Exclusion Toggle */}
                      {isColorizerActive && (
                        <button
                          type="button"
                          onClick={() => onToggleExcludeColor?.(fileId)}
                          disabled={isProcessing}
                          className={`inline-flex items-center space-x-1 px-2 py-0.5 rounded-full text-xs font-medium transition-all ${
                            excludedColorFiles?.has(fileId)
                              ? "bg-zinc-100 text-zinc-500 hover:bg-zinc-200 dark:bg-zinc-800 dark:text-zinc-400 dark:hover:bg-zinc-700 line-through decoration-zinc-400"
                              : autoDetectedColorFiles?.has(fileId)
                              ? "bg-purple-100 text-purple-700 dark:bg-purple-950/60 dark:text-purple-300 border border-purple-300 dark:border-purple-800"
                              : "bg-purple-50 text-purple-700 hover:bg-purple-100 dark:bg-purple-950/40 dark:text-purple-300 dark:hover:bg-purple-900/40"
                          } disabled:cursor-not-allowed`}
                          title={
                            excludedColorFiles?.has(fileId)
                              ? autoDetectedColorFiles?.has(fileId)
                                ? "Already colored (auto-skipped). Click to force re-coloring."
                                : "Coloring excluded. Click to enable coloring for this page."
                              : "Coloring enabled. Click to exclude from coloring."
                          }
                        >
                          <Icon
                            icon={
                              excludedColorFiles?.has(fileId)
                                ? "carbon:circle-dash"
                                : "carbon:color-palette"
                            }
                            className="h-3 w-3"
                          />
                          <span>
                            {excludedColorFiles?.has(fileId)
                              ? autoDetectedColorFiles?.has(fileId)
                                ? "Already Colored"
                                : "No Color"
                              : "Colorize"}
                          </span>
                        </button>
                      )}
                    </div>

                    <div className="ml-auto flex shrink-0 items-center space-x-1">
                      {/* Fullscreen view trigger */}
                      {isFinished && onOpenLightbox && (
                        <button
                          type="button"
                          onClick={() => onOpenLightbox(entry, status?.result ?? null)}
                          className="p-1 rounded text-zinc-400 hover:text-zinc-600 dark:hover:text-zinc-200 hover:bg-zinc-100 dark:hover:bg-zinc-800 transition-colors"
                          title="View fullscreen comparison"
                        >
                          <Icon icon="carbon:maximize" className="h-4 w-4" />
                        </button>
                      )}

                      {/* Download button if finished */}
                      {isFinished && status?.result && (
                        <button
                          type="button"
                          onClick={() => {
                            const isBlob = status.result instanceof Blob;
                            const url = isBlob
                              ? URL.createObjectURL(status.result as Blob)
                              : (status.result as string);
                            const a = document.createElement("a");
                            a.href = url;
                            a.download = `translated_${file.name}`;
                            document.body.appendChild(a);
                            a.click();
                            document.body.removeChild(a);
                            if (isBlob) {
                              setTimeout(() => URL.revokeObjectURL(url), 1000);
                            }
                          }}
                          className="p-1 rounded text-zinc-400 hover:text-indigo-600 dark:hover:text-indigo-400 hover:bg-zinc-100 dark:hover:bg-zinc-800 transition-colors"
                          title="Download translated image"
                        >
                          <Icon icon="carbon:download" className="h-4 w-4" />
                        </button>
                      )}

                      {/* Remove file button */}
                      {!isProcessing && (
                        <button
                          type="button"
                          onClick={() => removeFile(fileId)}
                          className="p-1 rounded text-zinc-400 hover:text-red-500 hover:bg-zinc-100 dark:hover:bg-zinc-800 transition-colors"
                          title="Remove file"
                        >
                          <Icon icon="carbon:close" className="h-4 w-4" />
                        </button>
                      )}
                    </div>
                  </div>

                  {/* Image Viewport */}
                  <div
                    className={`relative h-[min(360px,100vw)] w-full bg-zinc-100 dark:bg-zinc-950 flex items-center justify-center overflow-hidden sm:h-[360px] ${
                      isSelectableOnImageClick ? "cursor-pointer group/viewport" : ""
                    }`}
                    onClick={isSelectableOnImageClick ? (e) => onToggleFile(fileId, e.shiftKey) : undefined}
                    onKeyDown={
                      isSelectableOnImageClick
                        ? (e) => {
                            if (e.key === "Enter" || e.key === " ") {
                              e.preventDefault();
                              onToggleFile(fileId, e.shiftKey);
                            }
                          }
                        : undefined
                    }
                    role={isSelectableOnImageClick ? "button" : undefined}
                    tabIndex={isSelectableOnImageClick ? 0 : undefined}
                    aria-label={isSelectableOnImageClick ? `Select ${file.name}` : undefined}
                  >
                    <PreviewImage
                      file={file}
                      result={status?.result ?? null}
                      showComparisonControls={isFinished && isSelected}
                    />

                    {/* Deselected Click-to-Select Overlay */}
                    {isSelectableOnImageClick && (
                      <div
                        className="absolute inset-0 z-20 flex items-center justify-center bg-black/0 hover:bg-black/10 dark:hover:bg-white/5 transition-colors cursor-pointer select-none"
                        title="Click image to select (Shift+click for range)"
                      >
                        <div className="opacity-0 group-hover/viewport:opacity-100 transition-opacity bg-black/75 dark:bg-zinc-900/90 text-white text-xs font-medium px-3 py-1.5 rounded-full shadow-lg flex items-center space-x-1.5 pointer-events-none backdrop-blur-xs">
                          <Icon icon="carbon:checkbox-checked" className="w-4 h-4 text-indigo-400" />
                          <span>Click to select (Shift+click for range)</span>
                        </div>
                      </div>
                    )}

                    {/* Progress Overlay during processing */}
                    {status && currentStatusKey && !isFinished && !isError && (
                      <div className="absolute inset-0 bg-black/60 backdrop-blur-xs flex flex-col items-center justify-center p-6 text-white text-center space-y-4">
                        <div className="flex items-center space-x-2">
                          <Icon icon="carbon:renew" className="h-6 w-6 animate-spin text-indigo-400" />
                          <span className="text-base font-semibold">
                            {fetchStatusText(
                              status.status,
                              status.progress,
                              status.queuePos,
                              status.error
                            )}
                          </span>
                        </div>
                        {status.offlineModel && (
                          <span className="max-w-full truncate text-xs text-zinc-300" title={status.offlineModel}>
                            Offline model: {status.offlineModel}
                          </span>
                        )}
                        {status.geminiModel && (
                          <span className="max-w-full truncate text-xs text-indigo-300" title={status.geminiModel}>
                            Gemini model: {status.geminiModel}
                          </span>
                        )}

                        {/* Pipeline Stepper Pills */}
                        <div className="flex max-w-full flex-wrap items-center justify-center gap-0.5 rounded-xl border border-white/10 bg-black/50 p-1 text-xs sm:space-x-1 sm:rounded-full">
                          {pipelineSteps.map((step) => {
                            const isActive = currentStatusKey === step.key;
                            return (
                              <span
                                key={step.key}
                                className={`rounded-full px-1.5 py-0.5 transition-colors sm:px-2 ${
                                  isActive
                                    ? "bg-indigo-600 text-white font-semibold"
                                    : "text-zinc-400"
                                }`}
                              >
                                {step.label}
                              </span>
                            );
                          })}
                        </div>
                      </div>
                    )}

                    {/* Error Overlay */}
                    {isError && (
                      <div className="absolute inset-0 bg-red-950/80 backdrop-blur-xs flex flex-col items-center justify-center p-6 text-white text-center space-y-3">
                        <Icon icon="carbon:warning" className="h-8 w-8 text-rose-400" />
                        <div className="text-sm font-semibold text-rose-200">
                          {status?.error || "Translation failed"}
                        </div>
                        <p className="text-xs text-rose-300 max-w-xs">
                          Check server connection and try reducing detection resolution if out of memory.
                        </p>
                      </div>
                    )}
                  </div>

                  {/* Card Footer Status */}
                  <div className="flex items-center justify-between border-t border-zinc-100 dark:border-zinc-800 px-3.5 py-2 text-xs">
                    <div>
                      {isFinished ? (
                        <span className="inline-flex items-center space-x-1 text-emerald-600 dark:text-emerald-400 font-medium">
                          <Icon icon="carbon:checkmark-filled" className="h-4 w-4" />
                          <span>Translation complete</span>
                        </span>
                      ) : isError ? (
                        <span className="inline-flex items-center space-x-1 text-rose-600 dark:text-rose-400 font-medium">
                          <Icon icon="carbon:warning-filled" className="h-4 w-4" />
                          <span>Error occurred</span>
                        </span>
                      ) : isProcessing ? (
                        <span className="text-indigo-600 dark:text-indigo-400 font-medium animate-pulse">
                          Processing step...
                        </span>
                      ) : isSelected ? (
                        <span className="text-indigo-600 dark:text-indigo-400">
                          Selected for translation
                        </span>
                      ) : (
                        <span className="text-zinc-400 dark:text-zinc-500">
                          Not selected
                        </span>
                      )}
                    </div>
                  </div>
                </div>
              );
            })}
          </div>

          {/* Bottom Submit Action */}
          {!isProcessing && !isProcessingAllFinished && (
            <div className="pt-2">
              <button
                type="button"
                onClick={handleSubmit}
                disabled={noneSelected}
                className="w-full flex items-center justify-center space-x-2 rounded-xl bg-indigo-600 hover:bg-indigo-500 text-white font-semibold py-3.5 px-6 shadow-md shadow-indigo-600/20 transition-all active:scale-[0.99] disabled:opacity-40 disabled:cursor-not-allowed"
              >
                <Icon icon="carbon:translate" className="h-5 w-5" />
                <span className="text-sm">
                  Translate{" "}
                  {selectedCount > 0
                    ? selectedCount === files.length
                      ? `All ${files.length} ${files.length === 1 ? "Page" : "Pages"}`
                      : `${selectedCount} of ${files.length} ${files.length === 1 ? "Page" : "Pages"}`
                    : "— Select Pages Above"}
                </span>
              </button>
            </div>
          )}
        </div>
      )}
    </div>
  );
};
