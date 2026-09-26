import { Icon } from '@iconify/react';
import type { Dispatch, SetStateAction } from 'react';
import type { GallerySort } from '@/utils/routeState';
import type { MangaStatusFilter } from '@/utils/resultGallery';

interface GalleryToolbarProps {
  effectiveIsLoading: boolean;
  totalImagesCount: number;
  galleryMangaCount: number;
  reviewCount: number;
  reviewOnly: boolean;
  onGalleryReviewChange?: (reviewOnly: boolean) => void;
  statusFilter: MangaStatusFilter;
  handleStatusFilterChange: (nextFilter: MangaStatusFilter) => void;
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
  handleStatusFilterChange,
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
        <div className="flex w-full flex-wrap items-center justify-end gap-2 sm:w-auto">
          {/* Status Filter Selector */}
          <div className="flex items-center space-x-1 rounded-lg border border-zinc-200 dark:border-zinc-700 bg-zinc-50 dark:bg-zinc-800 px-2.5 py-1 text-xs text-zinc-700 dark:text-zinc-300">
            <Icon icon="carbon:filter" className="w-3.5 h-3.5 text-zinc-400" />
            <select
              value={statusFilter}
              aria-label="Filter manga status"
              onChange={(e) => handleStatusFilterChange(e.target.value as MangaStatusFilter)}
              className="bg-transparent border-none outline-hidden text-xs cursor-pointer font-medium"
            >
              <option value="all" className="dark:bg-zinc-800">All Manga</option>
              <option value="original" className="dark:bg-zinc-800">Originals (Raw)</option>
              <option value="translated" className="dark:bg-zinc-800">Translated</option>
              <option value="summarized" className="dark:bg-zinc-800">Summarized</option>
              <option value="review" className="dark:bg-zinc-800">Needs Review</option>
            </select>
          </div>

          {/* Search Bar */}
          <div className="relative flex items-center">
            <div className="absolute left-2.5 pointer-events-none text-zinc-400">
              <Icon icon="carbon:search" className="h-3.5 w-3.5" />
            </div>
            <input
              type="text"
              value={mangaSearchInput}
              onChange={(e) => setMangaSearchInput(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter') {
                  e.preventDefault();
                  handleSearchSubmit(mangaSearchInput);
                }
              }}
              placeholder="Search manga..."
              className="rounded-lg border border-zinc-200 dark:border-zinc-700 bg-zinc-50 dark:bg-zinc-800 pl-8 pr-7 py-1.5 text-xs text-zinc-800 dark:text-zinc-200 placeholder-zinc-400 focus:border-indigo-500 focus:outline-none w-36 sm:w-48 transition-all"
            />
            {mangaSearchInput && (
              <button
                type="button"
                onClick={() => {
                  setMangaSearchInput('');
                  handleSearchSubmit('');
                }}
                className="absolute right-1.5 text-zinc-400 hover:text-zinc-600 dark:hover:text-zinc-200 p-0.5 cursor-pointer"
                title="Clear search"
              >
                <Icon icon="carbon:close" className="h-3.5 w-3.5" />
              </button>
            )}
          </div>

          {/* Sort Selector */}
          <div className="flex items-center space-x-1 rounded-lg border border-zinc-200 dark:border-zinc-700 bg-zinc-50 dark:bg-zinc-800 px-2.5 py-1 text-xs text-zinc-700 dark:text-zinc-300">
            <Icon icon="carbon:sort-ascending" className="w-3.5 h-3.5 text-zinc-400" />
            <select
              value={sortBy}
              aria-label="Sort manga"
              onChange={(e) => {
                const nextSort = e.target.value as GallerySort;
                setSortBy(nextSort);
                setGalleryPage(1);
                onGallerySortChange?.(nextSort);
              }}
              className="bg-transparent border-none outline-hidden text-xs cursor-pointer font-medium"
            >
              <option value="alpha-asc" className="dark:bg-zinc-800">Alphabetical (A → Z)</option>
              <option value="alpha-desc" className="dark:bg-zinc-800">Alphabetical (Z → A)</option>
              <option value="date-desc" className="dark:bg-zinc-800">Newest First</option>
              <option value="date-asc" className="dark:bg-zinc-800">Oldest First</option>
            </select>
          </div>

        </div>
      </div>
  );
}
