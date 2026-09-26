import React, { useState, useMemo } from "react";
import { Icon } from "@iconify/react";
import type { MangaGroupSelection, QueuedImage, TranslationSettings } from "@/types";
import QueueItemRow from "./QueueItemRow";
import { GroupSelectionModal } from "./GroupSelectionModal";
import { MANGA_TITLE_MAX_LENGTH } from "@/config";

interface ImageQueueProps {
  queuedImages: QueuedImage[];
  onRemoveFromQueue: (id: string) => void;
  onAddToQueue: (files: File[], mangaTitle?: string) => void;
  onUpdateItemMangaTitle?: (id: string, newMangaTitle: string) => void;
  existingGroups?: string[];
  isProcessing: boolean;
  onStartQueue?: () => void;
  onStopQueue?: () => void;
  onClearCompleted?: () => void;
  onClearWaiting?: () => void;
  autoProcess?: boolean;
  onToggleAutoProcess?: (enabled: boolean) => void;
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
  mangaTitle?: string;
  onMangaTitleChange?: (title: string) => void;
  concurrency?: number | "auto";
  onConcurrencyChange?: (val: number | "auto") => void;
  serverWorkers?: number;
  isColorizerActive?: boolean;
  onToggleExcludeColor?: (id: string) => void;
}

export const ImageQueue: React.FC<ImageQueueProps> = ({
  queuedImages,
  onRemoveFromQueue,
  onAddToQueue,
  onUpdateItemMangaTitle,
  existingGroups = [],
  isProcessing,
  onStartQueue,
  onStopQueue,
  onClearCompleted,
  onClearWaiting,
  autoProcess = true,
  onToggleAutoProcess,
  onOpenLightbox,
  mangaTitle = "",
  onMangaTitleChange,
  concurrency = "auto",
  onConcurrencyChange,
  serverWorkers = 2,
  isColorizerActive,
  onToggleExcludeColor,
}) => {
  const [isDragOver, setIsDragOver] = useState(false);
  const [isGroupModalOpen, setIsGroupModalOpen] = useState(false);
  const [pendingDropFiles, setPendingDropFiles] = useState<File[]>([]);
  const [editingItem, setEditingItem] = useState<QueuedImage | null>(null);
  const [selectedMangaFilter, setSelectedMangaFilter] = useState<string>("all");
  const [mangaSearchQuery, setMangaSearchQuery] = useState<string>("");

  const handleFileChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const files = Array.from(e.target.files || []);
    if (files.length > 0) {
      setPendingDropFiles(files);
      setIsGroupModalOpen(true);
      e.target.value = "";
    }
  };

  const handleDrop = (e: React.DragEvent<HTMLDivElement>) => {
    e.preventDefault();
    setIsDragOver(false);
    const files = Array.from(e.dataTransfer.files || []);
    if (files.length > 0) {
      setPendingDropFiles(files);
      setIsGroupModalOpen(true);
    }
  };

  const handleConfirmGroup = (chosenGroup: MangaGroupSelection | string) => {
    const title = typeof chosenGroup === "string" ? chosenGroup : chosenGroup.title;
    const clean = title.trim() || "Ungrouped";
    if (editingItem) {
      onUpdateItemMangaTitle?.(editingItem.id, clean);
      setEditingItem(null);
    } else if (pendingDropFiles.length > 0) {
      onAddToQueue(pendingDropFiles, clean);
      if (onMangaTitleChange && clean !== "Ungrouped") {
        onMangaTitleChange(clean);
      }
      setPendingDropFiles([]);
    }
    setIsGroupModalOpen(false);
  };

  const handleCloseModal = () => {
    setIsGroupModalOpen(false);
    setPendingDropFiles([]);
    setEditingItem(null);
  };

  const allSuggestedGroups = useMemo(() => {
    const set = new Set<string>(existingGroups);
    queuedImages.forEach((img) => {
      const title = (img.mangaTitle || "").trim();
      if (title && title !== "Ungrouped") set.add(title);
    });
    return Array.from(set).sort((a, b) =>
      a.localeCompare(b, undefined, { numeric: true, sensitivity: "base" })
    );
  }, [existingGroups, queuedImages]);

  const mangaTitlesInQueue = useMemo(() => {
    const map = new Map<string, number>();
    queuedImages.forEach((img) => {
      const title = img.mangaTitle || "Ungrouped";
      map.set(title, (map.get(title) || 0) + 1);
    });
    return Array.from(map.entries()).sort((a, b) => {
      if (a[0] === "Ungrouped") return 1;
      if (b[0] === "Ungrouped") return -1;
      return a[0].localeCompare(b[0], undefined, { numeric: true, sensitivity: "base" });
    });
  }, [queuedImages]);

  const searchedMangaTitlesInQueue = useMemo(() => {
    const q = mangaSearchQuery.trim().toLowerCase();
    if (!q) return mangaTitlesInQueue;
    return mangaTitlesInQueue.filter(([title]) => title.toLowerCase().includes(q));
  }, [mangaTitlesInQueue, mangaSearchQuery]);

  const displayedImages = useMemo(() => {
    if (selectedMangaFilter === "all") return queuedImages;
    return queuedImages.filter((img) => (img.mangaTitle || "Ungrouped") === selectedMangaFilter);
  }, [queuedImages, selectedMangaFilter]);

  const isPendingStage = (step?: string) => step === "awaiting_translation" || step === "reserved";
  const queuedCount = queuedImages.filter((img) => img.status === "queued" || isPendingStage(img.step)).length;
  const processingCount = queuedImages.filter((img) => img.status === "processing" && !isPendingStage(img.step)).length;
  const finishedCount = queuedImages.filter((img) => img.status === "finished").length;
  const totalCount = queuedImages.length;
  const progressPercent = totalCount > 0 ? Math.round((finishedCount / totalCount) * 100) : 0;

  return (
    <div className="space-y-4">
      {/* Queue Header & Actions */}
      <div className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-zinc-200 dark:border-zinc-800 bg-white dark:bg-zinc-900 p-4 shadow-xs">
        <div className="flex items-center space-x-3">
          <div className="flex items-center space-x-2">
            <h3 className="text-base font-semibold text-zinc-900 dark:text-zinc-100">
              Batch Pipeline
            </h3>
            <span className="rounded-full bg-zinc-100 dark:bg-zinc-800 px-2 py-0.5 text-xs font-medium text-zinc-600 dark:text-zinc-300">
              {queuedImages.length} {queuedImages.length === 1 ? "page" : "pages"}
            </span>
          </div>

          {isProcessing ? (
            <span className="flex items-center space-x-1.5 rounded-full bg-indigo-50 dark:bg-indigo-950/60 border border-indigo-200 dark:border-indigo-800 px-2.5 py-0.5 text-xs font-medium text-indigo-700 dark:text-indigo-300">
              <Icon icon="carbon:renew" className="h-3 w-3 animate-spin" />
              <span>
                Processing ({processingCount} active{queuedCount > 0 ? `, ${queuedCount} queued` : ""})
              </span>
            </span>
          ) : queuedCount > 0 ? (
            <span className="rounded-full bg-amber-50 dark:bg-amber-950/60 border border-amber-200 dark:border-amber-800 px-2.5 py-0.5 text-xs font-medium text-amber-700 dark:text-amber-300">
              {queuedCount} waiting
            </span>
          ) : null}
        </div>

        <div className="flex flex-wrap items-center gap-2.5">
          {/* Manga / Series Title Input */}
          {onMangaTitleChange && (
            <div className="flex items-center space-x-1.5 rounded-lg border border-zinc-200 dark:border-zinc-700 bg-zinc-50 dark:bg-zinc-800/80 px-2.5 py-1 text-xs">
              <Icon icon="carbon:book" className="w-3.5 h-3.5 text-zinc-400" />
              <input
                type="text"
                value={mangaTitle}
                onChange={(e) => onMangaTitleChange(e.target.value)}
                maxLength={MANGA_TITLE_MAX_LENGTH}
                placeholder="Manga/Chapter Title"
                className="bg-transparent border-none outline-hidden text-xs text-zinc-800 dark:text-zinc-200 placeholder-zinc-400 w-32 sm:w-44 focus:ring-0"
              />
            </div>
          )}

          {/* Concurrency Selector */}
          {onConcurrencyChange && (
            <div className="flex items-center space-x-1 rounded-lg border border-zinc-200 dark:border-zinc-700 bg-zinc-50 dark:bg-zinc-800/80 px-2 py-1 text-xs">
              <Icon icon="carbon:network-4" className="w-3.5 h-3.5 text-indigo-500" />
              <span className="text-zinc-500 dark:text-zinc-400 text-xs hidden sm:inline">Parallel:</span>
              <select
                value={concurrency}
                onChange={(e) =>
                  onConcurrencyChange(
                    e.target.value === "auto" ? "auto" : parseInt(e.target.value, 10)
                  )
                }
                className="bg-transparent border-none outline-hidden text-xs font-medium text-zinc-800 dark:text-zinc-200 cursor-pointer focus:ring-0 pr-1"
                title="Concurrent image translation limit"
              >
                <option value="auto" className="bg-white dark:bg-zinc-900">
                  Auto ({serverWorkers} workers)
                </option>
                <option value="1" className="bg-white dark:bg-zinc-900">1 (Sequential)</option>
                <option value="2" className="bg-white dark:bg-zinc-900">2 Parallel</option>
                <option value="3" className="bg-white dark:bg-zinc-900">3 Parallel</option>
                <option value="4" className="bg-white dark:bg-zinc-900">4 Parallel</option>
                <option value="6" className="bg-white dark:bg-zinc-900">6 Parallel</option>
                <option value="8" className="bg-white dark:bg-zinc-900">8 Parallel</option>
              </select>
            </div>
          )}

          {/* Auto Process Toggle */}
          {onToggleAutoProcess && (
            <label className="flex items-center space-x-2 text-xs text-zinc-600 dark:text-zinc-300 cursor-pointer select-none">
              <input
                type="checkbox"
                checked={autoProcess}
                onChange={(e) => onToggleAutoProcess(e.target.checked)}
                className="rounded border-zinc-300 text-indigo-600 focus:ring-indigo-500 dark:border-zinc-700 dark:bg-zinc-800 h-4 w-4"
              />
              <span>Auto-advance</span>
            </label>
          )}

          {/* Stop Queue button */}
          {isProcessing && onStopQueue && (
            <button
              type="button"
              onClick={onStopQueue}
              className="flex items-center space-x-1.5 rounded-lg bg-rose-600 px-3 py-1.5 text-xs font-semibold text-white shadow-xs hover:bg-rose-500 transition-colors"
            >
              <Icon icon="carbon:stop-filled-alt" className="h-3.5 w-3.5" />
              <span>Stop Queue</span>
            </button>
          )}

          {/* Start Queue button */}
          {!isProcessing && queuedCount > 0 && onStartQueue && (
            <button
              type="button"
              onClick={onStartQueue}
              className="flex items-center space-x-1.5 rounded-lg bg-indigo-600 px-3 py-1.5 text-xs font-semibold text-white shadow-xs hover:bg-indigo-500 transition-colors"
            >
              <Icon icon="carbon:play" className="h-3.5 w-3.5" />
              <span>Process Queue ({queuedCount})</span>
            </button>
          )}

          {/* Cancel Waiting button */}
          {queuedCount > 0 && onClearWaiting && (
            <button
              type="button"
              onClick={onClearWaiting}
              className="flex items-center space-x-1 rounded-lg px-2.5 py-1.5 text-xs text-rose-600 hover:text-rose-700 dark:text-rose-400 dark:hover:text-rose-300 hover:bg-rose-50 dark:hover:bg-rose-950/40 transition-colors"
              title="Remove all waiting images from the queue"
            >
              <Icon icon="carbon:clean" className="h-3.5 w-3.5" />
              <span>Cancel Waiting ({queuedCount})</span>
            </button>
          )}

          {/* Clear Completed button */}
          {finishedCount > 0 && onClearCompleted && (
            <button
              type="button"
              onClick={onClearCompleted}
              className="flex items-center space-x-1 rounded-lg px-2.5 py-1.5 text-xs text-zinc-500 hover:text-zinc-700 dark:text-zinc-400 dark:hover:text-zinc-200 hover:bg-zinc-100 dark:hover:bg-zinc-800 transition-colors"
            >
              <span>Clear Completed</span>
            </button>
          )}
        </div>
      </div>

      {/* Progress Bar when images in queue */}
      {totalCount > 0 && (
        <div className="space-y-1.5">
          <div className="flex justify-between text-xs text-zinc-500 dark:text-zinc-400">
            <span>Progress: {finishedCount} of {totalCount} completed</span>
            <span>{progressPercent}%</span>
          </div>
          <div className="w-full h-2 rounded-full bg-zinc-100 dark:bg-zinc-800 overflow-hidden">
            <div
              className="h-full bg-indigo-600 transition-all duration-300 rounded-full"
              style={{ width: `${progressPercent}%` }}
            ></div>
          </div>
        </div>
      )}

      {/* Add Images Dropzone for Queue */}
      <div
        className={`rounded-xl border-2 border-dashed p-4 text-center cursor-pointer transition-colors ${
          isDragOver
            ? "border-indigo-500 bg-indigo-50/40 dark:bg-indigo-950/20"
            : "border-zinc-300 dark:border-zinc-800 hover:border-indigo-400 dark:hover:border-indigo-500"
        }`}
        onDrop={handleDrop}
        onDragOver={(e) => {
          e.preventDefault();
          setIsDragOver(true);
        }}
        onDragEnter={(e) => {
          e.preventDefault();
          setIsDragOver(true);
        }}
        onDragLeave={(e) => {
          e.preventDefault();
          setIsDragOver(false);
        }}
      >
        <label htmlFor="queue-file-input" className="cursor-pointer block">
          <div className="flex items-center justify-center space-x-2 text-xs font-medium text-zinc-600 dark:text-zinc-300">
            <Icon icon="carbon:add-alt" className="h-4 w-4 text-indigo-600 dark:text-indigo-400" />
            <span>Drop additional chapters or pages to enqueue</span>
          </div>
          <input
            id="queue-file-input"
            type="file"
            multiple
            accept="image/png,image/jpeg,image/bmp,image/webp"
            className="hidden"
            onChange={handleFileChange}
          />
        </label>
      </div>

      {/* Manga Filter Pills when multiple manga groups in queue */}
      {mangaTitlesInQueue.length > 1 && (
        <div className="flex items-center space-x-2 overflow-x-auto pb-1 pt-1 text-xs">
          <span className="text-zinc-400 dark:text-zinc-500 font-medium whitespace-nowrap">Filter by manga:</span>
          {mangaTitlesInQueue.length > 2 && (
            <div className="relative flex items-center shrink-0">
              <div className="absolute left-2.5 pointer-events-none text-zinc-400">
                <Icon icon="carbon:search" className="h-3 w-3" />
              </div>
              <input
                type="text"
                value={mangaSearchQuery}
                onChange={(e) => setMangaSearchQuery(e.target.value)}
                placeholder="Search manga..."
                className="rounded-lg border border-zinc-200 dark:border-zinc-800 bg-white dark:bg-zinc-900 pl-7 pr-6 py-1 text-xs text-zinc-800 dark:text-zinc-200 placeholder-zinc-400 focus:border-indigo-500 focus:outline-none w-28 sm:w-36 transition-all"
              />
              {mangaSearchQuery && (
                <button
                  type="button"
                  onClick={() => setMangaSearchQuery("")}
                  className="absolute right-1.5 text-zinc-400 hover:text-zinc-600 dark:hover:text-zinc-200 p-0.5 cursor-pointer"
                  title="Clear search"
                >
                  <Icon icon="carbon:close" className="h-3 w-3" />
                </button>
              )}
            </div>
          )}
          <button
            type="button"
            onClick={() => setSelectedMangaFilter("all")}
            className={`rounded-lg px-2.5 py-1 font-medium transition-colors whitespace-nowrap cursor-pointer ${
              selectedMangaFilter === "all"
                ? "bg-indigo-600 text-white shadow-xs"
                : "bg-zinc-100 dark:bg-zinc-800 text-zinc-600 dark:text-zinc-300 hover:bg-zinc-200 dark:hover:bg-zinc-700"
            }`}
          >
            All ({queuedImages.length})
          </button>
          {searchedMangaTitlesInQueue.map(([title, count]) => (
            <button
              key={title}
              type="button"
              onClick={() => setSelectedMangaFilter(title)}
              className={`flex items-center space-x-1.5 rounded-lg px-2.5 py-1 font-medium transition-colors whitespace-nowrap cursor-pointer ${
                selectedMangaFilter === title
                  ? "bg-indigo-600 text-white shadow-xs"
                  : "bg-zinc-100 dark:bg-zinc-800 text-zinc-600 dark:text-zinc-300 hover:bg-zinc-200 dark:hover:bg-zinc-700"
              }`}
            >
              <Icon icon="carbon:book" className="h-3 w-3 opacity-70" />
              <span>{title}</span>
              <span className={`rounded-full px-1.5 py-0.2 text-[10px] font-semibold ${
                selectedMangaFilter === title ? "bg-white/20 text-white" : "bg-black/10 dark:bg-white/10"
              }`}>
                {count}
              </span>
            </button>
          ))}
          {mangaSearchQuery && searchedMangaTitlesInQueue.length === 0 && (
            <span className="text-xs text-zinc-400 italic whitespace-nowrap py-1 px-1">
              No matching manga
            </span>
          )}
        </div>
      )}

      {/* Items list */}
      {queuedImages.length > 0 ? (
        <div className="space-y-2.5 max-h-[600px] overflow-y-auto pr-1">
          {displayedImages.map((img) => (
            <QueueItemRow
              key={img.id}
              item={img}
              onRemove={onRemoveFromQueue}
              onOpenLightbox={onOpenLightbox}
              isColorizerActive={isColorizerActive}
              onToggleExcludeColor={onToggleExcludeColor}
              onEditMangaTitle={(item) => {
                setEditingItem(item);
                setIsGroupModalOpen(true);
              }}
            />
          ))}
        </div>
      ) : (
        <div className="text-center py-12 rounded-xl border border-dashed border-zinc-200 dark:border-zinc-800 text-zinc-400">
          <Icon icon="carbon:in-progress" className="h-8 w-8 mx-auto mb-2 opacity-50" />
          <p className="text-sm font-medium">Queue is currently empty</p>
          <p className="text-xs text-zinc-500 mt-1">Drop comic pages above to queue sequential translation</p>
        </div>
      )}

      {/* Manga Group Selection / Assignment Modal */}
      <GroupSelectionModal
        isOpen={isGroupModalOpen}
        onClose={handleCloseModal}
        onConfirm={handleConfirmGroup}
        initialGroupName={
          editingItem
            ? (editingItem.mangaTitle || "Ungrouped")
            : mangaTitle || "Ungrouped"
        }
        existingGroups={allSuggestedGroups}
        pageCount={editingItem ? 1 : pendingDropFiles.length}
        title={editingItem ? "Reassign Manga Group" : "Queue into Manga Group"}
        subtitle={
          editingItem
            ? `Updating manga group for "${editingItem.file.name}"`
            : `Queueing ${pendingDropFiles.length} ${pendingDropFiles.length === 1 ? "page" : "pages"} for later translation`
        }
        actionLabel={editingItem ? "Reassign Page" : "Add to Queue"}
        icon="carbon:folder-add"
      />
    </div>
  );
};
