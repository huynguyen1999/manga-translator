import React from "react";
import { Icon } from "@iconify/react";
import type { MangaStatusFilter } from "@/utils/routeState";

export function GalleryNoResultsState({
  searchQuery,
  statusFilter,
  onResetFilters,
}: {
  searchQuery: string;
  statusFilter: MangaStatusFilter;
  onResetFilters: () => void;
}) {
  const statusLabel = statusFilter === "original"
    ? "original (raw)"
    : statusFilter === "translated"
      ? "translated"
      : statusFilter === "summarized"
        ? "summarized"
        : "needs review";

  return (
    <div className="text-center py-16 rounded-2xl border border-dashed border-zinc-200 dark:border-zinc-800 text-zinc-400">
      <Icon icon={statusFilter !== "all" ? "carbon:filter" : "carbon:search"} className="w-10 h-10 mx-auto mb-2 text-zinc-400" />
      <p className="text-sm font-semibold text-zinc-700 dark:text-zinc-300">
        {searchQuery.trim() && statusFilter !== "all"
          ? `No ${statusLabel} manga matching “${searchQuery}”`
          : searchQuery.trim()
            ? `No manga matching “${searchQuery}”`
            : `No ${statusLabel} manga found`}
      </p>
      <button
        type="button"
        onClick={onResetFilters}
        className="mt-3 inline-flex items-center space-x-1 text-xs text-indigo-600 dark:text-indigo-400 hover:underline font-medium cursor-pointer"
      >
        <span>Reset filters</span>
      </button>
    </div>
  );
}

export function GalleryLoadingState({ viewMode }: { viewMode: string }) {
  return (
    <div className="space-y-6 animate-pulse" aria-busy="true" aria-label="Loading manga gallery">
      {/* Top Gallery Header Skeleton */}
      <div className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-zinc-200 bg-white p-3 shadow-xs dark:border-zinc-800 dark:bg-zinc-900 sm:p-4">
        <div className="flex min-w-0 flex-wrap items-center gap-2 sm:gap-3">
          <div className="flex items-center space-x-2">
            <Icon icon="carbon:book" className="w-5 h-5 text-indigo-600/70 dark:text-indigo-400/70" />
            <h3 className="text-base font-semibold text-zinc-900 dark:text-zinc-100">
              Manga Gallery
            </h3>
          </div>
          <div className="flex items-center space-x-1.5">
            <span className="flex items-center space-x-1.5 rounded-full bg-indigo-50 dark:bg-indigo-950/60 border border-indigo-200/60 dark:border-indigo-800/60 px-2.5 py-0.5 text-xs font-medium text-indigo-700 dark:text-indigo-300">
              <Icon icon="carbon:renew" className="w-3 h-3 animate-spin text-indigo-600 dark:text-indigo-400" />
              <span>Loading library...</span>
            </span>
          </div>
        </div>

        {/* Skeletons for search & sort controls */}
        <div className="flex w-full flex-wrap items-center justify-end gap-2 sm:w-auto">
          <div className="h-7 w-36 sm:w-48 rounded-lg bg-zinc-100 dark:bg-zinc-800" />
          <div className="h-7 w-32 rounded-lg bg-zinc-100 dark:bg-zinc-800" />
        </div>
      </div>

      {/* Skeleton Grid */}
      {viewMode === "cards" ? (
        <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-5 gap-4 sm:gap-5">
          {Array.from({ length: 12 }).map((_, i) => (
            <div
              key={i}
              className="flex flex-col rounded-2xl border border-zinc-200 dark:border-zinc-800 bg-white dark:bg-zinc-900 overflow-hidden shadow-xs"
            >
              <div className="relative aspect-[3/4] w-full bg-zinc-100 dark:bg-zinc-950 flex flex-col items-center justify-center p-4">
                <div className="w-12 h-12 rounded-xl bg-zinc-200/80 dark:bg-zinc-800/60 flex items-center justify-center mb-2 shadow-inner">
                  <Icon icon="carbon:book" className="w-6 h-6 text-zinc-300 dark:text-zinc-700" />
                </div>
                <div className="absolute top-2.5 left-2.5 h-4 w-14 rounded-full bg-zinc-200 dark:bg-zinc-800" />
              </div>
              <div className="p-3 space-y-2">
                <div className="h-3.5 bg-zinc-200 dark:bg-zinc-700/60 rounded-md w-3/4" />
                <div className="h-3 bg-zinc-100 dark:bg-zinc-800/80 rounded-md w-2/5" />
              </div>
            </div>
          ))}
        </div>
      ) : (
        <div className="space-y-4">
          {Array.from({ length: 5 }).map((_, i) => (
            <div
              key={i}
              className="flex items-center justify-between p-4 rounded-2xl border border-zinc-200 dark:border-zinc-800 bg-white dark:bg-zinc-900 shadow-xs"
            >
              <div className="flex items-center space-x-3">
                <div className="w-12 h-16 rounded-xl bg-zinc-100 dark:bg-zinc-800 flex items-center justify-center">
                  <Icon icon="carbon:book" className="w-5 h-5 text-zinc-300 dark:text-zinc-700" />
                </div>
                <div className="space-y-2">
                  <div className="h-4 w-40 rounded bg-zinc-200 dark:bg-zinc-700/60" />
                  <div className="h-3 w-20 rounded bg-zinc-100 dark:bg-zinc-800" />
                </div>
              </div>
              <div className="h-7 w-24 rounded-lg bg-zinc-100 dark:bg-zinc-800" />
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

export function GalleryReviewClearState({ onBackToGallery }: { onBackToGallery: () => void }) {
  return (
    <div className="flex flex-col items-center justify-center rounded-2xl border border-emerald-800/70 bg-emerald-950/20 px-6 py-16 text-center text-emerald-100">
      <Icon icon="carbon:checkmark-filled" className="mb-3 h-10 w-10 text-emerald-400" />
      <p className="text-base font-semibold">Review queue is clear</p>
      <p className="mt-1 max-w-md text-xs leading-5 text-emerald-200/75">
        Every flagged page is approved. You can return to the full gallery whenever you’re ready.
      </p>
      <button
        type="button"
        onClick={onBackToGallery}
        className="mt-4 rounded-lg bg-emerald-600 px-3.5 py-2 text-xs font-semibold text-white transition-colors hover:bg-emerald-500 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-emerald-400"
      >
        Back to gallery
      </button>
    </div>
  );
}

export function GalleryEmptyState() {
  return (
    <div className="text-center py-16 rounded-2xl border border-dashed border-zinc-200 dark:border-zinc-800 text-zinc-400">
      <Icon icon="carbon:image" className="w-12 h-12 mx-auto mb-3 text-zinc-300 dark:text-zinc-700" />
      <p className="text-base font-semibold text-zinc-700 dark:text-zinc-300">
        No manga yet
      </p>
      <p className="text-xs text-zinc-500 mt-1">
        Upload manga pages from Studio to create a manga.
      </p>
    </div>
  );
}
