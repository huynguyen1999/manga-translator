import React, { useCallback } from 'react';
import { Icon } from '@iconify/react';
import { Link } from 'react-router';
import type { FinishedImage } from '@/types';
import type { MangaReadProgress } from '@/utils/resultGallery';
import { buildMangaDetailIdUrl, buildReaderIdUrl } from '@/utils/routeState';
import { useGalleryThumbnail } from './useGalleryThumbnail';

export const MangaReadBadge: React.FC<{ progress: MangaReadProgress; pageCount: number }> = ({ progress, pageCount }) => {
  const label = progress.complete
    ? `Finished · page ${pageCount} / ${pageCount}`
    : progress.page
    ? `Last read · page ${progress.page} / ${pageCount}`
    : 'Not started';

  return (
    <span
      className={`inline-flex items-center gap-1 rounded-full px-2.5 py-0.5 text-[10px] font-semibold text-white shadow-xs ${
        progress.complete
          ? 'bg-emerald-600/90'
          : progress.page
          ? 'bg-amber-500/95'
          : 'bg-black/65'
      }`}
      title={label}
    >
      <Icon
        icon={progress.complete ? 'carbon:checkmark-filled' : progress.page ? 'carbon:bookmark' : 'carbon:book'}
        className="h-3 w-3"
      />
      {label}
    </span>
  );
};

interface MangaCardProps {
  mangaId: string;
  title: string;
  count: number;
  needsReviewCount?: number;
  readProgress: MangaReadProgress;
  coverImage: FinishedImage | null;
  images?: FinishedImage[];
  isDownloading: boolean;
  isReaderLoading: boolean;
  reviewOnly?: boolean;
  isPriority?: boolean;
  detailLinkState: { from: string };
  onRead: (title: string, images?: FinishedImage[]) => void;
  onSummarize?: (title: string) => void;
  isSummarizing?: boolean;
  hasSummary?: boolean;
  onViewSummary?: (title: string) => void;
  onDownloadCbz: (title: string, images?: FinishedImage[]) => void;
  onStartRename?: (title: string) => void;
  onDelete?: (title: string) => void;
  seriesTitle?: string | null;
  isSelected?: boolean;
  isAssigned?: boolean;
  isHighlighted?: boolean;
  onToggleSelect?: (groupId: string) => void;
}

const areMangaCardPropsEqual = (prev: MangaCardProps, next: MangaCardProps) => {
  return (
    prev.mangaId === next.mangaId &&
    prev.title === next.title &&
    prev.count === next.count &&
    prev.needsReviewCount === next.needsReviewCount &&
    prev.readProgress.page === next.readProgress.page &&
    prev.readProgress.complete === next.readProgress.complete &&
    prev.coverImage?.id === next.coverImage?.id &&
    prev.coverImage?.result === next.coverImage?.result &&
    prev.coverImage?.folder === next.coverImage?.folder &&
    prev.isDownloading === next.isDownloading &&
    prev.isReaderLoading === next.isReaderLoading &&
    prev.reviewOnly === next.reviewOnly &&
    prev.isPriority === next.isPriority &&
    prev.isSummarizing === next.isSummarizing &&
    prev.hasSummary === next.hasSummary &&
    prev.onViewSummary === next.onViewSummary &&
    prev.seriesTitle === next.seriesTitle &&
    prev.isSelected === next.isSelected &&
    prev.isAssigned === next.isAssigned &&
    prev.isHighlighted === next.isHighlighted &&
    prev.detailLinkState.from === next.detailLinkState.from &&
    prev.onRead === next.onRead &&
    prev.onSummarize === next.onSummarize &&
    prev.onDownloadCbz === next.onDownloadCbz &&
    prev.onStartRename === next.onStartRename &&
    prev.onDelete === next.onDelete &&
    prev.onToggleSelect === next.onToggleSelect
  );
};

const MangaCardComponent: React.FC<MangaCardProps> = ({
  mangaId,
  title,
  count,
  needsReviewCount = 0,
  readProgress,
  coverImage,
  images,
  isDownloading,
  isReaderLoading,
  reviewOnly = false,
  isPriority = false,
  detailLinkState,
  onRead,
  onSummarize,
  isSummarizing,
  hasSummary = false,
  onViewSummary,
  onDownloadCbz,
  onStartRename,
  onDelete,
  seriesTitle,
  isSelected = false,
  isAssigned = false,
  isHighlighted = false,
  onToggleSelect,
}) => {
  const { src, hasError, isLoaded, handleLoad, handleError } = useGalleryThumbnail(coverImage, "cover");

  const progressPercent = readProgress.complete
    ? 100
    : readProgress.page && count > 0
    ? Math.min(100, Math.round((readProgress.page / count) * 100))
    : 0;

  const progressText = readProgress.complete
    ? 'Completed'
    : readProgress.page
    ? `Page ${readProgress.page} of ${count}`
    : 'Not started';

  const readActionText = readProgress.complete
    ? 'Read again'
    : readProgress.page
    ? 'Continue'
    : 'Read';

  const handleReadClick = useCallback((e: React.MouseEvent) => {
    e.stopPropagation();
    if (e.metaKey || e.ctrlKey) {
      window.open(buildReaderIdUrl(mangaId), '_blank', 'noopener,noreferrer');
      return;
    }
    onRead(title, images);
  }, [mangaId, onRead, title, images]);

  const handleDownloadCbzClick = useCallback((e: React.MouseEvent) => {
    e.stopPropagation();
    onDownloadCbz(title, images);
  }, [onDownloadCbz, title, images]);

  const handleStartRenameClick = useCallback((e?: React.MouseEvent) => {
    e?.stopPropagation();
    onStartRename?.(title);
  }, [onStartRename, title]);

  const handleDeleteClick = useCallback((e: React.MouseEvent) => {
    e.stopPropagation();
    onDelete?.(title);
  }, [onDelete, title]);

  const handleToggleSelectClick = useCallback((e?: React.MouseEvent) => {
    e?.stopPropagation();
    onToggleSelect?.(mangaId);
  }, [onToggleSelect, mangaId]);

  const handleViewSummaryClick = useCallback((e: React.MouseEvent) => {
    e.stopPropagation();
    onViewSummary?.(title);
  }, [onViewSummary, title]);

  const isSelectable = Boolean(onToggleSelect && !isAssigned);

  return (
    <div
      data-manga-id={mangaId}
      style={{ contentVisibility: 'auto', containIntrinsicSize: '0 380px' }}
      className={`flex flex-col rounded-2xl sm:rounded-3xl border bg-[#141416] dark:bg-[#141416] overflow-hidden shadow-xs transition-all duration-300 ${
        isHighlighted
          ? 'border-indigo-500 ring-4 ring-indigo-500/80 shadow-lg shadow-indigo-500/20'
          : isSelected
          ? 'border-indigo-500 ring-2 ring-indigo-500/40'
          : 'border-zinc-800'
      }`}
    >
      {/* Cover Image Container */}
      <div
        onClick={isSelectable ? handleToggleSelectClick : undefined}
        className={`relative aspect-[3/4] w-full overflow-hidden bg-zinc-950 flex items-center justify-center select-none ${
          isSelectable ? 'cursor-pointer' : ''
        }`}
      >
        {src && !hasError ? (
          <img
            src={src}
            alt={title}
            className={`w-full h-full object-cover select-none ${
              isLoaded ? 'opacity-100' : 'opacity-90'
            }`}
            loading={isPriority ? 'eager' : 'lazy'}
            fetchPriority={isPriority ? 'high' : 'auto'}
            decoding="async"
            onLoad={handleLoad}
            onError={handleError}
          />
        ) : (
          <div className="w-full h-full flex flex-col items-center justify-center bg-linear-to-b from-zinc-800 to-zinc-950 text-zinc-400 p-4">
            <div className="w-12 h-12 rounded-2xl bg-zinc-800/80 flex items-center justify-center mb-2 shadow-inner">
              <Icon icon="carbon:book" className="w-6 h-6 text-zinc-400" />
            </div>
            <span className="text-[11px] font-medium text-zinc-400 text-center line-clamp-1">No Cover</span>
          </div>
        )}

        {/* Gradient Overlay for bottom controls readability */}
        <div className="absolute inset-0 bg-gradient-to-t from-zinc-950 via-zinc-950/40 to-transparent pointer-events-none" />

        {/* Top Badges & Actions */}
        <div className="absolute top-2 left-2 right-2 flex items-center justify-between gap-1 pointer-events-none z-10">
          <div className="flex items-center gap-1 min-w-0 overflow-hidden pointer-events-auto">
            {coverImage?.sourceType === 'original' && (
              <span
                className="flex h-6 w-6 shrink-0 items-center justify-center rounded-lg bg-amber-600 text-white shadow-xs border border-white/10"
                title="Original"
              >
                <Icon icon="carbon:image" className="w-3.5 h-3.5" />
              </span>
            )}
            {hasSummary && (
              <button
                type="button"
                onClick={handleViewSummaryClick}
                className="flex h-6 w-6 shrink-0 items-center justify-center rounded-lg bg-indigo-600 hover:bg-indigo-500 text-white shadow-xs border border-white/10 transition-colors cursor-pointer"
                title={`View summary for ${title}`}
                aria-label={`View summary for ${title}`}
              >
                <Icon icon="carbon:document" className="w-3.5 h-3.5" />
              </button>
            )}
            {seriesTitle && (
              <span className="max-w-20 truncate rounded-full bg-indigo-600 px-1.5 py-0.5 text-[10px] font-semibold text-white shadow-xs border border-white/10" title={seriesTitle}>
                {seriesTitle}
              </span>
            )}
            {needsReviewCount > 0 && (
              <span className="rounded-full bg-amber-500 px-1.5 py-0.5 text-[10px] font-semibold text-amber-950 shadow-xs" title={`${needsReviewCount} page${needsReviewCount === 1 ? '' : 's'} need review`}>
                {needsReviewCount} review
              </span>
            )}
          </div>

          <div className="flex items-center gap-1 shrink-0 pointer-events-auto">
            {isDownloading && (
              <span className="flex items-center space-x-1 rounded-full bg-indigo-700 px-1.5 py-0.5 text-[10px] font-medium text-white shadow-xs">
                <Icon icon="carbon:renew" className="w-3 h-3 animate-spin" />
                <span>CBZ</span>
              </span>
            )}
            <button
              type="button"
              onClick={handleDownloadCbzClick}
              disabled={isDownloading}
              className="w-6 h-6 rounded-full bg-black/75 hover:bg-indigo-600 flex items-center justify-center text-white text-[10px] shadow-xs border border-white/10 transition-colors cursor-pointer"
              title="Download CBZ comic archive"
            >
              <Icon icon="carbon:catalog" className="w-3 h-3" />
            </button>
            {onDelete && (
              <button
                type="button"
                onClick={handleDeleteClick}
                className="w-6 h-6 rounded-full bg-black/75 hover:bg-red-600 flex items-center justify-center text-white text-[10px] shadow-xs border border-white/10 transition-colors cursor-pointer"
                title="Delete Manga from library"
              >
                <Icon icon="carbon:trash-can" className="w-3 h-3" />
              </button>
            )}
          </div>
        </div>

        {/* Bottom Section inside Cover: Progress Bar, Status Text, and Read Action Button */}
        <div className="absolute bottom-0 left-0 right-0 p-2.5 flex flex-col gap-1.5 z-20 min-w-0 pointer-events-none">
          {/* Progress Bar */}
          <div className="w-full h-1 bg-white/20 rounded-full overflow-hidden">
            <div
              className={`h-full rounded-full transition-all duration-300 ${
                readProgress.complete ? 'bg-emerald-400' : 'bg-indigo-400'
              }`}
              style={{ width: `${progressPercent}%` }}
            />
          </div>

          {/* Progress Status */}
          <div className="text-[11px] font-medium text-zinc-300 drop-shadow-xs truncate">
            {progressText}
          </div>

          {/* Button: Read (full width) */}
          <div className="w-full min-w-0 pointer-events-auto">
            <button
              type="button"
              onClick={handleReadClick}
              disabled={isReaderLoading}
              className="w-full min-w-0 flex items-center justify-center gap-1.5 py-1.5 px-3 rounded-xl bg-[#6C5CE7] hover:bg-[#5b4be0] active:scale-98 text-white text-xs font-semibold shadow-md transition-colors cursor-pointer disabled:opacity-60"
              title={`${readProgress.complete ? 'Read again' : readProgress.page ? 'Continue reading' : 'Start reading'} ${title}`}
            >
              {isReaderLoading ? (
                <Icon icon="carbon:renew" className="w-3.5 h-3.5 animate-spin shrink-0" />
              ) : (
                <Icon icon="carbon:book" className="w-3.5 h-3.5 shrink-0" />
              )}
              <span className="truncate">{readActionText}</span>
            </button>
          </div>
        </div>
      </div>

      {/* Card Body / Metadata */}
      <div className="p-3 flex flex-col gap-2 bg-[#141416] dark:bg-[#141416]">
        <Link
          to={buildMangaDetailIdUrl(mangaId, reviewOnly)}
          state={detailLinkState}
          className="text-sm font-bold text-zinc-100 dark:text-zinc-100 line-clamp-1 leading-snug hover:text-indigo-400 transition-colors cursor-pointer"
          title={title}
        >
          {title}
        </Link>

        <div className="pt-2 border-t border-zinc-800/80 flex items-center justify-between gap-2 min-w-0">
          <div className="flex items-center gap-1.5 text-zinc-400 text-xs font-medium shrink-0 whitespace-nowrap">
            <Icon icon="carbon:document" className="w-3.5 h-3.5 text-zinc-400 shrink-0" />
            <span>{count} {count === 1 ? 'page' : 'pages'}</span>
          </div>

          {needsReviewCount > 0 ? (
            <Link
              to={buildMangaDetailIdUrl(mangaId, true)}
              state={detailLinkState}
              className="flex items-center gap-1 px-2.5 py-1 rounded-lg bg-amber-500 text-amber-950 hover:bg-amber-400 text-xs font-semibold shadow-xs transition-colors cursor-pointer shrink-0 whitespace-nowrap"
              title={`Review ${needsReviewCount} flagged page${needsReviewCount === 1 ? '' : 's'} in ${title}`}
            >
              <Icon icon="carbon:edit" className="w-3 h-3" />
              <span>Review</span>
            </Link>
          ) : (
            <Link
              to={buildMangaDetailIdUrl(mangaId, reviewOnly)}
              state={detailLinkState}
              className="flex items-center gap-1 px-2.5 py-1 rounded-lg bg-zinc-800/90 hover:bg-zinc-700 text-zinc-200 hover:text-white text-xs font-medium border border-zinc-700/60 shadow-xs transition-colors cursor-pointer shrink-0 whitespace-nowrap"
              title={`Open ${title} details`}
            >
              <span>Details</span>
              <Icon icon="carbon:launch" className="w-3 h-3 text-zinc-400" />
            </Link>
          )}
        </div>
      </div>
    </div>
  );
};

export const MangaCard = React.memo(MangaCardComponent, areMangaCardPropsEqual);
MangaCard.displayName = 'MangaCard';
