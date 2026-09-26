import React from "react";
import { Icon } from "@iconify/react";
import { fetchStatusText } from "@/utils/fetchStatusText";
import { type FileStatus, type StudioFile, processingStatuses } from "@/types";
import PreviewImage from "./PreviewImage";

const pipelineSteps = [
  { key: "upload", label: "Upload" },
  { key: "colorizing", label: "Colorize" },
  { key: "detection", label: "Detection" },
  { key: "ocr", label: "OCR" },
  { key: "inpainting", label: "Inpaint" },
  { key: "translating", label: "Translate" },
  { key: "rendering", label: "Render" },
];

interface ImageHandlingCardProps {
  entry: StudioFile;
  status?: FileStatus;
  duplicateNameCount: number;
  isSelected: boolean;
  isProcessing: boolean;
  isColorizerActive?: boolean;
  excludedColorFiles?: Set<string>;
  autoDetectedColorFiles?: Set<string>;
  onToggleFile: (fileId: string, shiftKey?: boolean) => void;
  onToggleExcludeColor?: (fileId: string) => void;
  onOpenLightbox?: (file: StudioFile, result: Blob | File | string | null) => void;
  removeFile: (fileId: string) => void;
}

const ImageHandlingCard: React.FC<ImageHandlingCardProps> = ({
  entry,
  status,
  duplicateNameCount,
  isSelected,
  isProcessing,
  isColorizerActive,
  excludedColorFiles,
  autoDetectedColorFiles,
  onToggleFile,
  onToggleExcludeColor,
  onOpenLightbox,
  removeFile,
}) => {
  const file = entry.file;
  const fileId = entry.id;
  const isFinished = status?.status === "finished";
  const isError = status?.status === "error" || Boolean(status?.error);
  const isCurrentFileActive = Boolean(status?.status && processingStatuses.includes(status.status));
  const currentStatusKey = status?.status;
  const isSelectableOnImageClick = !isSelected && !isCurrentFileActive;

  return (
                <div
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
                        {duplicateNameCount > 1 && (
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
};

export default ImageHandlingCard;
