import { useEffect, useState, type Dispatch, type SetStateAction } from 'react';
import { Icon } from '@iconify/react';
import type { GallerySort } from '@/utils/routeState';

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
}: GalleryFilterControlsProps) {
  const tokens = (statusFilter && statusFilter !== 'all')
    ? statusFilter.split(',').map((s) => s.trim()).filter(Boolean)
    : [];
  const currentSource: 'all' | 'translated' | 'original' = tokens.includes('translated')
    ? 'translated'
    : tokens.includes('original')
      ? 'original'
      : 'all';
  const isSummarizedActive = tokens.includes('summarized');
  const isReviewActive = tokens.includes('review');

  const [minInput, setMinInput] = useState(minPages != null ? String(minPages) : '');
  const [maxInput, setMaxInput] = useState(maxPages != null ? String(maxPages) : '');

  useEffect(() => {
    setMinInput(minPages != null ? String(minPages) : '');
  }, [minPages]);

  useEffect(() => {
    setMaxInput(maxPages != null ? String(maxPages) : '');
  }, [maxPages]);

  const commitPageRange = (newMinStr: string, newMaxStr: string) => {
    const minVal = parseInt(newMinStr, 10);
    const maxVal = parseInt(newMaxStr, 10);
    const parsedMin = Number.isInteger(minVal) && minVal > 0 ? minVal : undefined;
    const parsedMax = Number.isInteger(maxVal) && maxVal > 0 ? maxVal : undefined;
    handlePageRangeChange(parsedMin, parsedMax);
  };

  const hasActiveFilters = currentSource !== 'all' || isSummarizedActive || isReviewActive || minPages != null || maxPages != null || Boolean(mangaSearchInput.trim());

  return (
    <div className="flex w-full flex-wrap items-center justify-end gap-2 sm:w-auto">
      {/* 1. Source Type Selector */}
      <div className="flex items-center space-x-1 rounded-lg border border-zinc-200 dark:border-zinc-700 bg-zinc-50 dark:bg-zinc-800 px-2.5 py-1 text-xs text-zinc-700 dark:text-zinc-300">
        <Icon icon="carbon:filter" className="w-3.5 h-3.5 text-zinc-400" />
        <select
          value={currentSource}
          aria-label="Filter manga status"
          onChange={(e) => handleSourceChange(e.target.value as 'all' | 'translated' | 'original')}
          className="bg-transparent border-none outline-hidden text-xs cursor-pointer font-medium"
        >
          <option value="all" className="dark:bg-zinc-800">All Manga</option>
          <option value="translated" className="dark:bg-zinc-800">Translated</option>
          <option value="original" className="dark:bg-zinc-800">Originals (Raw)</option>
        </select>
      </div>

      {/* 2. Summarized Toggle Chip */}
      <button
        type="button"
        onClick={() => toggleStatusFlag('summarized')}
        className={`inline-flex items-center gap-1.5 rounded-lg border px-2.5 py-1 text-xs font-medium transition-colors cursor-pointer ${
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

      {/* 3. Needs Review Toggle Chip */}
      <button
        type="button"
        onClick={() => toggleStatusFlag('review')}
        className={`inline-flex items-center gap-1.5 rounded-lg border px-2.5 py-1 text-xs font-medium transition-colors cursor-pointer ${
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

      {/* 4. Page Range Filter */}
      <div
        className={`flex items-center space-x-1 rounded-lg border px-2 py-1 text-xs transition-colors ${
          minPages != null || maxPages != null
            ? 'border-indigo-400 bg-indigo-50/50 dark:border-indigo-700 dark:bg-indigo-950/40 text-zinc-800 dark:text-zinc-200'
            : 'border-zinc-200 dark:border-zinc-700 bg-zinc-50 dark:bg-zinc-800 text-zinc-700 dark:text-zinc-300'
        }`}
        title="Filter by page count range"
      >
        <Icon icon="carbon:document-multiple-01" className="w-3.5 h-3.5 text-zinc-400 shrink-0" />
        <span className="text-zinc-400 text-[11px] hidden sm:inline">Pages:</span>
        <input
          type="number"
          min={1}
          placeholder="Min"
          value={minInput}
          onChange={(e) => {
            setMinInput(e.target.value);
            commitPageRange(e.target.value, maxInput);
          }}
          aria-label="Minimum page count"
          className="w-10 bg-transparent text-center outline-none text-xs text-zinc-800 dark:text-zinc-200 placeholder-zinc-400 [appearance:textfield] [&::-webkit-outer-spin-button]:appearance-none [&::-webkit-inner-spin-button]:appearance-none"
        />
        <span className="text-zinc-400">–</span>
        <input
          type="number"
          min={1}
          placeholder="Max"
          value={maxInput}
          onChange={(e) => {
            setMaxInput(e.target.value);
            commitPageRange(minInput, e.target.value);
          }}
          aria-label="Maximum page count"
          className="w-10 bg-transparent text-center outline-none text-xs text-zinc-800 dark:text-zinc-200 placeholder-zinc-400 [appearance:textfield] [&::-webkit-outer-spin-button]:appearance-none [&::-webkit-inner-spin-button]:appearance-none"
        />
        {(minPages != null || maxPages != null || minInput || maxInput) && (
          <button
            type="button"
            onClick={() => {
              setMinInput('');
              setMaxInput('');
              handlePageRangeChange(undefined, undefined);
            }}
            className="text-zinc-400 hover:text-zinc-600 dark:hover:text-zinc-200 p-0.5 cursor-pointer ml-0.5"
            title="Clear page range"
          >
            <Icon icon="carbon:close" className="h-3 w-3" />
          </button>
        )}
      </div>

      {/* 5. Search Bar */}
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
          className="rounded-lg border border-zinc-200 dark:border-zinc-700 bg-zinc-50 dark:bg-zinc-800 pl-8 pr-7 py-1.5 text-xs text-zinc-800 dark:text-zinc-200 placeholder-zinc-400 focus:border-indigo-500 focus:outline-none w-32 sm:w-44 transition-all"
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

      {/* 6. Sort Selector */}
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

      {/* 7. Reset Filters Button */}
      {hasActiveFilters && (
        <button
          type="button"
          onClick={handleClearFilters}
          className="inline-flex items-center gap-1 rounded-lg border border-dashed border-zinc-300 dark:border-zinc-600 px-2 py-1 text-xs text-zinc-500 hover:text-indigo-600 dark:text-zinc-400 dark:hover:text-indigo-400 cursor-pointer transition-colors"
          title="Reset all filters and search"
        >
          <Icon icon="carbon:filter-reset" className="h-3.5 w-3.5" />
          <span>Reset</span>
        </button>
      )}
    </div>
  );
}
