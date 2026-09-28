import { Icon } from '@iconify/react';
import type { Dispatch, SetStateAction } from 'react';
import type { GallerySort } from '@/utils/routeState';
import { GalleryFilterControls } from './GalleryFilterControls';

interface GalleryToolbarProps {
  effectiveIsLoading: boolean;
  totalImagesCount: number;
  galleryMangaCount: number;
  reviewCount: number;
  reviewOnly: boolean;
  onGalleryReviewChange?: (reviewOnly: boolean) => void;
  statusFilter: string;
  minPages?: number;
  maxPages?: number;
  handleSourceChange: (source: 'all' | 'translated' | 'original') => void;
  toggleStatusFlag: (flag: 'summarized' | 'review') => void;
  handlePageRangeChange: (min?: number, max?: number) => void;
  handleClearFilters: () => void;
  mangaSearchInput: string;
  setMangaSearchInput: Dispatch<SetStateAction<string>>;
  handleSearchSubmit: (query: string) => void;
  sortBy: GallerySort;
  setSortBy: Dispatch<SetStateAction<GallerySort>>;
  setGalleryPage: Dispatch<SetStateAction<number>>;
  onGallerySortChange?: (sort: GallerySort) => void;
}

export function GalleryToolbar({
  effectiveIsLoading,
  totalImagesCount,
  galleryMangaCount,
  reviewCount,
  reviewOnly,
  onGalleryReviewChange,
  statusFilter,
  minPages,
  maxPages,
  handleSourceChange,
  toggleStatusFlag,
  handlePageRangeChange,
  handleClearFilters,
  mangaSearchInput,
  setMangaSearchInput,
  handleSearchSubmit,
  sortBy,
  setSortBy,
  setGalleryPage,
  onGallerySortChange,
}: GalleryToolbarProps) {
  return (
    <div className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-zinc-200 bg-white p-3 shadow-xs dark:border-zinc-800 dark:bg-zinc-900 sm:p-4">
      <div className="flex min-w-0 flex-wrap items-center gap-2 sm:gap-3">
        <div className="flex items-center space-x-2">
          <Icon icon="carbon:book" className="w-5 h-5 text-indigo-600 dark:text-indigo-400" />
          <h3 className="text-base font-semibold text-zinc-900 dark:text-zinc-100">
            Manga Gallery
          </h3>
        </div>
        <div className="flex items-center space-x-1.5">
          <span className="flex items-center rounded-full bg-indigo-50 dark:bg-indigo-950/60 border border-indigo-200 dark:border-indigo-800 px-2.5 py-0.5 text-xs font-semibold text-indigo-700 dark:text-indigo-300">
            {effectiveIsLoading && (
              <Icon icon="carbon:renew" className="w-3 h-3 animate-spin mr-1 text-indigo-600 dark:text-indigo-400" />
            )}
            <span>{totalImagesCount} {totalImagesCount === 1 ? 'page' : 'pages'}</span>
          </span>
          <span className="rounded-full bg-zinc-100 dark:bg-zinc-800 px-2 py-0.5 text-xs font-medium text-zinc-600 dark:text-zinc-400">
            {galleryMangaCount} {galleryMangaCount === 1 ? 'manga' : 'manga series'}
          </span>
          {(reviewCount > 0 || reviewOnly) && (
            <button
              type="button"
              onClick={() => onGalleryReviewChange?.(!reviewOnly)}
              className={`inline-flex items-center gap-1 rounded-full border px-2.5 py-0.5 text-xs font-semibold transition-colors ${reviewOnly
                ? 'border-amber-300 bg-amber-100 text-amber-900 dark:border-amber-700 dark:bg-amber-950/50 dark:text-amber-200'
                : 'border-amber-200 bg-amber-50 text-amber-800 hover:bg-amber-100 dark:border-amber-800 dark:bg-amber-950/30 dark:text-amber-300 dark:hover:bg-amber-950/60'}`}
              aria-pressed={reviewOnly}
            >
              <Icon icon="carbon:warning-alt" className="h-3 w-3" />
              {reviewOnly ? `${reviewCount} ${reviewCount === 1 ? 'page' : 'pages'} to review` : `${reviewCount} needs review`}
            </button>
          )}
        </div>
      </div>

      {reviewOnly && (
        <div className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-amber-800/70 bg-amber-950/30 px-4 py-3 text-amber-100 shadow-xs">
          <div className="flex min-w-0 items-start gap-3">
            <span className="mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-amber-500/15 text-amber-300">
              <Icon icon="carbon:task" className="h-4 w-4" />
            </span>
            <div className="min-w-0">
              <p className="text-sm font-semibold">Review queue</p>
              <p className="mt-0.5 max-w-2xl text-xs leading-5 text-amber-200/80">
                Open a flagged page, resolve the highlighted bubbles, then choose <strong className="font-semibold text-amber-100">Save & approve</strong>. Approved pages leave this queue automatically.
              </p>
            </div>
          </div>
          <button
            type="button"
            onClick={() => onGalleryReviewChange?.(false)}
            className="shrink-0 rounded-lg border border-amber-700/70 px-3 py-1.5 text-xs font-semibold text-amber-200 transition-colors hover:bg-amber-900/50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-amber-400"
          >
            Show all pages
          </button>
        </div>
      )}

      {/* Global Actions, Search & Sort controls */}
      <GalleryFilterControls
        statusFilter={statusFilter}
        minPages={minPages}
        maxPages={maxPages}
        handleSourceChange={handleSourceChange}
        toggleStatusFlag={toggleStatusFlag}
        handlePageRangeChange={handlePageRangeChange}
        handleClearFilters={handleClearFilters}
        mangaSearchInput={mangaSearchInput}
        setMangaSearchInput={setMangaSearchInput}
        handleSearchSubmit={handleSearchSubmit}
        sortBy={sortBy}
        setSortBy={setSortBy}
        setGalleryPage={setGalleryPage}
        onGallerySortChange={onGallerySortChange}
      />
    </div>
  );
}
