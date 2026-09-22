import React, { useState } from 'react';
import { Icon } from '@iconify/react';

export type PaginationItem = number | 'ellipsis-left' | 'ellipsis-right';

export interface PaginationProps {
  /** Current active page (1-based index) */
  currentPage: number;
  /** Total number of pages */
  totalPages: number;
  /** Callback fired when a new page is selected */
  onPageChange: (page: number) => void;

  /** Optional total count of items being paginated (e.g. 160 manga) */
  totalItems?: number;
  /** Optional items per page (used to calculate item range: e.g. "Showing 1–24 of 160") */
  pageSize?: number;
  /** Optional label for the items (default: "items", e.g. "manga", "series") */
  itemLabel?: string;

  /** Optional page size options for inline changer */
  pageSizeOptions?: readonly number[] | number[];
  /** Callback when page size changes */
  onPageSizeChange?: (newPageSize: number) => void;

  /** Number of adjacent sibling page buttons to show around the current page (default: 1) */
  siblingCount?: number;
  /** Whether to show First and Last page jump buttons (default: true) */
  showFirstLast?: boolean;
  /** Whether to show a Quick Jump input for navigating to arbitrary pages (default: true if totalPages >= 7) */
  showJumpInput?: boolean;

  /** Accessibility label for the <nav> element (default: "Pagination") */
  ariaLabel?: string;
  /** Additional container CSS classes */
  className?: string;
}

/**
 * Generates an array of page numbers and ellipsis tokens for pagination.
 */
export function generatePaginationItems(
  currentPage: number,
  totalPages: number,
  siblingCount: number = 1
): PaginationItem[] {
  if (totalPages <= 0) return [];
  if (totalPages === 1) return [1];

  const totalPageNumbers = siblingCount * 2 + 5; // e.g. siblingCount=1 -> 7 visible slots

  // Case 1: Total pages is less than or equal to slots needed
  if (totalPages <= totalPageNumbers) {
    return Array.from({ length: totalPages }, (_, index) => index + 1);
  }

  const leftSiblingIndex = Math.max(currentPage - siblingCount, 1);
  const rightSiblingIndex = Math.min(currentPage + siblingCount, totalPages);

  const shouldShowLeftDots = leftSiblingIndex > 2;
  const shouldShowRightDots = rightSiblingIndex < totalPages - 1;

  const firstPageIndex = 1;
  const lastPageIndex = totalPages;

  // Case 2: No left dots, but right dots to be shown
  if (!shouldShowLeftDots && shouldShowRightDots) {
    const leftItemCount = 3 + 2 * siblingCount;
    const leftRange = Array.from({ length: leftItemCount }, (_, index) => index + 1);
    return [...leftRange, 'ellipsis-right', totalPages];
  }

  // Case 3: No right dots, but left dots to be shown
  if (shouldShowLeftDots && !shouldShowRightDots) {
    const rightItemCount = 3 + 2 * siblingCount;
    const rightRange = Array.from(
      { length: rightItemCount },
      (_, index) => totalPages - rightItemCount + index + 1
    );
    return [firstPageIndex, 'ellipsis-left', ...rightRange];
  }

  // Case 4: Both left and right dots to be shown
  if (shouldShowLeftDots && shouldShowRightDots) {
    const middleRange = Array.from(
      { length: rightSiblingIndex - leftSiblingIndex + 1 },
      (_, index) => leftSiblingIndex + index
    );
    return [firstPageIndex, 'ellipsis-left', ...middleRange, 'ellipsis-right', lastPageIndex];
  }

  return Array.from({ length: totalPages }, (_, index) => index + 1);
}

/**
 * Calculates start and end item index for range summary text.
 */
export function getItemRange(
  currentPage: number,
  pageSize: number,
  totalItems: number
): { start: number; end: number; total: number } {
  if (totalItems <= 0) {
    return { start: 0, end: 0, total: 0 };
  }
  const safePage = Math.max(1, currentPage);
  const start = Math.min((safePage - 1) * pageSize + 1, totalItems);
  const end = Math.min(safePage * pageSize, totalItems);
  return { start, end, total: totalItems };
}

export const Pagination: React.FC<PaginationProps> = ({
  currentPage,
  totalPages,
  onPageChange,
  totalItems,
  pageSize,
  itemLabel = 'items',
  pageSizeOptions,
  onPageSizeChange,
  siblingCount = 1,
  showFirstLast = true,
  showJumpInput,
  ariaLabel = 'Pagination',
  className = '',
}) => {
  const [jumpPageInput, setJumpPageInput] = useState('');
  const safeCurrentPage = Math.min(Math.max(1, currentPage), Math.max(1, totalPages));

  // Determine whether to show quick jump input (default true if totalPages >= 7)
  const shouldShowJump = showJumpInput !== undefined ? showJumpInput : totalPages >= 7;

  const paginationItems = generatePaginationItems(safeCurrentPage, totalPages, siblingCount);

  const handleJumpSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    const parsed = parseInt(jumpPageInput.trim(), 10);
    if (!isNaN(parsed)) {
      const target = Math.min(Math.max(1, parsed), totalPages);
      onPageChange(target);
      setJumpPageInput('');
    }
  };

  const hasRangeInfo = totalItems !== undefined && pageSize !== undefined && totalItems > 0;
  const itemRange = hasRangeInfo ? getItemRange(safeCurrentPage, pageSize!, totalItems!) : null;

  if (totalPages <= 1 && !hasRangeInfo && !pageSizeOptions) {
    return null;
  }

  return (
    <nav
      className={`flex flex-wrap items-center justify-between gap-3 rounded-xl border border-zinc-200 dark:border-zinc-800 bg-white dark:bg-zinc-900 p-3 shadow-xs ${className}`}
      aria-label={ariaLabel}
    >
      {/* Left side: Context Summary (Page / Items info) */}
      <div className="flex items-center gap-2 text-xs font-medium text-zinc-500 dark:text-zinc-400">
        {hasRangeInfo && itemRange ? (
          <span>
            Showing <strong className="font-semibold text-zinc-900 dark:text-zinc-100">{itemRange.start}–{itemRange.end}</strong> of{' '}
            <strong className="font-semibold text-zinc-900 dark:text-zinc-100">{itemRange.total}</strong> {itemLabel}
            <span className="ml-1.5 hidden sm:inline text-zinc-400 dark:text-zinc-500">
              (Page {safeCurrentPage} of {totalPages})
            </span>
          </span>
        ) : (
          <span>
            Page <strong className="font-semibold text-zinc-900 dark:text-zinc-100">{safeCurrentPage}</strong> of{' '}
            <strong className="font-semibold text-zinc-900 dark:text-zinc-100">{totalPages}</strong>
          </span>
        )}
      </div>

      {/* Center / Controls: Navigation buttons */}
      <div className="flex flex-wrap items-center gap-1 sm:gap-1.5">
        {/* First Page Button */}
        {showFirstLast && totalPages > 2 && (
          <button
            type="button"
            onClick={() => onPageChange(1)}
            disabled={safeCurrentPage <= 1}
            aria-label="Go to first page"
            title="First Page"
            className="flex h-9 w-9 items-center justify-center rounded-lg border border-zinc-200 dark:border-zinc-700 text-zinc-700 dark:text-zinc-200 transition-colors hover:bg-zinc-100 dark:hover:bg-zinc-800 disabled:cursor-not-allowed disabled:opacity-40 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500"
          >
            <Icon icon="lucide:chevrons-left" className="h-4 w-4" />
          </button>
        )}

        {/* Previous Page Button */}
        <button
          type="button"
          onClick={() => onPageChange(safeCurrentPage - 1)}
          disabled={safeCurrentPage <= 1}
          aria-label="Go to previous page"
          title="Previous Page"
          className="flex h-9 items-center gap-1 rounded-lg border border-zinc-200 dark:border-zinc-700 px-2.5 sm:px-3 text-xs font-semibold text-zinc-700 dark:text-zinc-200 transition-colors hover:bg-zinc-100 dark:hover:bg-zinc-800 disabled:cursor-not-allowed disabled:opacity-40 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500"
        >
          <Icon icon="lucide:chevron-left" className="h-4 w-4 shrink-0" />
          <span className="hidden sm:inline">Previous</span>
        </button>

        {/* Numbered Page Buttons */}
        <div className="flex items-center gap-1">
          {paginationItems.map((item, index) => {
            if (item === 'ellipsis-left' || item === 'ellipsis-right') {
              return (
                <span
                  key={`dots-${index}`}
                  className="flex h-9 w-6 sm:w-8 items-center justify-center text-xs font-semibold text-zinc-400 dark:text-zinc-500 select-none"
                  aria-hidden="true"
                >
                  …
                </span>
              );
            }

            const isActive = item === safeCurrentPage;
            return (
              <button
                key={item}
                type="button"
                onClick={() => onPageChange(item)}
                aria-label={`Page ${item}`}
                aria-current={isActive ? 'page' : undefined}
                className={`flex h-9 min-w-9 items-center justify-center rounded-lg px-2.5 text-xs font-semibold transition-all focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500 ${
                  isActive
                    ? 'bg-indigo-600 dark:bg-indigo-500 text-white shadow-xs font-bold'
                    : 'border border-zinc-200 dark:border-zinc-700 text-zinc-700 dark:text-zinc-200 hover:bg-zinc-100 dark:hover:bg-zinc-800'
                }`}
              >
                {item}
              </button>
            );
          })}
        </div>

        {/* Next Page Button */}
        <button
          type="button"
          onClick={() => onPageChange(safeCurrentPage + 1)}
          disabled={safeCurrentPage >= totalPages}
          aria-label="Go to next page"
          title="Next Page"
          className="flex h-9 items-center gap-1 rounded-lg border border-zinc-200 dark:border-zinc-700 px-2.5 sm:px-3 text-xs font-semibold text-zinc-700 dark:text-zinc-200 transition-colors hover:bg-zinc-100 dark:hover:bg-zinc-800 disabled:cursor-not-allowed disabled:opacity-40 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500"
        >
          <span className="hidden sm:inline">Next</span>
          <Icon icon="lucide:chevron-right" className="h-4 w-4 shrink-0" />
        </button>

        {/* Last Page Button */}
        {showFirstLast && totalPages > 2 && (
          <button
            type="button"
            onClick={() => onPageChange(totalPages)}
            disabled={safeCurrentPage >= totalPages}
            aria-label="Go to last page"
            title="Last Page"
            className="flex h-9 w-9 items-center justify-center rounded-lg border border-zinc-200 dark:border-zinc-700 text-zinc-700 dark:text-zinc-200 transition-colors hover:bg-zinc-100 dark:hover:bg-zinc-800 disabled:cursor-not-allowed disabled:opacity-40 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500"
          >
            <Icon icon="lucide:chevrons-right" className="h-4 w-4" />
          </button>
        )}
      </div>

      {/* Right side: Optional Page Size and Jump Input */}
      {((pageSizeOptions && onPageSizeChange) || shouldShowJump) && (
        <div className="flex flex-wrap items-center gap-2 text-xs">
          {/* Optional Page Size Selector */}
          {pageSizeOptions && onPageSizeChange && pageSize && (
            <div className="flex items-center gap-1.5 rounded-lg border border-zinc-200 dark:border-zinc-700 bg-zinc-50 dark:bg-zinc-800 px-2.5 py-1 text-zinc-700 dark:text-zinc-300">
              <label htmlFor="pagination-page-size" className="text-zinc-500 dark:text-zinc-400">
                Per page
              </label>
              <select
                id="pagination-page-size"
                value={pageSize}
                onChange={(e) => onPageSizeChange(Number(e.target.value))}
                className="bg-transparent border-none outline-hidden cursor-pointer font-medium focus-visible:ring-2 focus-visible:ring-indigo-500"
                aria-label="Items per page"
              >
                {pageSizeOptions.map((opt) => (
                  <option key={opt} value={opt} className="dark:bg-zinc-800">
                    {opt}
                  </option>
                ))}
              </select>
            </div>
          )}

          {/* Jump to Page Input Form */}
          {shouldShowJump && (
            <form onSubmit={handleJumpSubmit} className="flex items-center gap-1">
              <span className="text-zinc-500 dark:text-zinc-400">Go to</span>
              <input
                type="number"
                min={1}
                max={totalPages}
                value={jumpPageInput}
                onChange={(e) => setJumpPageInput(e.target.value)}
                placeholder={String(safeCurrentPage)}
                aria-label="Go to page number"
                className="h-7 w-12 rounded-md border border-zinc-200 dark:border-zinc-700 bg-zinc-50 dark:bg-zinc-800 px-1.5 text-center text-xs text-zinc-900 dark:text-zinc-100 focus:border-indigo-500 focus:outline-hidden focus:ring-1 focus:ring-indigo-500"
              />
              <button
                type="submit"
                className="h-7 rounded-md border border-zinc-200 dark:border-zinc-700 bg-zinc-50 dark:bg-zinc-800 px-2 text-xs font-medium text-zinc-700 dark:text-zinc-200 hover:bg-zinc-100 dark:hover:bg-zinc-700 transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500"
              >
                Go
              </button>
            </form>
          )}
        </div>
      )}
    </nav>
  );
};
