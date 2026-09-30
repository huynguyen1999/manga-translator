import { useEffect, useRef, useState, type Dispatch, type SetStateAction } from 'react';
import { Icon } from '@iconify/react';
import type { GallerySort } from '@/utils/routeState';

export function shouldFocusMangaSearch(event: KeyboardEvent) {
  if (event.key !== '/' || event.altKey || event.ctrlKey || event.metaKey) return false;
  const target = event.target as (EventTarget & { tagName?: string; isContentEditable?: boolean }) | null;
  return !target?.isContentEditable && !['INPUT', 'TEXTAREA', 'SELECT'].includes(target?.tagName ?? '');
}

interface GalleryFilterControlsProps {
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

export function GalleryFilterControls({
  statusFilter, minPages, maxPages, handleSourceChange, toggleStatusFlag,
  handlePageRangeChange, handleClearFilters, mangaSearchInput, setMangaSearchInput,
  handleSearchSubmit, sortBy, setSortBy, setGalleryPage, onGallerySortChange,
}: GalleryFilterControlsProps) {
  const mangaSearchRef = useRef<HTMLInputElement>(null);
  const tokens = (statusFilter && statusFilter !== 'all') ? statusFilter.split(',').map((s) => s.trim()).filter(Boolean) : [];
  const currentSource: 'all' | 'translated' | 'original' = tokens.includes('translated') ? 'translated' : tokens.includes('original') ? 'original' : 'all';
  const isSummarizedActive = tokens.includes('summarized');
  const isReviewActive = tokens.includes('review');

  const [minInput, setMinInput] = useState(minPages != null ? String(minPages) : '');
  const [maxInput, setMaxInput] = useState(maxPages != null ? String(maxPages) : '');
  const [isPageRangeFocused, setIsPageRangeFocused] = useState(false);

  useEffect(() => { setMinInput(minPages != null ? String(minPages) : ''); }, [minPages]);
  useEffect(() => { setMaxInput(maxPages != null ? String(maxPages) : ''); }, [maxPages]);

  const commitPageRange = (newMinStr: string, newMaxStr: string) => {
    const minVal = parseInt(newMinStr, 10);
    const maxVal = parseInt(newMaxStr, 10);
    const parsedMin = Number.isInteger(minVal) && minVal > 0 ? minVal : undefined;
    const parsedMax = Number.isInteger(maxVal) && maxVal > 0 ? maxVal : undefined;
    handlePageRangeChange(parsedMin, parsedMax);
  };

  const handlePageRangeSubmit = (e?: React.FormEvent) => {
    e?.preventDefault();
    commitPageRange(minInput, maxInput);
  };

  const hasActiveFilters = currentSource !== 'all' || isSummarizedActive || isReviewActive || minPages != null || maxPages != null || Boolean(mangaSearchInput.trim());

  useEffect(() => {
    const focusSearch = (event: KeyboardEvent) => {
      if (!shouldFocusMangaSearch(event) || document.querySelector('[role="dialog"][aria-modal="true"], dialog[open]')) return;
      event.preventDefault();
      mangaSearchRef.current?.focus();
    };
    window.addEventListener('keydown', focusSearch);
    return () => window.removeEventListener('keydown', focusSearch);
  }, []);

  return (
    <div className="grid w-full grid-cols-2 items-stretch gap-2 sm:flex sm:flex-wrap sm:items-center">
      {/* Search is first so the main gallery action is immediately available. */}
      <div className="relative col-span-2 flex min-w-0 items-center sm:min-w-[13rem] sm:flex-1">
        <Icon icon="carbon:search" className="pointer-events-none absolute left-3 h-4 w-4 text-zinc-400" />
        <input
          ref={mangaSearchRef}
          type="text"
          value={mangaSearchInput}
          onChange={(e) => setMangaSearchInput(e.target.value)}
          onKeyDown={(e) => { if (e.key === 'Enter') { e.preventDefault(); handleSearchSubmit(mangaSearchInput); } }}
          placeholder="Search manga..."
          aria-label="Search manga"
          aria-keyshortcuts="/"
          className="h-11 w-full rounded-lg border border-zinc-200 bg-zinc-50 pl-10 pr-12 text-sm text-zinc-800 placeholder-zinc-400 transition-colors focus-visible:border-indigo-500 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500/40 dark:border-zinc-700 dark:bg-zinc-800 dark:text-zinc-200 sm:h-10"
        />
        {!mangaSearchInput && (
          <kbd className="pointer-events-none absolute right-3 hidden rounded border border-zinc-300 bg-white px-1.5 py-0.5 text-[11px] font-medium text-zinc-500 shadow-xs dark:border-zinc-600 dark:bg-zinc-900 dark:text-zinc-400 sm:inline-flex">/</kbd>
        )}
        {mangaSearchInput && (
          <button
            type="button"
            onClick={() => { setMangaSearchInput(''); handleSearchSubmit(''); }}
            className="absolute right-0 top-0 flex h-11 w-11 items-center justify-center rounded-r-lg text-zinc-400 hover:text-zinc-600 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-indigo-500 dark:hover:text-zinc-200 sm:h-10 sm:w-10"
            title="Clear search"
            aria-label="Clear search"
          >
            <Icon icon="carbon:close" className="h-4 w-4" />
          </button>
        )}
      </div>

      {/* Source Type Selector */}
      <div className="col-span-2 flex h-11 min-w-0 items-center gap-2 rounded-lg border border-zinc-200 bg-zinc-50 px-3 text-sm text-zinc-700 dark:border-zinc-700 dark:bg-zinc-800 dark:text-zinc-300 sm:h-10 sm:w-auto sm:flex-none">
        <Icon icon="carbon:filter" className="w-3.5 h-3.5 text-zinc-400" />
        <select
          value={currentSource}
          aria-label="Filter manga status"
          onChange={(e) => handleSourceChange(e.target.value as 'all' | 'translated' | 'original')}
          className="min-w-0 flex-1 cursor-pointer bg-transparent font-medium outline-hidden focus-visible:rounded focus-visible:ring-2 focus-visible:ring-indigo-500/60 sm:flex-initial"
        >
          <option value="all" className="dark:bg-zinc-800">All Manga</option>
          <option value="translated" className="dark:bg-zinc-800">Translated</option>
          <option value="original" className="dark:bg-zinc-800">Originals (Raw)</option>
        </select>
      </div>

      {/* Summarized Toggle */}
      <button
        type="button"
        onClick={() => toggleStatusFlag('summarized')}
        className={`col-span-1 inline-flex h-11 min-w-0 items-center justify-center gap-1.5 whitespace-nowrap rounded-lg border px-2.5 text-xs font-medium transition-colors cursor-pointer focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500/60 sm:h-10 sm:flex-none ${
          isSummarizedActive
            ? 'border-indigo-400 bg-indigo-50 text-indigo-700 dark:border-indigo-600 dark:bg-indigo-950/70 dark:text-indigo-300'
            : 'border-zinc-200 bg-zinc-50 text-zinc-600 hover:bg-zinc-100 dark:border-zinc-700 dark:bg-zinc-800 dark:text-zinc-400 dark:hover:bg-zinc-700'
        }`}
        aria-pressed={isSummarizedActive}
        title="Filter manga with generated summaries"
      >
        <Icon icon="carbon:notebook" className="h-3.5 w-3.5" />
        <span>Summarized</span>
      </button>

      {/* Needs Review Toggle */}
      <button
        type="button"
        onClick={() => toggleStatusFlag('review')}
        className={`col-span-1 inline-flex h-11 min-w-0 items-center justify-center gap-1.5 whitespace-nowrap rounded-lg border px-2.5 text-xs font-medium transition-colors cursor-pointer focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-amber-500/60 sm:h-10 sm:flex-none ${
          isReviewActive
            ? 'border-amber-400 bg-amber-50 text-amber-800 dark:border-amber-600 dark:bg-amber-950/70 dark:text-amber-300'
            : 'border-zinc-200 bg-zinc-50 text-zinc-600 hover:bg-zinc-100 dark:border-zinc-700 dark:bg-zinc-800 dark:text-zinc-400 dark:hover:bg-zinc-700'
        }`}
        aria-pressed={isReviewActive}
        title="Filter manga with pages requiring review"
      >
        <Icon icon="carbon:warning-alt" className="h-3.5 w-3.5" />
        <span>Needs Review</span>
      </button>

      {/* Page Range Filter */}
      <form
        onSubmit={handlePageRangeSubmit}
        onFocus={() => setIsPageRangeFocused(true)}
        onBlur={(e) => {
          if (!e.currentTarget.contains(e.relatedTarget)) {
            setIsPageRangeFocused(false);
          }
        }}
        className={`col-span-2 flex h-11 min-w-0 items-center justify-between gap-2 rounded-lg border px-3 text-sm transition-colors sm:h-10 sm:w-auto sm:flex-none ${
          minPages != null || maxPages != null
            ? 'border-indigo-400 bg-indigo-50/50 dark:border-indigo-700 dark:bg-indigo-950/40 text-zinc-800 dark:text-zinc-200'
            : 'border-zinc-200 dark:border-zinc-700 bg-zinc-50 dark:bg-zinc-800 text-zinc-700 dark:text-zinc-300'
        }`}
        title="Filter by page count range (Press Enter to apply)"
      >
        <Icon icon="carbon:document-multiple-01" className="w-3.5 h-3.5 text-zinc-400 shrink-0" />
        <span className="text-xs text-zinc-500 dark:text-zinc-400">Pages</span>
        <input
          type="number"
          min={1}
          placeholder="Min"
          value={minInput}
          onChange={(e) => setMinInput(e.target.value)}
          onKeyDown={(e) => { if (e.key === 'Enter') { e.preventDefault(); handlePageRangeSubmit(); } }}
          aria-label="Minimum page count"
          className="w-12 rounded bg-transparent text-center text-xs text-zinc-800 outline-none placeholder-zinc-400 focus-visible:ring-2 focus-visible:ring-indigo-500/60 dark:text-zinc-200 sm:w-10 [appearance:textfield] [&::-webkit-outer-spin-button]:appearance-none [&::-webkit-inner-spin-button]:appearance-none"
        />
        <span className="text-zinc-400">–</span>
        <input
          type="number"
          min={1}
          placeholder="Max"
          value={maxInput}
          onChange={(e) => setMaxInput(e.target.value)}
          onKeyDown={(e) => { if (e.key === 'Enter') { e.preventDefault(); handlePageRangeSubmit(); } }}
          aria-label="Maximum page count"
          className="w-12 rounded bg-transparent text-center text-xs text-zinc-800 outline-none placeholder-zinc-400 focus-visible:ring-2 focus-visible:ring-indigo-500/60 dark:text-zinc-200 sm:w-10 [appearance:textfield] [&::-webkit-outer-spin-button]:appearance-none [&::-webkit-inner-spin-button]:appearance-none"
        />
        {isPageRangeFocused && (
          <button
            type="submit"
            className="flex h-11 w-11 shrink-0 cursor-pointer items-center justify-center rounded text-indigo-600 hover:text-indigo-700 hover:bg-indigo-100/60 dark:text-indigo-400 dark:hover:text-indigo-300 dark:hover:bg-indigo-950/60 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500 sm:h-7 sm:w-7 transition-colors"
            title="Apply page range (Enter)"
            aria-label="Apply page range"
          >
            <Icon icon="carbon:checkmark" className="h-3.5 w-3.5" />
          </button>
        )}
        {(minPages != null || maxPages != null || minInput || maxInput) && (
          <button
            type="button"
            onClick={() => { setMinInput(''); setMaxInput(''); handlePageRangeChange(undefined, undefined); }}
            className="ml-0.5 flex h-11 w-11 shrink-0 cursor-pointer items-center justify-center text-zinc-400 hover:text-zinc-600 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500 dark:hover:text-zinc-200 sm:h-7 sm:w-7"
            title="Clear page range"
            aria-label="Clear page range"
          >
            <Icon icon="carbon:close" className="h-3 w-3" />
          </button>
        )}
      </form>

      {/* Sort Selector */}
      <div className="col-span-2 flex h-11 min-w-0 items-center gap-2 rounded-lg border border-zinc-200 bg-zinc-50 px-3 text-sm text-zinc-700 dark:border-zinc-700 dark:bg-zinc-800 dark:text-zinc-300 sm:h-10 sm:w-auto sm:flex-none">
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
          className="min-w-0 flex-1 cursor-pointer bg-transparent font-medium outline-hidden focus-visible:rounded focus-visible:ring-2 focus-visible:ring-indigo-500/60 sm:flex-initial"
        >
          <option value="alpha-asc" className="dark:bg-zinc-800">Alphabetical (A → Z)</option>
          <option value="alpha-desc" className="dark:bg-zinc-800">Alphabetical (Z → A)</option>
          <option value="date-desc" className="dark:bg-zinc-800">Newest First</option>
          <option value="date-asc" className="dark:bg-zinc-800">Oldest First</option>
        </select>
      </div>

      {/* Reset Filters Button */}
      {hasActiveFilters && (
        <button
          type="button"
          onClick={handleClearFilters}
          className="col-span-2 ml-auto inline-flex h-11 cursor-pointer items-center gap-1.5 rounded-lg border border-dashed border-zinc-300 px-3 text-xs text-zinc-600 transition-colors hover:text-indigo-600 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500 dark:border-zinc-600 dark:text-zinc-400 dark:hover:text-indigo-400 sm:h-10 sm:flex-none"
          title="Reset all filters and search"
        >
          <Icon icon="carbon:filter-reset" className="h-3.5 w-3.5" />
          <span>Reset</span>
        </button>
      )}
    </div>
  );
}
