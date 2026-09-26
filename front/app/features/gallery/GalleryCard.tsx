import React, { useCallback, useMemo } from 'react';
import { Icon } from '@iconify/react';
import { Menu, MenuButton, MenuItem, MenuItems } from '@headlessui/react';
import { Link } from 'react-router';
import type { FinishedImage } from '@/types';
import type { MangaReadProgress } from '@/utils/resultGallery';
import { buildPageViewUrl } from '@/utils/routeState';
import { useGalleryThumbnail } from './useGalleryThumbnail';

export interface GalleryCardProps {
  image: FinishedImage;
  pageIndex?: number;
  showSourcePath?: boolean;
  isSelected?: boolean;
  isHighlighted?: boolean;
  onToggleSelect?: (id: string, shiftKey?: boolean) => void;
  onMoveToManga?: (image: FinishedImage) => void;
  onReadFromHere?: (pageIndex: number) => void;
  onClick: (image: FinishedImage) => void;
  onDownload: (image: FinishedImage) => void;
  onDelete?: (image: FinishedImage) => void;
  onEdit?: (image: FinishedImage) => void;
  onRerender?: (image: FinishedImage) => void | Promise<void>;
  pageViewState: { from: string };
}

const areGalleryCardPropsEqual = (prev: GalleryCardProps, next: GalleryCardProps) => {
  return (
    prev.image.id === next.image.id &&
    prev.image.result === next.image.result &&
    prev.image.inputUrl === next.image.inputUrl &&
    prev.image.originalName === next.image.originalName &&
    prev.image.hasTextRegions === next.image.hasTextRegions &&
    prev.image.sourceType === next.image.sourceType &&
    prev.pageIndex === next.pageIndex &&
    prev.showSourcePath === next.showSourcePath &&
    prev.isSelected === next.isSelected &&
    prev.isHighlighted === next.isHighlighted &&
    prev.onToggleSelect === next.onToggleSelect &&
    prev.onMoveToManga === next.onMoveToManga &&
    prev.onReadFromHere === next.onReadFromHere &&
    prev.onClick === next.onClick &&
    prev.onDownload === next.onDownload &&
    prev.onDelete === next.onDelete &&
    prev.onEdit === next.onEdit &&
    prev.onRerender === next.onRerender &&
    prev.pageViewState.from === next.pageViewState.from
  );
};

const GalleryCardComponent: React.FC<GalleryCardProps> = ({
  image,
  pageIndex,
  showSourcePath = false,
  isSelected = false,
  isHighlighted = false,
  onToggleSelect,
  onMoveToManga,
  onReadFromHere,
  onClick,
  onDownload,
  onDelete,
  onEdit,
  onRerender,
  pageViewState,
}) => {
  const { src, hasError, isLoaded, handleLoad, handleError } = useGalleryThumbnail(image);

  const finishedDateStr = useMemo(() => {
    const finishedDate =
      image.finishedAt instanceof Date
        ? image.finishedAt
        : new Date(image.finishedAt || Date.now());
    return finishedDate.toLocaleDateString();
  }, [image.finishedAt]);

  const handleCardClick = useCallback(() => {
    onClick(image);
  }, [onClick, image]);

  const handleToggleSelectClick = useCallback((e: React.MouseEvent) => {
    e.stopPropagation();
    onToggleSelect?.(image.id, e.shiftKey);
  }, [onToggleSelect, image.id]);

  const handleEditClick = useCallback((e: React.MouseEvent) => {
    e.stopPropagation();
    onEdit?.(image);
  }, [onEdit, image]);

  const handleMoveClick = useCallback((e: React.MouseEvent) => {
    e.stopPropagation();
    onMoveToManga?.(image);
  }, [onMoveToManga, image]);

  const handleDownloadClick = useCallback((e: React.MouseEvent) => {
    e.stopPropagation();
    onDownload(image);
  }, [onDownload, image]);

  const handleDeleteClick = useCallback((e: React.MouseEvent) => {
    e.stopPropagation();
    onDelete?.(image);
  }, [onDelete, image]);

  const handleRerenderClick = useCallback((e: React.MouseEvent) => {
    e.stopPropagation();
    void Promise.resolve(onRerender?.(image)).catch((error) => {
      window.alert(error instanceof Error ? error.message : 'Could not queue rerender.');
    });
  }, [onRerender, image]);

  const handleReadClick = useCallback((e: React.MouseEvent) => {
    e.stopPropagation();
    if (pageIndex !== undefined) {
      onReadFromHere?.(pageIndex - 1);
    }
  }, [onReadFromHere, pageIndex]);

  return (
    <div
      data-image-id={image.id}
      data-page-index={pageIndex !== undefined ? pageIndex - 1 : undefined}
      style={{ contentVisibility: 'auto', containIntrinsicSize: '0 280px' }}
      className={`group relative flex flex-col rounded-xl border bg-white dark:bg-zinc-900 overflow-hidden shadow-2xs hover:shadow-md transition-all cursor-pointer ${
        isHighlighted
          ? 'border-indigo-500 ring-2 ring-indigo-500 shadow-lg ring-offset-2 dark:ring-offset-zinc-900'
          : isSelected
          ? 'border-indigo-500 ring-2 ring-indigo-500/20 dark:ring-indigo-500/30'
          : 'border-zinc-200 dark:border-zinc-800 hover:border-indigo-400 dark:hover:border-indigo-600'
      }`}
      onClick={image.folder ? undefined : handleCardClick}
    >
      {image.folder && (
        <Link
          to={buildPageViewUrl(image.folder)}
          state={pageViewState}
          draggable={false}
          aria-label={`Open page detail for ${image.originalName}`}
          title={`Open page detail for ${image.originalName}`}
          className="absolute inset-0 z-[1] rounded-xl focus-visible:outline-2 focus-visible:outline-indigo-400"
        />
      )}
      {/* Thumbnail Area */}
      <div className="relative aspect-[3/4] w-full overflow-hidden bg-zinc-100 dark:bg-zinc-950 flex items-center justify-center">
        {src && !hasError ? (
          <img
            src={src}
            alt={`${image.sourceType === 'original' ? 'Original' : 'Translated'}: ${image.originalName}`}
            className={`w-full h-full object-cover group-hover:scale-105 transition-transform duration-200 select-none ${
              isLoaded ? 'opacity-100' : 'opacity-90'
            }`}
            loading={isLoaded ? 'eager' : 'lazy'}
            decoding="async"
            onLoad={handleLoad}
            onError={handleError}
          />
        ) : (
          <Icon icon="carbon:image" className="w-8 h-8 text-zinc-400 animate-pulse" />
        )}

        {/* Top Badges, Select Box, and Menu */}
        <div className="absolute top-2 left-2 right-2 flex items-center justify-between pointer-events-none z-10">
          <div className="flex items-center space-x-1.5 pointer-events-auto">
            {onToggleSelect && (
              <button
                type="button"
                onClick={handleToggleSelectClick}
                className={`w-6 h-6 rounded flex items-center justify-center transition-all select-none ${
                  isSelected
                    ? 'bg-indigo-600 text-white shadow-xs'
                    : 'bg-black/50 text-transparent hover:text-white/60 opacity-0 group-hover:opacity-100 focus-visible:opacity-100'
                }`}
                title={
                  isSelected
                    ? 'Deselect page (Shift+click for range)'
                    : 'Select page (Shift+click for range)'
                }
              >
                <Icon icon="carbon:checkmark" className="w-4 h-4" />
              </button>
            )}

            {pageIndex !== undefined && (
              <span className="rounded-md bg-black/60 backdrop-blur-xs px-1.5 py-0.5 text-[10px] font-mono text-white">
                #{pageIndex}
              </span>
            )}
          </div>

          <div className="flex items-center space-x-1.5 pointer-events-auto">
            {/* Source / Engine Badge */}
            {image.sourceType === 'original' ? (
              <div className="rounded-md bg-amber-500/80 backdrop-blur-xs px-1.5 py-0.5 text-[10px] font-mono text-white pointer-events-none">
                Original
              </div>
            ) : image.settings?.translator ? (
              <div className="rounded-md bg-black/60 backdrop-blur-xs px-1.5 py-0.5 text-[10px] font-mono text-zinc-200 pointer-events-none">
                {image.settings.translator}
              </div>
            ) : null}

            {/* Accessible Actions Menu */}
            <Menu as="div" className="relative pointer-events-auto">
              <MenuButton
                type="button"
                onClick={(e) => e.stopPropagation()}
                className="min-w-[44px] min-h-[44px] rounded-lg bg-black/60 hover:bg-black/80 text-zinc-200 hover:text-white backdrop-blur-xs flex items-center justify-center transition-colors focus-visible:outline-2 focus-visible:outline-indigo-400"
                aria-label={`Actions for ${image.originalName}`}
              >
                <Icon icon="carbon:overflow-menu-vertical" className="w-5 h-5" />
              </MenuButton>
              <MenuItems
                anchor="bottom end"
                className="w-48 origin-top-right rounded-xl bg-zinc-900 border border-zinc-700/80 shadow-2xl p-1 text-xs text-zinc-200 z-50 focus:outline-none"
              >
                {onEdit && (
                  <MenuItem>
                    <button
                      type="button"
                      onClick={handleEditClick}
                      className="flex w-full items-center gap-2.5 rounded-lg px-3 py-2.5 min-h-[44px] transition-colors data-focus:bg-indigo-600 data-focus:text-white text-zinc-200 hover:bg-zinc-800"
                    >
                      <Icon icon="carbon:text-annotation-toggle" className="w-4 h-4 text-indigo-400" />
                      <span>Edit Text</span>
                    </button>
                  </MenuItem>
                )}
                {onMoveToManga && (
                  <MenuItem>
                    <button
                      type="button"
                      onClick={handleMoveClick}
                      className="flex w-full items-center gap-2.5 rounded-lg px-3 py-2.5 min-h-[44px] transition-colors data-focus:bg-indigo-600 data-focus:text-white text-zinc-200 hover:bg-zinc-800"
                    >
                      <Icon icon="carbon:folder-move-to" className="w-4 h-4 text-zinc-400" />
                      <span>Move to Manga</span>
                    </button>
                  </MenuItem>
                )}
                {onRerender && (
                  <MenuItem>
                    <button
                      type="button"
                      onClick={handleRerenderClick}
                      className="flex w-full items-center gap-2.5 rounded-lg px-3 py-2.5 min-h-[44px] transition-colors data-focus:bg-indigo-600 data-focus:text-white text-zinc-200 hover:bg-zinc-800"
                    >
                      <Icon icon="carbon:reset" className="w-4 h-4 text-indigo-400" />
                      <span>Rerun pipeline…</span>
                    </button>
                  </MenuItem>
                )}
                <MenuItem>
                  <button
                    type="button"
                    onClick={handleDownloadClick}
                    className="flex w-full items-center gap-2.5 rounded-lg px-3 py-2.5 min-h-[44px] transition-colors data-focus:bg-indigo-600 data-focus:text-white text-zinc-200 hover:bg-zinc-800"
                  >
                    <Icon icon="carbon:download" className="w-4 h-4 text-zinc-400" />
                    <span>Download</span>
                  </button>
                </MenuItem>
                {onDelete && (
                  <MenuItem>
                    <button
                      type="button"
                      onClick={handleDeleteClick}
                      className="flex w-full items-center gap-2.5 rounded-lg px-3 py-2.5 min-h-[44px] transition-colors data-focus:bg-red-600 data-focus:text-white text-red-400 hover:bg-red-500/20"
                    >
                      <Icon icon="carbon:trash-can" className="w-4 h-4" />
                      <span>Delete</span>
                    </button>
                  </MenuItem>
                )}
              </MenuItems>
            </Menu>
          </div>
        </div>

        {/* Read From Here overlay button */}
        {onReadFromHere && (
          <div className="absolute inset-x-0 bottom-3 flex items-center justify-center pointer-events-none z-10">
            <button
              type="button"
              onClick={handleReadClick}
              className="pointer-events-auto min-h-[44px] px-3.5 py-2 flex items-center gap-1.5 rounded-xl bg-emerald-600/95 hover:bg-emerald-600 text-white text-xs font-semibold backdrop-blur-xs shadow-lg hover:scale-105 active:scale-95 transition-all opacity-0 group-hover:opacity-100 focus-visible:opacity-100 focus-visible:outline-2 focus-visible:outline-emerald-400"
              title="Read from this page"
              aria-label={`Read from page ${pageIndex !== undefined ? pageIndex : image.originalName}`}
            >
              <Icon icon="carbon:book-open" className="w-4 h-4" />
              <span>Read from here</span>
            </button>
          </div>
        )}
      </div>

      {/* Card Info */}
      <div className="p-3 space-y-1">
        <div className="text-xs font-semibold text-zinc-800 dark:text-zinc-200 truncate" title={image.originalName}>
          {image.originalName}
        </div>
        {image.reviewStatus === 'pending' && onEdit && (
          <button
            type="button"
            onClick={handleEditClick}
            className="relative z-10 mt-1 flex w-full items-center justify-center gap-1.5 rounded-lg border border-amber-700/60 bg-amber-950/30 px-2.5 py-1.5 text-xs font-semibold text-amber-300 transition-colors hover:bg-amber-900/50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-amber-400"
          >
            <Icon icon="carbon:edit" className="h-3.5 w-3.5" />
            Review page
          </button>
        )}
        {showSourcePath && image.sourcePath && image.sourcePath !== image.originalName && (
          <div className="truncate text-[11px] text-zinc-500 dark:text-zinc-400" title={image.sourcePath}>
            {image.sourcePath}
          </div>
        )}
        <div className="flex items-center justify-between text-[11px] text-zinc-400 dark:text-zinc-500">
          <span>{finishedDateStr}</span>
          {image.settings?.targetLanguage && (
            <span className="rounded bg-zinc-100 dark:bg-zinc-800 px-1.5 py-0.2 font-medium text-zinc-600 dark:text-zinc-400">
              → {image.settings.targetLanguage}
            </span>
          )}
        </div>
        {image.settings?.offlineModel && (
          <div className="truncate text-[11px] text-zinc-500 dark:text-zinc-400" title={image.settings.offlineModel}>
            Offline model: {image.settings.offlineModel}
          </div>
        )}
        {image.settings?.geminiModel && (
          <div className="truncate text-[11px] text-indigo-500 dark:text-indigo-400" title={image.settings.geminiModel}>
            Gemini model: {image.settings.geminiModel}
          </div>
        )}
      </div>
    </div>
  );
};

export const GalleryCard = React.memo(GalleryCardComponent, areGalleryCardPropsEqual);
GalleryCard.displayName = 'GalleryCard';
