import React, { useState } from "react";
import { Icon } from "@iconify/react";
import { type FileStatus, type PendingStudioFile, type StudioFile } from "@/types";
import { MANGA_TITLE_MAX_LENGTH } from "@/config";
import { SUPPORTED_UPLOAD_ACCEPT } from "@/utils/zipUtils";
import PreviewImage from "./PreviewImage";
import PendingFilesOrderPanel from "./PendingFilesOrderPanel";
import ImageHandlingCard from "./ImageHandlingCard";

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

      <PendingFilesOrderPanel
        pendingFiles={pendingFiles}
        onConfirm={onConfirmPendingFiles}
        onRemove={onRemovePendingFile}
        onReorder={onReorderPendingFiles}
        onClear={onClearPendingFiles}
      />

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
            {files.map((entry) => (
              <ImageHandlingCard
                key={entry.id}
                entry={entry}
                status={fileStatuses.get(entry.id)}
                duplicateNameCount={duplicateNames.get(entry.file.name.toLocaleLowerCase()) || 0}
                isSelected={selectedFiles.has(entry.id)}
                isProcessing={isProcessing}
                isColorizerActive={isColorizerActive}
                excludedColorFiles={excludedColorFiles}
                autoDetectedColorFiles={autoDetectedColorFiles}
                onToggleFile={onToggleFile}
                onToggleExcludeColor={onToggleExcludeColor}
                onOpenLightbox={onOpenLightbox}
                removeFile={removeFile}
              />
            ))}
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
