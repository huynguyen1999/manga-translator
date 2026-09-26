import React from "react";
import { Icon } from "@iconify/react";

export interface CreatedSeriesToast {
  seriesId: string;
  title: string;
  count: number;
}

interface MangaSeriesSelectionDockProps {
  selectedCount: number;
  isSingleMangaView: boolean;
  canDeleteSelection: boolean;
  createdSeriesToast: CreatedSeriesToast | null;
  onClearSelection: () => void;
  onDeleteSelection: () => void;
  onOpenSeriesManager: () => void;
  onDismissToast: () => void;
  onOpenSeries?: (seriesId: string) => void;
}

export const MangaSeriesSelectionDock: React.FC<
  MangaSeriesSelectionDockProps
> = ({
  selectedCount,
  isSingleMangaView,
  canDeleteSelection,
  createdSeriesToast,
  onClearSelection,
  onDeleteSelection,
  onOpenSeriesManager,
  onDismissToast,
  onOpenSeries,
}) => (
  <>
    {/* Floating Selection Dock for Series Actions */}
    {selectedCount > 0 && !isSingleMangaView && (
      <div
        role="region"
        aria-label="Series selection toolbar"
        className="fixed bottom-3 sm:bottom-6 left-1/2 -translate-x-1/2 z-40 w-[calc(100%-1.25rem)] sm:w-[calc(100%-2rem)] max-w-lg transition-all"
      >
        <div className="flex items-center justify-between gap-2.5 sm:gap-3 rounded-2xl border border-indigo-200/80 bg-white/95 p-2.5 sm:p-3 shadow-2xl backdrop-blur-md dark:border-indigo-900/80 dark:bg-zinc-900/95 dark:shadow-indigo-950/40">
          <div className="flex items-center gap-2 sm:gap-2.5 min-w-0">
            <span className="flex h-7 w-7 sm:h-8 sm:w-8 shrink-0 items-center justify-center rounded-xl bg-indigo-600 text-white font-bold text-xs shadow-xs">
              {selectedCount}
            </span>
            <div className="min-w-0">
              <p className="text-xs font-semibold text-zinc-900 dark:text-zinc-100 truncate">
                {selectedCount} {selectedCount === 1 ? "manga" : "manga"}{" "}
                selected
              </p>
              <p className="text-[10px] sm:text-[11px] text-zinc-500 dark:text-zinc-400 truncate">
                {selectedCount < 2
                  ? "Add to series or select more to create new"
                  : "Ready to create or add to series"}
              </p>
            </div>
          </div>

          <div className="flex items-center gap-1.5 sm:gap-2 shrink-0">
            <button
              type="button"
              onClick={() => onClearSelection()}
              className="rounded-xl px-2.5 py-1.5 text-xs font-medium text-zinc-600 hover:bg-zinc-100 dark:text-zinc-400 dark:hover:bg-zinc-800 transition-colors cursor-pointer touch-manipulation"
              title="Clear selection (Esc)"
            >
              Clear
            </button>
            {canDeleteSelection && (
              <button
                type="button"
                onClick={() => onDeleteSelection()}
                disabled={selectedCount === 0}
                className="flex items-center gap-1.5 rounded-xl bg-red-600 px-3 sm:px-3.5 py-2 text-xs font-semibold text-white shadow-md shadow-red-600/20 hover:bg-red-500 disabled:cursor-not-allowed disabled:opacity-50 transition-all cursor-pointer touch-manipulation"
                title={`Delete ${selectedCount} selected manga`}
              >
                <Icon icon="carbon:trash-can" className="h-4 w-4" />
                <span>Delete</span>
              </button>
            )}
            <button
              type="button"
              onClick={onOpenSeriesManager}
              disabled={selectedCount === 0}
              className="flex items-center gap-1.5 rounded-xl bg-indigo-600 px-3 sm:px-3.5 py-2 text-xs font-semibold text-white shadow-md shadow-indigo-600/20 hover:bg-indigo-500 disabled:cursor-not-allowed disabled:opacity-50 transition-all cursor-pointer touch-manipulation"
              title="Create a new series or add selected manga to an existing series"
            >
              <Icon icon="carbon:folders" className="h-4 w-4" />
              <span>Series</span>
            </button>
          </div>
        </div>
      </div>
    )}

    {/* Floating Success Toast for Series Creation */}
    {createdSeriesToast && (
      <div
        role="status"
        aria-live="polite"
        className="fixed bottom-3 sm:bottom-6 right-3 sm:right-6 z-50 w-[calc(100%-1.5rem)] sm:w-auto sm:max-w-sm transition-all"
      >
        <div className="flex items-start justify-between gap-3 rounded-2xl border border-emerald-200/80 bg-white/95 p-3 sm:p-3.5 shadow-2xl backdrop-blur-md dark:border-emerald-900/80 dark:bg-zinc-900/95">
          <div className="flex items-start gap-2.5 min-w-0">
            <span className="flex h-7 w-7 shrink-0 items-center justify-center rounded-xl bg-emerald-600 text-white shadow-xs">
              <Icon icon="carbon:checkmark" className="h-4 w-4" />
            </span>
            <div className="min-w-0">
              <p className="text-xs font-semibold text-zinc-900 dark:text-zinc-100">
                Series created
              </p>
              <p
                className="text-xs text-zinc-600 dark:text-zinc-300 truncate"
                title={createdSeriesToast.title}
              >
                “{createdSeriesToast.title}” ({createdSeriesToast.count} manga)
              </p>
              {onOpenSeries && (
                <button
                  type="button"
                  onClick={() => onOpenSeries(createdSeriesToast.seriesId)}
                  className="mt-1.5 inline-flex items-center gap-1 text-xs font-semibold text-indigo-600 hover:text-indigo-500 dark:text-indigo-400 dark:hover:text-indigo-300 transition-colors cursor-pointer touch-manipulation"
                >
                  <span>View series</span>
                  <Icon icon="carbon:arrow-right" className="h-3 w-3" />
                </button>
              )}
            </div>
          </div>
          <button
            type="button"
            onClick={() => onDismissToast()}
            className="rounded-lg p-1 text-zinc-400 hover:bg-zinc-100 hover:text-zinc-600 dark:hover:bg-zinc-800 dark:hover:text-zinc-200 transition-colors cursor-pointer touch-manipulation"
            aria-label="Dismiss notification"
          >
            <Icon icon="carbon:close" className="h-4 w-4" />
          </button>
        </div>
      </div>
    )}
  </>
);
