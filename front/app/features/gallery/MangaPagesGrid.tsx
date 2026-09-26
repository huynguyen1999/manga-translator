import React from 'react';
import type { Dispatch, SetStateAction } from 'react';
import { Icon } from '@iconify/react';
import type { FinishedImage } from '@/types';
import type { PageSortOption } from '@/utils/resultGallery';
import { GalleryCard } from './GalleryCard';

type MangaPageGroup = {
  id: string;
  title: string;
  count: number;
  images: FinishedImage[];
  isLoading: boolean;
  isLoaded: boolean;
};

interface MangaPagesGridProps {
  currentSingleGroup: MangaPageGroup;
  canReorderCurrentGroup: boolean;
  pageSort: PageSortOption;
  reorderingGroupId: string | null;
  handlePageSort: (sortMode: PageSortOption) => void;
  handleSavePageSort: () => Promise<void>;
  pageSortIsDirty: boolean;
  pageOrderError: string | null;
  displayedPageImages: FinishedImage[];
  setDraggedPageId: Dispatch<SetStateAction<string | null>>;
  setDragOverPageId: Dispatch<SetStateAction<string | null>>;
  draggedPageId: string | null;
  dragOverPageId: string | null;
  stopPageDragAutoScroll: () => void;
  handlePageDrop: (group: { id: string; title: string; images: FinishedImage[] }, sourceId: string, targetId: string) => void | Promise<void>;
  duplicateSinglePageNames: Set<string>;
  highlightedImageId: string | null;
  selectedImageIds: Set<string>;
  handleSingleGroupToggleSelect: (id: string, shiftKey?: boolean) => void;
  handleCardMove: (image: FinishedImage) => void;
  handleSingleGroupReadFromHere: (pageIndex: number) => void;
  handleCardClick: (image: FinishedImage) => void;
  handleCardDownload: (image: FinishedImage) => void;
  onDeleteImage?: (image: FinishedImage) => void;
  handleCardDelete: (image: FinishedImage) => void;
  handleCardEdit: (image: FinishedImage) => void;
  onRerenderImage?: (image: FinishedImage) => void | Promise<void>;
  pageViewState: { from: string };
}

export const MangaPagesGrid: React.FC<MangaPagesGridProps> = ({
  currentSingleGroup,
  canReorderCurrentGroup,
  pageSort,
  reorderingGroupId,
  handlePageSort,
  handleSavePageSort,
  pageSortIsDirty,
  pageOrderError,
  displayedPageImages,
  setDraggedPageId,
  setDragOverPageId,
  draggedPageId,
  dragOverPageId,
  stopPageDragAutoScroll,
  handlePageDrop,
  duplicateSinglePageNames,
  highlightedImageId,
  selectedImageIds,
  handleSingleGroupToggleSelect,
  handleCardMove,
  handleSingleGroupReadFromHere,
  handleCardClick,
  handleCardDownload,
  onDeleteImage,
  handleCardDelete,
  handleCardEdit,
  onRerenderImage,
  pageViewState,
}) => (
<div className="rounded-2xl border border-zinc-200 dark:border-zinc-800 bg-white dark:bg-zinc-900/50 p-4 shadow-xs">
            {canReorderCurrentGroup && (
              <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
                <p className="text-xs text-zinc-500 dark:text-zinc-400">
                  Drag pages to set their reading order. Sorting previews the change until you save it.
                </p>
                <div className="flex flex-wrap items-center justify-end gap-2">
                  <label className="flex items-center gap-1.5 text-xs font-medium text-zinc-600 dark:text-zinc-300">
                    <Icon icon="carbon:sort-ascending" className="h-3.5 w-3.5 text-zinc-400" />
                    <span>Sort and reorder</span>
                    <select
                      value={pageSort}
                      aria-label="Sort and reorder pages"
                      disabled={
                        reorderingGroupId === currentSingleGroup.id ||
                        currentSingleGroup.isLoading ||
                        !currentSingleGroup.isLoaded
                      }
                      onChange={(event) => handlePageSort(event.target.value as PageSortOption)}
                      className="rounded-lg border border-zinc-200 bg-zinc-50 px-2 py-1 text-xs text-zinc-700 outline-hidden dark:border-zinc-700 dark:bg-zinc-800 dark:text-zinc-200"
                    >
                      <option value="order">Reading order</option>
                      <option value="name-asc">Name (A → Z)</option>
                      <option value="name-desc">Name (Z → A)</option>
                      <option value="created-asc">Created (oldest first)</option>
                      <option value="created-desc">Created (newest first)</option>
                    </select>
                  </label>
                  <button
                    type="button"
                    onClick={() => void handleSavePageSort()}
                    disabled={
                      !pageSortIsDirty ||
                      reorderingGroupId === currentSingleGroup.id ||
                      currentSingleGroup.isLoading ||
                      !currentSingleGroup.isLoaded
                    }
                    className="inline-flex items-center gap-1.5 rounded-lg bg-indigo-600 px-3 py-1.5 text-xs font-semibold text-white shadow-xs transition-colors hover:bg-indigo-500 disabled:cursor-not-allowed disabled:opacity-50"
                  >
                    <Icon icon="carbon:save" className="h-3.5 w-3.5" />
                    <span>{reorderingGroupId === currentSingleGroup.id ? 'Saving…' : 'Save order'}</span>
                  </button>
                </div>
              </div>
            )}
            {pageOrderError && (
              <p className="mb-3 text-xs text-red-600 dark:text-red-400" role="alert">
                {pageOrderError}
              </p>
            )}
            {currentSingleGroup.isLoading || (!currentSingleGroup.isLoaded && currentSingleGroup.count > 0) ? (
              <div className="flex items-center justify-center py-16 text-zinc-400 space-x-2">
                <Icon icon="carbon:renew" className="w-5 h-5 animate-spin text-indigo-500" />
                <span className="text-sm font-medium">Loading pages for {currentSingleGroup.title}...</span>
              </div>
            ) : currentSingleGroup.images.length === 0 ? (
              <div className="text-center py-12 text-zinc-400 text-xs">
                No pages available for this manga.
              </div>
            ) : (
              <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-5 gap-4">
                {displayedPageImages.map((image, idx) => (
                  <div
                    key={image.id}
                    draggable={canReorderCurrentGroup && pageSort === 'order' && reorderingGroupId !== currentSingleGroup.id}
                    onDragStart={(event) => {
                      if (!canReorderCurrentGroup || pageSort !== 'order') return;
                      setDraggedPageId(image.id);
                      event.dataTransfer.effectAllowed = 'move';
                      event.dataTransfer.setData('text/plain', image.id);
                    }}
                    onDragOver={(event) => {
                      if (!canReorderCurrentGroup) return;
                      event.preventDefault();
                      event.dataTransfer.dropEffect = 'move';
                      setDragOverPageId(image.id);
                    }}
                    onDrop={(event) => {
                      event.preventDefault();
                      stopPageDragAutoScroll();
                      const sourceId = event.dataTransfer.getData('text/plain') || draggedPageId || '';
                      void handlePageDrop(currentSingleGroup, sourceId, image.id);
                      setDraggedPageId(null);
                      setDragOverPageId(null);
                    }}
                    onDragEnd={() => {
                      stopPageDragAutoScroll();
                      setDraggedPageId(null);
                      setDragOverPageId(null);
                    }}
                    className={`min-w-0 rounded-xl transition-shadow ${
                      dragOverPageId === image.id ? 'ring-2 ring-indigo-500 ring-offset-2 dark:ring-offset-zinc-900' : ''
                    } ${draggedPageId === image.id ? 'opacity-40' : ''}`}
                    aria-label={`Page ${idx + 1}: ${image.originalName}`}
                  >
                    <GalleryCard
                      image={image}
                      pageIndex={idx + 1}
                      showSourcePath={duplicateSinglePageNames.has(image.originalName.toLocaleLowerCase())}
                      isHighlighted={highlightedImageId === image.id}
                      isSelected={selectedImageIds.has(image.id)}
                      onToggleSelect={handleSingleGroupToggleSelect}
                      onMoveToManga={handleCardMove}
                      onReadFromHere={handleSingleGroupReadFromHere}
                      onClick={handleCardClick}
                      onDownload={handleCardDownload}
                      onDelete={onDeleteImage ? handleCardDelete : undefined}
                      onEdit={image.hasTextRegions && image.sourceType !== 'original' ? handleCardEdit : undefined}
                      onRerender={image.hasTextRegions && image.sourceType !== 'original' ? onRerenderImage : undefined}
                      pageViewState={pageViewState}
                    />
                  </div>
                ))}
              </div>
            )}
          </div>
);
