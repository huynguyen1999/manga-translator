import { Icon } from '@iconify/react';
import type { FinishedImage } from '@/types';

interface MoveToMangaModalProps {
  isOpen: boolean;
  singleImageToMove: FinishedImage | null;
  selectedPageCount: number;
  mangaGroups: Array<{ title: string; images: FinishedImage[] }>;
  search: string;
  onSearchChange: (value: string) => void;
  targetMangaName: string;
  onTargetMangaNameChange: (value: string) => void;
  maxTitleLength: number;
  onClose: () => void;
  onMove: (title: string) => void;
}

export function MoveToMangaModal({
  isOpen,
  singleImageToMove,
  selectedPageCount,
  mangaGroups,
  search,
  onSearchChange,
  targetMangaName,
  onTargetMangaNameChange,
  maxTitleLength,
  onClose,
  onMove,
}: MoveToMangaModalProps) {
  if (!isOpen) return null;

  return (
    <div
      className="fixed inset-0 z-50 bg-black/60 backdrop-blur-xs flex items-center justify-center p-4"
    >
      <div
        className="w-full max-w-md rounded-2xl border border-zinc-200 dark:border-zinc-800 bg-white dark:bg-zinc-900 p-6 shadow-2xl space-y-4"
      >
        <div className="flex items-center justify-between">
          <div className="flex items-center space-x-2">
            <Icon icon="carbon:folder-move-to" className="w-5 h-5 text-indigo-600 dark:text-indigo-400" />
            <h3 className="text-base font-semibold text-zinc-900 dark:text-zinc-100">
              Move to Manga
            </h3>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="rounded-lg p-1 text-zinc-400 hover:text-zinc-600 dark:hover:text-zinc-200"
          >
            <Icon icon="carbon:close" className="w-5 h-5" />
          </button>
        </div>

        <p className="text-xs text-zinc-500">
          {singleImageToMove
            ? `Assign "${singleImageToMove.originalName}" to a manga group:`
            : `Assign ${selectedPageCount} selected pages to a manga group:`}
        </p>

        {mangaGroups.length > 0 && (
          <div className="space-y-1.5">
            <label className="text-xs font-medium text-zinc-700 dark:text-zinc-300">
              Select existing manga:
            </label>
            <div className="relative flex items-center">
              <Icon icon="carbon:search" className="pointer-events-none absolute left-2.5 h-3.5 w-3.5 text-zinc-400" />
              <input
                type="text"
                aria-label="Search existing manga"
                value={search}
                onChange={(event) => onSearchChange(event.target.value)}
                placeholder="Search existing manga..."
                className="w-full rounded-lg border border-zinc-200 bg-zinc-50 py-1.5 pl-8 pr-7 text-xs text-zinc-900 placeholder-zinc-400 outline-hidden transition-all focus:border-indigo-500 focus:bg-white dark:border-zinc-700/80 dark:bg-zinc-800/60 dark:text-zinc-100 dark:focus:bg-zinc-900"
              />
              {search && (
                <button
                  type="button"
                  onClick={() => onSearchChange('')}
                  className="absolute right-2 p-0.5 text-zinc-400 hover:text-zinc-600 dark:hover:text-zinc-200"
                  title="Clear search"
                >
                  <Icon icon="carbon:close" className="h-3.5 w-3.5" />
                </button>
              )}
            </div>
            <div className="max-h-36 overflow-y-auto space-y-1 rounded-lg border border-zinc-200 dark:border-zinc-800 p-1.5">
              {mangaGroups.length > 0 ? mangaGroups.map((group) => (
                <button
                  key={group.title}
                  type="button"
                  onClick={() => onMove(group.title)}
                  className="w-full flex items-center justify-between rounded-md px-2.5 py-1.5 text-xs text-left text-zinc-800 dark:text-zinc-200 hover:bg-indigo-50 dark:hover:bg-indigo-950/50 hover:text-indigo-600 dark:hover:text-indigo-400 transition-colors"
                >
                  <span className="truncate">{group.title}</span>
                  <span className="text-[10px] text-zinc-400">{group.images.length} pages</span>
                </button>
              )) : (
                <p className="px-2.5 py-2 text-center text-xs text-zinc-500 dark:text-zinc-400">
                  No matching manga found.
                </p>
              )}
            </div>
          </div>
        )}

        <div className="space-y-1.5">
          <label className="text-xs font-medium text-zinc-700 dark:text-zinc-300">
            Or create new manga collection:
          </label>
          <div className="flex items-center space-x-2">
            <input
              type="text"
              value={targetMangaName}
              onChange={(event) => onTargetMangaNameChange(event.target.value)}
              maxLength={maxTitleLength}
              placeholder="e.g. One Piece Chapter 100"
              onKeyDown={(event) => {
                if (event.key === 'Enter' && targetMangaName.trim()) {
                  onMove(targetMangaName);
                }
              }}
              className="flex-1 rounded-lg border border-zinc-200 dark:border-zinc-700 bg-zinc-50 dark:bg-zinc-800 px-3 py-1.5 text-xs text-zinc-900 dark:text-zinc-100 placeholder-zinc-400 outline-hidden focus:border-indigo-500"
            />
            <button
              type="button"
              onClick={() => onMove(targetMangaName)}
              disabled={!targetMangaName.trim()}
              className="rounded-lg bg-indigo-600 px-3 py-1.5 text-xs font-semibold text-white shadow-xs hover:bg-indigo-500 disabled:opacity-50 transition-colors"
            >
              Create &amp; Move
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
