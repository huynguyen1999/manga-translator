import React from "react";
import { Icon } from "@iconify/react";

interface GalleryBulkSelectionBarProps {
  selectedCount: number;
  onMove: () => void;
  onDelete?: () => void;
  onRerender?: () => void;
  onClear: () => void;
}

export const GalleryBulkSelectionBar: React.FC<GalleryBulkSelectionBarProps> = ({
  selectedCount, onMove, onDelete, onRerender, onClear,
}) => (
  <div role="region" aria-label="Page selection toolbar" className="fixed bottom-3 left-1/2 z-40 w-[calc(100%-1.25rem)] -translate-x-1/2 sm:bottom-6 sm:w-[calc(100%-2rem)] sm:max-w-5xl">
    <div className="flex flex-wrap items-center justify-between gap-2 rounded-xl border border-indigo-200 bg-indigo-50 p-3 shadow-2xl backdrop-blur-md dark:border-indigo-800 dark:bg-indigo-950/90">
      <div className="flex items-center space-x-2 text-xs font-semibold text-indigo-900 dark:text-indigo-200">
        <Icon icon="carbon:checkbox-checked-filled" className="w-4 h-4 text-indigo-600 dark:text-indigo-400" />
        <span>{selectedCount} {selectedCount === 1 ? "page" : "pages"} selected</span>
      </div>
      <div className="flex flex-wrap items-center justify-end gap-1.5 sm:gap-2">
        <button type="button" onClick={onMove} className="flex items-center space-x-1 whitespace-nowrap rounded-lg bg-indigo-600 px-2.5 py-2 text-xs font-semibold text-white shadow-xs hover:bg-indigo-500 transition-colors cursor-pointer sm:px-3">
          <Icon icon="carbon:folder-move-to" className="w-4 h-4" />
          <span>Move to Manga...</span>
        </button>
        {onDelete && (
          <button type="button" onClick={onDelete} className="flex items-center space-x-1 whitespace-nowrap rounded-lg bg-red-600 px-2.5 py-2 text-xs font-semibold text-white shadow-xs hover:bg-red-500 transition-colors cursor-pointer sm:px-3" title={`Delete ${selectedCount} selected ${selectedCount === 1 ? "page" : "pages"}`}>
            <Icon icon="carbon:trash-can" className="w-4 h-4" />
            <span>Delete...</span>
          </button>
        )}
        {onRerender && (
          <button type="button" onClick={onRerender} className="flex items-center space-x-1 whitespace-nowrap rounded-lg bg-indigo-600 px-2.5 py-2 text-xs font-semibold text-white shadow-xs hover:bg-indigo-500 transition-colors cursor-pointer sm:px-3">
            <Icon icon="carbon:reset" className="w-4 h-4" />
            <span>Rerun pipeline…</span>
          </button>
        )}
        <button type="button" onClick={onClear} className="whitespace-nowrap rounded-lg px-2.5 py-2 text-xs text-indigo-800 dark:text-indigo-200 hover:bg-indigo-100 dark:hover:bg-indigo-900/50 transition-colors cursor-pointer">
          Deselect All
        </button>
      </div>
    </div>
  </div>
);
