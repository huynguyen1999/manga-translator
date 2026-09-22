import React, { useState, useEffect, useLayoutEffect, useRef, useMemo, useCallback } from 'react';
import { Icon } from '@iconify/react';
import { Menu, MenuButton, MenuItem, MenuItems } from '@headlessui/react';
import { Link } from 'react-router';
import type {
  FinishedImage,
  MangaGroupSummary,
  MangaSummary,
  SeriesDetail,
  SeriesMember,
  SeriesSummary,
} from '@/types';
import PreviewImage from './PreviewImage';
import { PageDetailModal } from './PageDetailModal';
import { MangaEditorModal } from './MangaEditorModal';
import { MangaReaderModal } from './MangaReaderModal';
import { SeriesLibrary } from './SeriesLibrary';
import { AssignToSeriesModal } from './AssignToSeriesModal';
import { Pagination } from './Pagination';
import {
  computeRangeSelection,
  clearBrowserTextSelection,
  type SelectionAnchor,
} from '@/utils/selectionUtils';
import { getMangaReadProgress, mergeGalleryImages, type MangaReadProgress } from '@/utils/resultGallery';
import { apiUrl } from '@/utils/api';
import { MANGA_TITLE_MAX_LENGTH } from '@/config';
import { buildMangaDetailIdUrl, DEFAULT_GALLERY_PAGE_SIZE, GALLERY_PAGE_SIZE_OPTIONS, mangaIdForTitle, type GallerySection, type GallerySort } from '@/utils/routeState';
import { addMangaToSeries, createSeries, fetchAllSeries, fetchGroupSeries } from '@/utils/series';
import { pauseSummaryJob, resumeSummaryJob, stopSummaryJob } from '@/utils/summaryJobs';

const useClientLayoutEffect = typeof window === 'undefined' ? useEffect : useLayoutEffect;

interface ResultGalleryProps {
  finishedImages?: FinishedImage[];
  mangaSummaries?: MangaGroupSummary[];
  summaryModel?: string;
  totalGalleryCount?: number;
  totalMangaCount?: number;
  galleryPage?: number;
  galleryPageSize?: number;
  gallerySearch?: string;
  gallerySort?: GallerySort;
  galleryStatus?: MangaStatusFilter;
  reviewOnly?: boolean;
  isLoading?: boolean;
  onClearGallery: () => void;
  onDeleteImage?: (image: FinishedImage) => void;
  onDeleteImages?: (images: FinishedImage[]) => void | Promise<void>;
  onDeleteManga?: (images: FinishedImage[], mangaTitle?: string) => void | Promise<void>;
  onDeleteMangas?: (mangaList: Array<{ title: string; images: FinishedImage[] }>) => void | Promise<void>;
  onReorderMangaPages?: (groupId: string, pageIds: string[]) => Promise<void>;
  onUpdateImage?: (image: FinishedImage) => void;
  onUpdateMangaTitle?: (pageIds: string[], newMangaTitle: string, oldMangaTitle?: string, groupId?: string, folders?: string[]) => void;
  selectedImageForModal?: FinishedImage | null;
  onCloseExternalModal?: () => void;
  onOpenPageView?: (folder: string) => void;
  onOpenPageEdit?: (folder: string) => void;
  onRetryImage?: (image: FinishedImage) => void | Promise<void>;
  onRerenderImage?: (image: FinishedImage) => void | Promise<void>;
  onRerenderImages?: (images: FinishedImage[]) => void | Promise<void>;
  galleryRevision?: number;
  onOpenReader?: (mangaId: string, initialPageIndex?: number, replace?: boolean) => void;
  onCloseOverlay?: () => void;
  initialPageViewFolder?: string | null;
  initialPageEditFolder?: string | null;
  initialReaderMangaId?: string | null;
  initialReaderManga?: string | null;
  selectedMangaId?: string | null;
  selectedMangaTitle?: string | null;
  onOpenMangaDetail?: (mangaId: string, reviewOnly?: boolean) => void;
  onCloseMangaDetail?: () => void;
  gallerySection?: GallerySection;
  initialSeriesId?: string | null;
  onOpenSeriesDetail?: (seriesId: string) => void;
  onCloseSeriesDetail?: () => void;
  onSeriesChanged?: () => void | Promise<void>;
  onGalleryPageChange?: (page: number) => void;
  onGalleryPageSizeChange?: (pageSize: number) => void;
  onGallerySearchChange?: (search: string) => void;
  onGallerySortChange?: (sort: GallerySort) => void;
  onGalleryStatusChange?: (status: MangaStatusFilter) => void;
  onGalleryReviewChange?: (pending: boolean) => void;
}

type SortOption = 'alpha-asc' | 'alpha-desc' | 'date-desc' | 'date-asc';
export type PageSortOption = 'order' | 'name-asc' | 'name-desc' | 'created-asc' | 'created-desc';
type SummaryAvailabilityState = 'loading' | 'summarized' | 'not-summarized' | 'queued' | 'generating' | 'paused' | 'stale' | 'error' | 'unavailable';

// Natural sort comparison (e.g. page_1 before page_10)
const naturalCompare = (a: string, b: string): number => {
  return a.localeCompare(b, undefined, { numeric: true, sensitivity: 'base' });
};

export function sortMangaPages(images: FinishedImage[]): FinishedImage[] {
  return [...images].sort((a, b) => {
    const aOrder = Number(a.pageOrder);
    const bOrder = Number(b.pageOrder);
    const aHasOrder = Number.isFinite(aOrder) && aOrder > 0;
    const bHasOrder = Number.isFinite(bOrder) && bOrder > 0;
    if (aHasOrder !== bHasOrder) return aHasOrder ? -1 : 1;
    if (aHasOrder && bHasOrder && aOrder !== bOrder) {
      return aOrder - bOrder;
    }
    const nameOrder = naturalCompare(a.originalName, b.originalName);
    if (nameOrder !== 0) return nameOrder;
    return naturalCompare(a.folder || a.id, b.folder || b.id);
  });
}

export function sortMangaPagesForOrder(images: FinishedImage[], sortMode: PageSortOption): FinishedImage[] {
  if (sortMode === 'order') return sortMangaPages(images);

  return [...images].sort((a, b) => {
    if (sortMode === 'name-asc' || sortMode === 'name-desc') {
      const difference = naturalCompare(a.originalName, b.originalName);
      if (difference !== 0) return sortMode === 'name-asc' ? difference : -difference;
    } else {
      const aTime = a.finishedAt instanceof Date ? a.finishedAt.getTime() : Date.parse(a.finishedAt);
      const bTime = b.finishedAt instanceof Date ? b.finishedAt.getTime() : Date.parse(b.finishedAt);
      if (Number.isFinite(aTime) && Number.isFinite(bTime) && aTime !== bTime) {
        const difference = aTime - bTime;
        return sortMode === 'created-asc' ? difference : -difference;
      }
    }

    const nameOrder = naturalCompare(a.originalName, b.originalName);
    if (nameOrder !== 0) return nameOrder;
    return naturalCompare(a.folder || a.id, b.folder || b.id);
  });
}

export function calculateDragAutoScrollSpeed(
  clientY: number,
  viewportHeight: number,
  edgeZone = 120,
): number {
  if (viewportHeight <= 0) return 0;
  const zone = Math.min(edgeZone, Math.max(60, viewportHeight * 0.15));
  if (clientY < zone) {
    const ratio = Math.max(0, Math.min(1, (zone - clientY) / zone));
    return -Math.round(4 + ratio * 18);
  }
  if (clientY > viewportHeight - zone) {
    const ratio = Math.max(0, Math.min(1, (clientY - (viewportHeight - zone)) / zone));
    return Math.round(4 + ratio * 18);
  }
  return 0;
}

type SortableMangaGroup = {
  title: string;
  latestFinishedAt?: string | number;
};

export function sortMangaGroups<T extends SortableMangaGroup>(groups: T[], sortMode: SortOption): T[] {
  const getTimestamp = (value?: string | number) => {
    const timestamp = typeof value === 'number' ? value : Date.parse(value || '');
    return Number.isFinite(timestamp) ? timestamp : 0;
  };

  return [...groups].sort((a, b) => {
    if (a.title === 'Ungrouped') return b.title === 'Ungrouped' ? 0 : 1;
    if (b.title === 'Ungrouped') return -1;

    if (sortMode === 'alpha-desc') return naturalCompare(b.title, a.title);
    if (sortMode === 'date-asc' || sortMode === 'date-desc') {
      const difference = getTimestamp(a.latestFinishedAt) - getTimestamp(b.latestFinishedAt);
      if (difference !== 0) return sortMode === 'date-asc' ? difference : -difference;
    }
    return naturalCompare(a.title, b.title);
  });
}

export type MangaStatusFilter = 'all' | 'original' | 'translated' | 'summarized' | 'review';

export function filterMangaGroupsByStatus<T extends {
  coverImage?: FinishedImage | null;
  cover?: FinishedImage | null;
  images?: FinishedImage[];
  hasSummary?: boolean;
  needsReviewCount?: number;
}>(groups: T[], filter: MangaStatusFilter): T[] {
  if (filter === 'all') return groups;
  if (filter === 'original') {
    return groups.filter((g) => {
      if (g.images && g.images.length > 0) {
        return g.images.every((img) => img.sourceType === 'original');
      }
      const cover = g.coverImage || g.cover;
      return cover?.sourceType === 'original';
    });
  }
  if (filter === 'translated') {
    return groups.filter((g) => {
      if (g.images && g.images.length > 0) {
        return g.images.some((img) => img.sourceType !== 'original');
      }
      const cover = g.coverImage || g.cover;
      return cover ? cover.sourceType !== 'original' : false;
    });
  }
  if (filter === 'summarized') {
    return groups.filter((g) => Boolean(g.hasSummary));
  }
  if (filter === 'review') {
    return groups.filter((g) => (g.needsReviewCount ?? 0) > 0);
  }
  return groups;
}

export function shouldShowEmptyLibraryState({
  mangaGroupsLength,
  totalImagesCount,
  mangaSearchQuery,
  statusFilter,
  activeMangaFilter,
  reviewOnly,
}: {
  mangaGroupsLength: number;
  totalImagesCount: number;
  mangaSearchQuery?: string;
  statusFilter?: string;
  activeMangaFilter?: string;
  reviewOnly?: boolean;
}): boolean {
  return (
    mangaGroupsLength === 0 &&
    totalImagesCount === 0 &&
    !(mangaSearchQuery || '').trim() &&
    (!statusFilter || statusFilter === 'all') &&
    (!activeMangaFilter || activeMangaFilter === 'all') &&
    !reviewOnly
  );
}

export function getMangaSummaryAvailability(summary: Pick<MangaSummary, 'summary' | 'stale' | 'jobStatus'>): SummaryAvailabilityState {
  if (summary.jobStatus === 'queued') return 'queued';
  if (summary.jobStatus === 'generating') return 'generating';
  if (summary.jobStatus === 'paused') return 'paused';
  if (summary.jobStatus === 'error') return 'error';
  if (summary.summary && summary.stale) return 'stale';
  return summary.summary ? 'summarized' : 'not-summarized';
}

export const isSummaryPending = (summary: Pick<MangaSummary, 'jobStatus'>): boolean =>
  summary.jobStatus === 'queued' || summary.jobStatus === 'generating' || summary.jobStatus === 'paused';

export function getSuggestedSeriesTitle(selectedMangaTitles: ReadonlyMap<string, string>): string {
  return [...selectedMangaTitles.values()].sort(naturalCompare)[0] || '';
}

export function buildMangaGroupTitles({
  activeSummaries,
  hasExplicitSummaries,
  mangaImages,
  finishedImages,
  activeMangaFilter,
}: {
  activeSummaries: MangaGroupSummary[];
  hasExplicitSummaries: boolean;
  mangaImages?: Record<string, FinishedImage[]>;
  finishedImages?: FinishedImage[];
  activeMangaFilter?: string;
}): string[] {
  const summaryMap = new Map<string, MangaGroupSummary>();
  activeSummaries.forEach((s) => {
    summaryMap.set(s.title, s);
  });

  const allTitles = new Set<string>(summaryMap.keys());
  if (!hasExplicitSummaries) {
    if (mangaImages) {
      Object.keys(mangaImages).forEach((t) => allTitles.add(t));
    }
    (finishedImages || []).forEach((img) => {
      const t = (img.mangaTitle || 'Ungrouped').trim() || 'Ungrouped';
      allTitles.add(t);
    });
  }
  if (activeMangaFilter && activeMangaFilter !== 'all') {
    allTitles.add(activeMangaFilter);
  }

  return Array.from(allTitles).sort((a, b) => {
    if (a === 'Ungrouped') return 1;
    if (b === 'Ungrouped') return -1;
    return naturalCompare(a, b);
  });
}

export function getGalleryPageCorrection(
  page: number,
  pageCount: number,
  isLoading: boolean,
): number | null {
  if (isLoading) return null;
  const correctedPage = Math.min(Math.max(1, page), Math.max(1, pageCount));
  return correctedPage === page ? null : correctedPage;
}

const getStoredMangaReadProgress = (title: string, pageCount: number): MangaReadProgress => {
  if (typeof window === 'undefined') return { page: null, complete: false };
  try {
    return getMangaReadProgress(
      window.localStorage.getItem(`manga-read-page-${title}`),
      window.localStorage.getItem(`manga-read-count-${title}`),
      pageCount,
    );
  } catch {
    return { page: null, complete: false };
  }
};

const MangaReadBadge: React.FC<{ progress: MangaReadProgress; pageCount: number }> = ({ progress, pageCount }) => {
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

// ──────────────────────────────────────────────────────────────
// Gallery Thumbnail Cache & State
// ──────────────────────────────────────────────────────────────
export const loadedThumbnailUrls = new Set<string>();
const blobUrlCache = new WeakMap<Blob, string>();

export function getGalleryThumbnailUrl(
  image: FinishedImage | null | undefined,
  variant: "detail" | "cover" | boolean = "detail"
): string | null {
  if (!image) return null;
  const preferThumbnail = variant !== false;
  const preferredUrl = variant === "cover" ? image.coverUrl : image.detailPreviewUrl;
  if (preferThumbnail && preferredUrl) {
    return apiUrl(preferredUrl);
  }
  if (preferThumbnail && image.folder) {
    return apiUrl(`/result/${image.folder}/${variant === "cover" ? "cover.webp" : "thumbnail.webp"}`);
  }
  if (typeof image.result === 'string') {
    return apiUrl(image.result);
  }
  if (image.result instanceof Blob) {
    if (image.result.size < 1000 && image.folder) {
      return apiUrl(`/result/${image.folder}/${preferThumbnail ? (variant === "cover" ? 'cover.webp' : 'thumbnail.webp') : 'final.png'}`);
    }
    let url = blobUrlCache.get(image.result);
    if (!url) {
      url = URL.createObjectURL(image.result);
      blobUrlCache.set(image.result, url);
    }
    return url;
  }
  if (image.folder) {
    return apiUrl(image.fullUrl || `/result/${image.folder}/final.png`);
  }
  return null;
}

export function getGalleryFallbackUrl(
  image: FinishedImage | null | undefined,
  variant: "detail" | "cover" = "detail",
): string | null {
  if (!image) return null;
  if (variant === "cover") {
    if (image.thumbnailUrl) return apiUrl(image.thumbnailUrl);
    if (image.folder) return apiUrl(`/result/${image.folder}/thumbnail.webp`);
    return null;
  }
  if (image.folder) {
    return apiUrl(image.fullUrl || `/result/${image.folder}/final.png`);
  }
  if (typeof image.result === 'string') {
    return apiUrl(image.result);
  }
  return null;
}

export function useGalleryThumbnail(image: FinishedImage | null | undefined, variant: "detail" | "cover" = "detail") {
  const initialUrl = getGalleryThumbnailUrl(image, variant);
  const [src, setSrc] = useState<string | null>(() => initialUrl);
  const [hasError, setHasError] = useState(false);
  const [isLoaded, setIsLoaded] = useState(() => Boolean(initialUrl && loadedThumbnailUrls.has(initialUrl)));
  const retryCountRef = useRef(0);
  const retryTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    const nextSrc = getGalleryThumbnailUrl(image, variant);
    setSrc(nextSrc);
    setHasError(false);
    setIsLoaded(Boolean(nextSrc && loadedThumbnailUrls.has(nextSrc)));
    retryCountRef.current = 0;
    return () => {
      if (retryTimeoutRef.current) clearTimeout(retryTimeoutRef.current);
    };
  }, [image?.thumbnailUrl, image?.detailPreviewUrl, image?.coverUrl, image?.folder, image?.result, variant]);

  const handleLoad = useCallback(() => {
    if (src) {
      loadedThumbnailUrls.add(src);
      loadedThumbnailUrls.add(src.split('?')[0]);
    }
    setIsLoaded(true);
    setHasError(false);
    retryCountRef.current = 0;
  }, [src]);

  const handleError = useCallback(() => {
    if (!image) {
      setHasError(true);
      return;
    }

    // If this image already succeeded previously, keep the decoded image in memory
    if (src && (loadedThumbnailUrls.has(src) || loadedThumbnailUrls.has(src.split('?')[0]))) {
      return;
    }

    // Keep cover failures on small derivatives instead of loading final.png.
    const fallback = getGalleryFallbackUrl(image, variant);
    if (fallback && src && fallback !== src) {
      setSrc(fallback);
      return;
    }

    if (variant === "cover") {
      setHasError(true);
      return;
    }

    // Transient network or scroll abort retry
    if (retryCountRef.current < 2) {
      retryCountRef.current += 1;
      if (retryTimeoutRef.current) clearTimeout(retryTimeoutRef.current);
      retryTimeoutRef.current = setTimeout(() => {
        setSrc((prev) => {
          if (!prev) return prev;
          const clean = prev.split('?')[0];
          return `${clean}?retry=${Date.now()}`;
        });
      }, 400 * retryCountRef.current);
    } else {
      setHasError(true);
    }
  }, [image, src, variant]);

  return { src, hasError, isLoaded, handleLoad, handleError };
}

interface GalleryCardProps {
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
    prev.onRerender === next.onRerender
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
      className={`group flex flex-col rounded-xl border bg-white dark:bg-zinc-900 overflow-hidden shadow-2xs hover:shadow-md transition-all cursor-pointer ${
        isHighlighted
          ? 'border-indigo-500 ring-2 ring-indigo-500 shadow-lg ring-offset-2 dark:ring-offset-zinc-900'
          : isSelected
          ? 'border-indigo-500 ring-2 ring-indigo-500/20 dark:ring-indigo-500/30'
          : 'border-zinc-200 dark:border-zinc-800 hover:border-indigo-400 dark:hover:border-indigo-600'
      }`}
      onClick={handleCardClick}
    >
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
                      <span>Rerun layout &amp; render</span>
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
            className="mt-1 flex w-full items-center justify-center gap-1.5 rounded-lg border border-amber-700/60 bg-amber-950/30 px-2.5 py-1.5 text-xs font-semibold text-amber-300 transition-colors hover:bg-amber-900/50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-amber-400"
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

const GalleryCard = React.memo(GalleryCardComponent, areGalleryCardPropsEqual);
GalleryCard.displayName = 'GalleryCard';

// ──────────────────────────────────────────────────────────────
// Manga Group Thumbnail — first-page cover shown in group header
// ──────────────────────────────────────────────────────────────
const MangaGroupThumbnail: React.FC<{ image: FinishedImage }> = React.memo(({ image }) => {
  const { src, hasError, isLoaded, handleLoad, handleError } = useGalleryThumbnail(image, "cover");

  if (!src || hasError) {
    return (
      <div className="w-9 h-12 rounded-md bg-zinc-200 dark:bg-zinc-700 flex items-center justify-center shrink-0">
        <Icon icon="carbon:image" className="w-4 h-4 text-zinc-400" />
      </div>
    );
  }

  return (
    <img
      src={src}
      alt="cover"
      className="w-9 h-12 rounded-md object-cover shadow-sm border border-zinc-200 dark:border-zinc-700 shrink-0"
      loading={isLoaded ? 'eager' : 'lazy'}
      decoding="async"
      onLoad={handleLoad}
      onError={handleError}
    />
  );
});

MangaGroupThumbnail.displayName = 'MangaGroupThumbnail';

// ──────────────────────────────────────────────────────────────
// Manga Card — Visual card for a manga collection in gallery view
// ──────────────────────────────────────────────────────────────
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
  onOpenDetails: (title: string, reviewOnly?: boolean) => void;
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
    prev.onOpenDetails === next.onOpenDetails &&
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
  onOpenDetails,
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

  const handleOpenDetailsClick = useCallback((e?: React.MouseEvent) => {
    e?.stopPropagation();
    onOpenDetails(title, reviewOnly);
  }, [onOpenDetails, reviewOnly, title]);

  const handleReviewClick = useCallback((e: React.MouseEvent) => {
    e.stopPropagation();
    onOpenDetails(title, true);
  }, [onOpenDetails, title]);

  const handleReadClick = useCallback((e: React.MouseEvent) => {
    e.stopPropagation();
    onRead(title, images);
  }, [onRead, title, images]);

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
        <h4
          className="text-sm font-bold text-zinc-100 dark:text-zinc-100 line-clamp-1 leading-snug hover:text-indigo-400 transition-colors cursor-pointer"
          title={title}
          onClick={handleOpenDetailsClick}
        >
          {title}
        </h4>

        <div className="pt-2 border-t border-zinc-800/80 flex items-center justify-between gap-2 min-w-0">
          <div className="flex items-center gap-1.5 text-zinc-400 text-xs font-medium shrink-0 whitespace-nowrap">
            <Icon icon="carbon:document" className="w-3.5 h-3.5 text-zinc-400 shrink-0" />
            <span>{count} {count === 1 ? 'page' : 'pages'}</span>
          </div>

          {needsReviewCount > 0 ? (
            <button
              type="button"
              onClick={handleReviewClick}
              className="flex items-center gap-1 px-2.5 py-1 rounded-lg bg-amber-500 text-amber-950 hover:bg-amber-400 text-xs font-semibold shadow-xs transition-colors cursor-pointer shrink-0 whitespace-nowrap"
              title={`Review ${needsReviewCount} flagged page${needsReviewCount === 1 ? '' : 's'} in ${title}`}
            >
              <Icon icon="carbon:edit" className="w-3 h-3" />
              <span>Review</span>
            </button>
          ) : (
            <button
              type="button"
              onClick={handleOpenDetailsClick}
              className="flex items-center gap-1 px-2.5 py-1 rounded-lg bg-zinc-800/90 hover:bg-zinc-700 text-zinc-200 hover:text-white text-xs font-medium border border-zinc-700/60 shadow-xs transition-colors cursor-pointer shrink-0 whitespace-nowrap"
              title={`Open ${title} details`}
            >
              <span>Details</span>
              <Icon icon="carbon:launch" className="w-3 h-3 text-zinc-400" />
            </button>
          )}
        </div>
      </div>
    </div>
  );
};

const MangaCard = React.memo(MangaCardComponent, areMangaCardPropsEqual);
MangaCard.displayName = 'MangaCard';

// ──────────────────────────────────────────────────────────────
// Row Group Cards — Memoized grid for an expanded manga row
// ──────────────────────────────────────────────────────────────
interface RowGroupCardsProps {
  title: string;
  images: FinishedImage[];
  highlightedImageId: string | null;
  selectedImageIds: Set<string>;
  onToggleSelectImage: (id: string, groupImages?: FinishedImage[], shiftKey?: boolean) => void;
  onMoveImage: (image: FinishedImage) => void;
  onReadFromHere: (title: string, images: FinishedImage[], idx: number) => void;
  onClickImage: (image: FinishedImage) => void;
  onDownloadImage: (image: FinishedImage) => void;
  onDeleteImage?: (image: FinishedImage) => void;
  onEditImage?: (image: FinishedImage) => void;
  onRerenderImage?: (image: FinishedImage) => void;
}

const RowGroupCards: React.FC<RowGroupCardsProps> = React.memo(({
  title,
  images,
  highlightedImageId,
  selectedImageIds,
  onToggleSelectImage,
  onMoveImage,
  onReadFromHere,
  onClickImage,
  onDownloadImage,
  onDeleteImage,
  onEditImage,
  onRerenderImage,
}) => {
  const duplicateNames = useMemo(() => {
    const counts = new Map<string, number>();
    images.forEach((image) => {
      const key = image.originalName.toLocaleLowerCase();
      counts.set(key, (counts.get(key) || 0) + 1);
    });
    return new Set([...counts].filter(([, count]) => count > 1).map(([name]) => name));
  }, [images]);

  const handleToggleSelect = useCallback((id: string, shiftKey = false) => {
    onToggleSelectImage(id, images, shiftKey);
  }, [onToggleSelectImage, images]);

  const handleRead = useCallback((pageIndex: number) => {
    onReadFromHere(title, images, pageIndex);
  }, [onReadFromHere, title, images]);

  return (
    <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-5 gap-4">
      {images.map((image, idx) => (
        <GalleryCard
          key={image.id}
          image={image}
          pageIndex={idx + 1}
          showSourcePath={duplicateNames.has(image.originalName.toLocaleLowerCase())}
          isHighlighted={highlightedImageId === image.id}
          isSelected={selectedImageIds.has(image.id)}
          onToggleSelect={handleToggleSelect}
          onMoveToManga={onMoveImage}
          onReadFromHere={handleRead}
          onClick={onClickImage}
          onDownload={onDownloadImage}
          onDelete={onDeleteImage}
          onEdit={image.hasTextRegions && image.sourceType !== 'original' ? onEditImage : undefined}
          onRerender={image.hasTextRegions && image.sourceType !== 'original' ? onRerenderImage : undefined}
        />
      ))}
    </div>
  );
});

RowGroupCards.displayName = 'RowGroupCards';

// Manga reader is now implemented in MangaReaderModal.tsx

export const ResultGallery: React.FC<ResultGalleryProps> = ({
  finishedImages = [],
  mangaSummaries = [],
  summaryModel = 'deepseek-flash',
  totalGalleryCount = 0,
  totalMangaCount = 0,
  galleryPage: requestedGalleryPage = 1,
  galleryPageSize: requestedGalleryPageSize = DEFAULT_GALLERY_PAGE_SIZE,
  gallerySearch = '',
  gallerySort: requestedGallerySort = 'date-desc',
  galleryStatus,
  reviewOnly = false,
  isLoading,
  onClearGallery,
  onDeleteImage,
  onDeleteImages,
  onDeleteManga,
  onDeleteMangas,
  onReorderMangaPages,
  onUpdateImage,
  onUpdateMangaTitle,
  selectedImageForModal = null,
  onCloseExternalModal,
  onOpenPageView,
  onOpenPageEdit,
  onRetryImage,
  onRerenderImage,
  onRerenderImages,
  galleryRevision = 0,
  onOpenReader,
  onCloseOverlay,
  initialPageViewFolder = null,
  initialPageEditFolder = null,
  initialReaderMangaId = null,
  initialReaderManga = null,
  selectedMangaId,
  selectedMangaTitle = null,
  onOpenMangaDetail,
  onCloseMangaDetail,
  gallerySection = 'manga',
  initialSeriesId = null,
  onOpenSeriesDetail,
  onCloseSeriesDetail,
  onSeriesChanged,
  onGalleryPageChange,
  onGalleryPageSizeChange,
  onGallerySearchChange,
  onGallerySortChange,
  onGalleryStatusChange,
  onGalleryReviewChange,
}) => {
  const [selectedImage, setSelectedImage] = useState<FinishedImage | null>(null);
  const [editingImage, setEditingImage] = useState<FinishedImage | null>(null);
  const [isModalOpen, setIsModalOpen] = useState(false);
  const [zoomLevel, setZoomLevel] = useState(1);
  const modalContentRef = useRef<HTMLDivElement>(null);

  // Sorting & Filtering State (page sorting is preview-only until explicitly saved)
  const [sortBy, setSortBy] = useState<SortOption>(requestedGallerySort);
  const [statusFilter, setStatusFilter] = useState<MangaStatusFilter>(galleryStatus || (reviewOnly ? 'review' : 'all'));
  const [activeMangaFilter, setActiveMangaFilter] = useState<string>(selectedMangaTitle || 'all');
  const [galleryPage, setGalleryPage] = useState(() => Math.max(1, requestedGalleryPage));
  const [mangaSearchQuery, setMangaSearchQuery] = useState(gallerySearch);
  const [mangaSearchInput, setMangaSearchInput] = useState(gallerySearch);
  const [viewMode] = useState<'cards' | 'rows'>('cards');

  const handleStatusFilterChange = (nextFilter: MangaStatusFilter) => {
    setStatusFilter(nextFilter);
    setGalleryPage(1);
    if (onGalleryStatusChange) {
      onGalleryStatusChange(nextFilter);
    } else if (nextFilter === 'review') {
      onGalleryReviewChange?.(true);
    } else if (reviewOnly) {
      onGalleryReviewChange?.(false);
    }
  };

  useEffect(() => {
    if (galleryStatus) {
      setStatusFilter(galleryStatus);
    } else if (reviewOnly) {
      setStatusFilter('review');
    } else {
      setStatusFilter('all');
    }
  }, [galleryStatus, reviewOnly]);

  const openMangaDetail = (title: string, reviewOnlyOverride = false) => {
    const group = mangaGroups.find((item) => item.title === title);
    if (onOpenMangaDetail) {
      onOpenMangaDetail(group?.id || mangaIdForTitle(title), reviewOnlyOverride);
    } else {
      setActiveMangaFilter(title);
    }
    setExpandedGroups((prev) => ({ ...prev, [title]: true }));
    void loadMangaImagesIfNeeded(title);
  };

  const closeMangaDetail = () => {
    if (onCloseMangaDetail) {
      onCloseMangaDetail();
    } else {
      setActiveMangaFilter('all');
    }
  };

  const handleSearchSubmit = (q: string) => {
    const search = q.trim();
    if (search === mangaSearchQuery.trim()) return;
    setMangaSearchQuery(search);
    setGalleryPage(1);
    if (onGallerySearchChange) {
      onGallerySearchChange(search);
    } else {
      setGalleryPageAndRoute(1);
    }
    if (search && activeMangaFilter !== 'all') {
      closeMangaDetail();
    }
  };

  useEffect(() => {
    setMangaSearchQuery(gallerySearch);
    setMangaSearchInput(gallerySearch);
  }, [gallerySearch]);

  useEffect(() => {
    setSortBy(requestedGallerySort);
  }, [requestedGallerySort]);

  // Manga sections collapsed by default (stores true when explicitly expanded)
  const [expandedGroups, setExpandedGroups] = useState<Record<string, boolean>>({});

  // Detailed images loaded per manga on-demand when uncollapsed
  const [mangaImages, setMangaImages] = useState<Record<string, FinishedImage[]>>({});
  const [loadingManga, setLoadingManga] = useState<Record<string, boolean>>({});
  const [draggedPageId, setDraggedPageId] = useState<string | null>(null);
  const [dragOverPageId, setDragOverPageId] = useState<string | null>(null);
  const [reorderingGroupId, setReorderingGroupId] = useState<string | null>(null);
  const [pageSort, setPageSort] = useState<PageSortOption>('order');
  const [pageOrderError, setPageOrderError] = useState<string | null>(null);
  const mangaImageLoadsRef = useRef<Partial<Record<string, Promise<FinishedImage[]>>>>({});
  const [fallbackSummaries, setFallbackSummaries] = useState<MangaGroupSummary[]>([]);
  const [isFallbackLoading, setIsFallbackLoading] = useState(() => {
    return isLoading === undefined && (!mangaSummaries || mangaSummaries.length === 0);
  });

  const pageDragAutoScrollFrameRef = useRef<number | null>(null);
  const pageDragAutoScrollSpeedRef = useRef<number>(0);

  const stopPageDragAutoScroll = useCallback(() => {
    pageDragAutoScrollSpeedRef.current = 0;
    if (pageDragAutoScrollFrameRef.current !== null) {
      cancelAnimationFrame(pageDragAutoScrollFrameRef.current);
      pageDragAutoScrollFrameRef.current = null;
    }
  }, []);

  const runPageDragAutoScroll = useCallback(() => {
    const speed = pageDragAutoScrollSpeedRef.current;
    if (speed === 0) {
      pageDragAutoScrollFrameRef.current = null;
      return;
    }
    window.scrollBy(0, speed);
    pageDragAutoScrollFrameRef.current = requestAnimationFrame(runPageDragAutoScroll);
  }, []);

  const startPageDragAutoScroll = useCallback(() => {
    if (pageDragAutoScrollFrameRef.current === null) {
      pageDragAutoScrollFrameRef.current = requestAnimationFrame(runPageDragAutoScroll);
    }
  }, [runPageDragAutoScroll]);

  useEffect(() => {
    if (!draggedPageId) {
      stopPageDragAutoScroll();
      return;
    }

    const handleDragOver = (e: DragEvent) => {
      const speed = calculateDragAutoScrollSpeed(e.clientY, window.innerHeight);
      if (speed !== 0) {
        pageDragAutoScrollSpeedRef.current = speed;
        startPageDragAutoScroll();
      } else {
        stopPageDragAutoScroll();
      }
    };

    const handleDragEnd = () => {
      stopPageDragAutoScroll();
      setDraggedPageId(null);
      setDragOverPageId(null);
    };

    window.addEventListener('dragover', handleDragOver, { passive: true });
    window.addEventListener('dragend', handleDragEnd);
    window.addEventListener('drop', handleDragEnd);

    return () => {
      stopPageDragAutoScroll();
      window.removeEventListener('dragover', handleDragOver);
      window.removeEventListener('dragend', handleDragEnd);
      window.removeEventListener('drop', handleDragEnd);
    };
  }, [draggedPageId, startPageDragAutoScroll, stopPageDragAutoScroll]);

  useEffect(() => stopPageDragAutoScroll, [stopPageDragAutoScroll]);

  useEffect(() => {
    setMangaImages({});
    mangaImageLoadsRef.current = {};
    setExpandedGroups({});
  }, [reviewOnly]);

  useEffect(() => {
    if (galleryRevision === 0) return;
    setMangaImages({});
    mangaImageLoadsRef.current = {};
  }, [galleryRevision]);

  // Selection state for moving/reorganizing pages
  const [selectedImageIds, setSelectedImageIds] = useState<Set<string>>(new Set());
  const lastSelectedGalleryIdRef = useRef<SelectionAnchor | null>(null);
  const [isMoveModalOpen, setIsMoveModalOpen] = useState(false);
  const [targetMangaName, setTargetMangaName] = useState('');
  const [singleImageToMove, setSingleImageToMove] = useState<FinishedImage | null>(null);

  // Manga groups selected for a new series; IDs survive pagination and search changes.
  const [selectedMangaIds, setSelectedMangaIds] = useState<Map<string, string>>(new Map());
  const [isCreateSeriesOpen, setIsCreateSeriesOpen] = useState(false);
  const [newSeriesTitle, setNewSeriesTitle] = useState('');
  const [seriesError, setSeriesError] = useState<string | null>(null);
  const [isCreatingSeries, setIsCreatingSeries] = useState(false);
  const [openSeriesAfterCreate, setOpenSeriesAfterCreate] = useState(false);
  const [createSeriesDraggedId, setCreateSeriesDraggedId] = useState<string | null>(null);
  const [createSeriesDragOverId, setCreateSeriesDragOverId] = useState<string | null>(null);
  const [createdSeriesToast, setCreatedSeriesToast] = useState<{
    seriesId: string;
    title: string;
    count: number;
  } | null>(null);
  const [assigningManga, setAssigningManga] = useState<{
    id: string;
    title: string;
    seriesId?: string | null;
    seriesTitle?: string | null;
  } | null>(null);

  // Dual mode series modal (Create new series vs Add to existing series)
  const [seriesModalMode, setSeriesModalMode] = useState<'create' | 'add'>('create');
  const [allExistingSeries, setAllExistingSeries] = useState<SeriesSummary[]>([]);
  const [isLoadingExistingSeries, setIsLoadingExistingSeries] = useState(false);
  const [existingSeriesSearch, setExistingSeriesSearch] = useState('');
  const [targetExistingSeriesId, setTargetExistingSeriesId] = useState<string | null>(null);

  const loadGalleryAllSeries = useCallback(async () => {
    setIsLoadingExistingSeries(true);
    try {
      const list = await fetchAllSeries();
      setAllExistingSeries(list);
      if (list.length > 0) {
        setTargetExistingSeriesId((current) => current || list[0].id);
      }
    } catch (err) {
      console.warn('Failed to load existing series list:', err);
    } finally {
      setIsLoadingExistingSeries(false);
    }
  }, []);

  const filteredExistingSeries = useMemo(() => {
    const q = existingSeriesSearch.trim().toLowerCase();
    if (!q) return allExistingSeries;
    return allExistingSeries.filter((s) => s.title.toLowerCase().includes(q));
  }, [allExistingSeries, existingSeriesSearch]);

  // Confirm-delete state for whole manga groups and bulk selections
  const [confirmDeleteManga, setConfirmDeleteManga] = useState<string | null>(null);
  const [confirmDeleteSelectedPages, setConfirmDeleteSelectedPages] = useState(false);
  const [isDeletingSelectedPages, setIsDeletingSelectedPages] = useState(false);
  const [confirmDeleteSelectedMangas, setConfirmDeleteSelectedMangas] = useState(false);
  const [isDeletingSelectedMangas, setIsDeletingSelectedMangas] = useState(false);

  // Renaming manga inline
  const [renamingManga, setRenamingManga] = useState<string | null>(null);
  const [renameInputValue, setRenameInputValue] = useState('');

  // CBZ downloading loading state
  const [downloadingCbz, setDownloadingCbz] = useState<Record<string, boolean>>({});

  // Scroll reader state
  const [readingManga, setReadingManga] = useState<{
    groupId: string;
    title: string;
    images: FinishedImage[];
    initialPageIndex?: number;
    series: SeriesDetail | null;
  } | null>(null);
  const [lastExitedReadPosition, setLastExitedReadPosition] = useState<{
    mangaTitle: string;
    mangaId?: string;
    pageIndex: number;
    imageId?: string;
  } | null>(null);
  const [highlightedImageId, setHighlightedImageId] = useState<string | null>(null);
  const [highlightedMangaId, setHighlightedMangaId] = useState<string | null>(null);
  const [readerLoadingTitle, setReaderLoadingTitle] = useState<string | null>(null);
  const [readerLoadError, setReaderLoadError] = useState<string | null>(null);
  const [summaryState, setSummaryState] = useState<{
    title: string;
    data: MangaSummary | null;
    loading: boolean;
    error: string | null;
  } | null>(null);
  const [summaryAvailability, setSummaryAvailability] = useState<{
    title: string;
    state: SummaryAvailabilityState;
  } | null>(null);
  const [summarizingTitle, setSummarizingTitle] = useState<string | null>(null);
  const [summaryCopied, setSummaryCopied] = useState(false);
  useEffect(() => {
    if (!summaryState && !isCreateSeriesOpen && !isMoveModalOpen && !confirmDeleteManga && !confirmDeleteSelectedPages && !confirmDeleteSelectedMangas) return;
    const handleModalKeyDown = (event: KeyboardEvent) => {
      if (event.key !== 'Escape') return;
      if (summaryState) setSummaryState(null);
      else if (isCreateSeriesOpen) setIsCreateSeriesOpen(false);
      else if (isMoveModalOpen) setIsMoveModalOpen(false);
      else if (confirmDeleteSelectedPages) setConfirmDeleteSelectedPages(false);
      else if (confirmDeleteSelectedMangas) setConfirmDeleteSelectedMangas(false);
      else setConfirmDeleteManga(null);
    };
    window.addEventListener('keydown', handleModalKeyDown);
    return () => window.removeEventListener('keydown', handleModalKeyDown);
  }, [summaryState, isCreateSeriesOpen, isMoveModalOpen, confirmDeleteManga, confirmDeleteSelectedPages, confirmDeleteSelectedMangas]);

  // Lock body scroll when synopsis modal is active to prevent scroll contention and lag
  useEffect(() => {
    if (!summaryState) return;
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    return () => {
      document.body.style.overflow = previousOverflow;
    };
  }, [Boolean(summaryState)]);

  // Sync external modal requests
  useEffect(() => {
    if (selectedImageForModal) {
      setSelectedImage(selectedImageForModal);
      setIsModalOpen(true);
      setZoomLevel(1);
    }
  }, [selectedImageForModal]);


  // Fetch groups summary if not provided by parent
  useEffect(() => {
    if (isLoading === undefined && mangaSummaries === undefined) {
      setIsFallbackLoading(true);
      fetch(apiUrl('/api/results/groups'))
        .then((res) => res.json())
        .then((data) => {
          if (data.groups && Array.isArray(data.groups)) {
            setFallbackSummaries(data.groups);
          }
        })
        .catch(() => {})
        .finally(() => {
          setIsFallbackLoading(false);
        });
    }
  }, [mangaSummaries, isLoading]);

  const effectiveIsLoading = isLoading !== undefined ? isLoading : isFallbackLoading;

  const activeSummaries = useMemo(() => {
    return mangaSummaries !== undefined ? mangaSummaries : fallbackSummaries;
  }, [mangaSummaries, fallbackSummaries]);

  // On-demand loader for a manga's images when uncollapsed
  const loadMangaImagesIfNeeded = async (title: string, detail?: string, requestedGroupId?: string): Promise<FinishedImage[]> => {
    if (mangaImages[title]) {
      return mangaImages[title];
    }
    if (mangaImageLoadsRef.current[title]) return mangaImageLoadsRef.current[title];

    setLoadingManga((prev) => ({ ...prev, [title]: true }));
    const request = (async () => {
      try {
      const groupId = requestedGroupId || activeSummaries.find((summary) => summary.title === title)?.id;
      const groupFilter = groupId || title;
      const detailParam = detail ? `&detail=${encodeURIComponent(detail)}` : '';
      const reviewParam = reviewOnly ? '&review=pending' : '';
      const rawItems: any[] = [];
      let offset = 0;
      let hasNextPage = true;
      while (hasNextPage) {
        const res = await fetch(
          apiUrl(
            `/api/results/list?groupId=${encodeURIComponent(groupFilter)}&sort=alpha&limit=500&offset=${offset}${detailParam}${reviewParam}`,
          ),
        );
        if (!res.ok) break;
        const data = await res.json();
        if (!Array.isArray(data.items)) break;
        rawItems.push(...data.items);
        hasNextPage = data.nextOffset !== null && data.nextOffset !== undefined;
        offset = Number(data.nextOffset || 0);
      }
      if (rawItems.length > 0) {
          const items: FinishedImage[] = rawItems.map((item: any) => ({
            id: item.id || item.folder,
            originalName: (item.originalName && item.originalName !== 'Unknown') ? item.originalName : `${item.folder}.png`,
            pageOrder: item.pageOrder ?? null,
            sourcePath: item.sourcePath ?? null,
            result: item.resultUrl
              ? apiUrl(item.resultUrl)
              : item.folder
              ? apiUrl(`/result/${item.folder}/final.png`)
              : "",
            thumbnailUrl: item.thumbnailUrl
              ? apiUrl(item.thumbnailUrl)
              : (item.folder ? apiUrl(`/result/${item.folder}/thumbnail.webp`) : null),
            batchPreviewUrl: item.batchPreviewUrl ? apiUrl(item.batchPreviewUrl) : null,
            coverUrl: item.coverUrl ? apiUrl(item.coverUrl) : null,
            detailPreviewUrl: item.detailPreviewUrl ? apiUrl(item.detailPreviewUrl) : null,
            readerUrl: item.readerUrl ? apiUrl(item.readerUrl) : null,
            fullUrl: item.fullUrl ? apiUrl(item.fullUrl) : (item.resultUrl ? apiUrl(item.resultUrl) : null),
            sourceType: item.sourceType === 'original' ? 'original' : 'translated',
            inputUrl: item.inputUrl
              ? apiUrl(item.inputUrl)
              : (item.folder ? apiUrl(`/result/${item.folder}/input.png`) : null),
            inpaintedUrl: item.inpaintedUrl ? apiUrl(item.inpaintedUrl) : null,
            textRegionsUrl: item.textRegionsUrl ? apiUrl(item.textRegionsUrl) : null,
            bubbleMaskUrl: item.bubbleMaskUrl ? apiUrl(item.bubbleMaskUrl) : null,
            hasTextRegions: item.hasTextRegions ?? Boolean(item.textRegionsUrl),
            reviewStatus: item.reviewStatus || (item.needsReview ? 'pending' : 'not_required'),
            reviewedAt: item.reviewedAt || null,
            folder: item.folder,
            groupId: item.groupId || groupId || null,
            mangaTitle: item.mangaTitle || title,
            seriesId: item.seriesId || null,
            seriesTitle: item.seriesTitle || null,
            finishedAt: item.finishedAt ? new Date(item.finishedAt) : new Date(),
            startedAt: item.startedAt ? new Date(item.startedAt) : null,
            durationMs: item.durationMs ?? null,
            settings: item.settings || {},
          }));
          setMangaImages((prev) => ({
            ...prev,
            [title]: items,
          }));
          return items;
      }
      } catch (err) {
        console.error(`Failed to fetch images for manga "${title}":`, err);
      } finally {
        delete mangaImageLoadsRef.current[title];
        setLoadingManga((prev) => ({ ...prev, [title]: false }));
      }
      return [];
    })();
    mangaImageLoadsRef.current[title] = request;
    return request;
  };

  // Sync route-owned manga selection before paint so route transitions never show stale cards.
  useClientLayoutEffect(() => {
    if (selectedMangaId !== undefined) {
      const selected = selectedMangaId
        ? activeSummaries.find((summary) => summary.id === selectedMangaId || mangaIdForTitle(summary.title) === selectedMangaId || summary.title === selectedMangaId)
        : null;
      if (selectedMangaId && !selected) return;
      const target = selected?.title || 'all';
      setActiveMangaFilter(target);
      if (selected) {
        setExpandedGroups((prev) => ({ ...prev, [selected.title]: true }));
        void loadMangaImagesIfNeeded(selected.title);
      }
      return;
    }

    if (selectedMangaTitle !== undefined) {
      const target = selectedMangaTitle || 'all';
      setActiveMangaFilter(target);
      if (selectedMangaTitle) {
        setExpandedGroups((prev) => ({ ...prev, [selectedMangaTitle]: true }));
        void loadMangaImagesIfNeeded(selectedMangaTitle);
      }
    }
  }, [activeSummaries, selectedMangaId, selectedMangaTitle]);

  const hasExplicitSummaries = mangaSummaries !== undefined || fallbackSummaries.length > 0;

  // Build manga display collections from summaries and loaded images
  const mangaGroups = useMemo(() => {
    const summaryMap = new Map<string, MangaGroupSummary>();
    activeSummaries.forEach((s) => {
      summaryMap.set(s.title, s);
    });

    const keys = buildMangaGroupTitles({
      activeSummaries,
      hasExplicitSummaries,
      mangaImages,
      finishedImages,
      activeMangaFilter,
    });

    const groups: {
      id: string;
      title: string;
      count: number;
      coverImage: FinishedImage | null;
      images: FinishedImage[];
      latestFinishedAt?: string | number;
      seriesId?: string | null;
      seriesTitle?: string | null;
      hasSummary?: boolean;
      needsReviewCount: number;
      isLoaded: boolean;
      isLoading: boolean;
    }[] = [];

    keys.forEach((key) => {
      const summary = summaryMap.get(key);
      const loaded = mangaImages[key];
      const sessionExtra = (finishedImages || []).filter(
        (img) => ((img.mangaTitle || 'Ungrouped').trim() || 'Ungrouped') === key
      );

      let images: FinishedImage[] = [];
      let isLoaded = false;

      if (loaded) {
        isLoaded = true;
        images = sortMangaPages(mergeGalleryImages(loaded, sessionExtra));
      } else if (sessionExtra.length > 0) {
        images = sortMangaPages(sessionExtra);
      }

      const count = summary?.count ?? images.length;
      const needsReviewCount = reviewOnly && isLoaded
        ? images.length
        : summary?.needsReviewCount ?? images.filter((image) => image.reviewStatus === 'pending').length;
      const cover = summary?.cover || (images.length > 0 ? images[0] : null);
      const summaryTimestamp = summary?.latestFinishedAt ? new Date(summary.latestFinishedAt).getTime() : 0;
      const latestFinishedAt = images.reduce(
        (latest, image) => {
          const timestamp = new Date(image.finishedAt).getTime();
          return Number.isFinite(timestamp) ? Math.max(latest, timestamp) : latest;
        },
        Number.isFinite(summaryTimestamp) ? summaryTimestamp : 0,
      );
      const hasSummary = Boolean(
        summary?.hasSummary ||
        (summaryState?.title === key && Boolean(summaryState.data?.summary)) ||
        (summaryAvailability?.title === key && summaryAvailability.state === 'summarized')
      );

      groups.push({
        id: summary?.id || mangaIdForTitle(key),
        title: key,
        count: reviewOnly ? needsReviewCount : Math.max(count, images.length),
        coverImage: cover,
        images,
        latestFinishedAt,
        seriesId: summary?.seriesId || null,
        seriesTitle: summary?.seriesTitle || null,
        hasSummary,
        needsReviewCount,
        isLoaded,
        isLoading: Boolean(loadingManga[key]) || (!isLoaded && key === activeMangaFilter),
      });
    });

    return sortMangaGroups(reviewOnly ? groups.filter((group) => group.needsReviewCount > 0) : groups, sortBy);
  }, [activeSummaries, hasExplicitSummaries, mangaImages, finishedImages, loadingManga, sortBy, activeMangaFilter, summaryState, summaryAvailability, reviewOnly]);

  // Automatically load images for active manga or when only 1 manga group exists
  useEffect(() => {
    if (activeMangaFilter !== 'all') {
      loadMangaImagesIfNeeded(activeMangaFilter);
    }
  }, [activeMangaFilter]);

  useEffect(() => {
    if (mangaGroups.length === 1 && !mangaImages[mangaGroups[0].title]) {
      loadMangaImagesIfNeeded(mangaGroups[0].title);
    }
  }, [mangaGroups, mangaImages]);

  const allLoadedImages = useMemo(() => {
    const list: FinishedImage[] = [];
    mangaGroups.forEach((g) => {
      list.push(...g.images);
    });
    return list;
  }, [mangaGroups]);

  const totalImagesCount = useMemo(() => {
    if (typeof totalGalleryCount === 'number' && totalGalleryCount > 0) {
      return totalGalleryCount;
    }
    return mangaGroups.reduce((acc, g) => acc + g.count, 0);
  }, [totalGalleryCount, mangaGroups]);

  // List of images for modal navigation, scoped to the current image's manga group
  const currentModalImages = useMemo(() => {
    if (!selectedImage) return [];

    const targetGroupTitle = (selectedImage.mangaTitle || 'Ungrouped').trim() || 'Ungrouped';
    const group = mangaGroups.find((g) => g.title === targetGroupTitle);
    if (group && group.images.length > 0) {
      return group.images;
    }

    return allLoadedImages.length > 0 ? sortMangaPages(allLoadedImages) : [selectedImage];
  }, [selectedImage, mangaGroups, allLoadedImages]);

  const toggleGroupCollapse = (title: string) => {
    const willBeExpanded = !expandedGroups[title];
    setExpandedGroups((prev) => ({ ...prev, [title]: willBeExpanded }));
    if (willBeExpanded) {
      loadMangaImagesIfNeeded(title);
    }
  };

  const toggleSelectImage = useCallback((id: string, groupImages?: FinishedImage[], shiftKey = false) => {
    const allIds = groupImages && groupImages.length > 0 ? groupImages.map((img) => img.id) : [id];

    setSelectedImageIds((prev) => {
      const res = computeRangeSelection({
        selectedIds: prev,
        allIds,
        targetId: id,
        shiftKey,
        anchor: lastSelectedGalleryIdRef.current,
      });
      lastSelectedGalleryIdRef.current = res.nextAnchor;
      return res.nextSelectedIds;
    });

    if (shiftKey) {
      clearBrowserTextSelection();
    }
  }, []);

  const toggleSelectAllInGroup = (images: FinishedImage[]) => {
    const allSelected = images.every((img) => selectedImageIds.has(img.id));
    setSelectedImageIds((prev) => {
      const next = new Set(prev);
      images.forEach((img) => {
        if (allSelected) {
          next.delete(img.id);
        } else {
          next.add(img.id);
        }
      });
      return next;
    });
    lastSelectedGalleryIdRef.current = null;
  };

  const requestRerender = useCallback((images: FinishedImage[]) => {
    if (!onRerenderImages) return;
    void Promise.resolve(onRerenderImages(images)).catch((error) => {
      window.alert(error instanceof Error ? error.message : 'Could not queue rerender.');
    });
  }, [onRerenderImages]);

  const handleStartRename = (title: string) => {
    setRenamingManga(title);
    setRenameInputValue(title);
  };

  const handleSaveRename = (oldTitle: string) => {
    const newTitle = renameInputValue.trim();
    if (newTitle && newTitle !== oldTitle) {
      const group = mangaGroups.find((g) => g.title === oldTitle);
        const folders = group?.images
        ? group.images.map((img) => img.folder || img.id).filter(Boolean)
        : [];
      const pageIds = group?.images?.map((img) => img.id).filter(Boolean) || [];
      onUpdateMangaTitle?.(pageIds, newTitle, oldTitle, group?.id, folders);
      if (typeof window !== 'undefined') {
        for (const suffix of ['pos', 'page', 'count']) {
          const oldKey = `manga-read-${suffix}-${oldTitle}`;
          const newKey = `manga-read-${suffix}-${newTitle}`;
          const value = window.localStorage.getItem(oldKey);
          if (value !== null && window.localStorage.getItem(newKey) === null) {
            window.localStorage.setItem(newKey, value);
          }
          window.localStorage.removeItem(oldKey);
        }
      }
      if (activeMangaFilter === oldTitle) {
        if (onOpenMangaDetail) {
          onOpenMangaDetail(group?.id || mangaIdForTitle(newTitle));
        } else {
          setActiveMangaFilter(newTitle);
        }
      }
      setMangaImages((prev) => {
        if (prev[oldTitle]) {
          const next = { ...prev, [newTitle]: prev[oldTitle].map((img) => ({ ...img, mangaTitle: newTitle })) };
          delete next[oldTitle];
          return next;
        }
        return prev;
      });
      setExpandedGroups((prev) => {
        if (prev[oldTitle] !== undefined) {
          const next = { ...prev, [newTitle]: prev[oldTitle] };
          delete next[oldTitle];
          return next;
        }
        return prev;
      });
      if (group?.id) {
        setSelectedMangaIds((previous) => {
          if (!previous.has(group.id)) return previous;
          const next = new Map(previous);
          next.set(group.id, newTitle);
          return next;
        });
      }
    }
    setRenamingManga(null);
  };

  const toggleMangaSeriesSelection = (groupId: string) => {
    const group = mangaGroups.find((item) => item.id === groupId);
    if (!group) return;
    setSelectedMangaIds((previous) => {
      const next = new Map(previous);
      next.has(groupId) ? next.delete(groupId) : next.set(groupId, group.title);
      return next;
    });
  };

  useEffect(() => {
    if (!createdSeriesToast) return;
    const timer = window.setTimeout(() => {
      setCreatedSeriesToast(null);
    }, 6000);
    return () => window.clearTimeout(timer);
  }, [createdSeriesToast]);

  useEffect(() => {
    if (selectedMangaIds.size === 0) return;
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape' && !isCreateSeriesOpen && !isMoveModalOpen && !confirmDeleteManga && !confirmDeleteSelectedPages && !confirmDeleteSelectedMangas && !summaryState) {
        setSelectedMangaIds(new Map());
      }
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [selectedMangaIds.size, isCreateSeriesOpen, isMoveModalOpen, confirmDeleteManga, confirmDeleteSelectedPages, confirmDeleteSelectedMangas, summaryState]);

  const moveSelectedManga = (index: number, direction: -1 | 1) => {
    const entries = Array.from(selectedMangaIds.entries());
    const target = index + direction;
    if (target < 0 || target >= entries.length) return;
    const next = [...entries];
    [next[index], next[target]] = [next[target], next[index]];
    setSelectedMangaIds(new Map(next));
  };

  const dropSelectedManga = (sourceId: string, targetId: string) => {
    if (!sourceId || sourceId === targetId) return;
    const entries = Array.from(selectedMangaIds.entries());
    const sourceIndex = entries.findIndex(([id]) => id === sourceId);
    const targetIndex = entries.findIndex(([id]) => id === targetId);
    if (sourceIndex === -1 || targetIndex === -1) return;
    const next = [...entries];
    const [removed] = next.splice(sourceIndex, 1);
    next.splice(targetIndex, 0, removed);
    setCreateSeriesDraggedId(null);
    setCreateSeriesDragOverId(null);
    setSelectedMangaIds(new Map(next));
  };

  const sortSelectedManga = (mode: 'natural' | 'alpha-desc' | 'reverse') => {
    const entries = Array.from(selectedMangaIds.entries());
    const sorted = [...entries];
    if (mode === 'natural') {
      sorted.sort((a, b) => naturalCompare(a[1], b[1]));
    } else if (mode === 'alpha-desc') {
      sorted.sort((a, b) => naturalCompare(b[1], a[1]));
    } else if (mode === 'reverse') {
      sorted.reverse();
    }
    setSelectedMangaIds(new Map(sorted));
  };

  const handleCreateSeries = async () => {
    const title = newSeriesTitle.trim();
    if (selectedMangaIds.size < 2) {
      setSeriesError('Select at least two unassigned manga.');
      return;
    }
    if (!title) {
      setSeriesError('Enter a series title.');
      return;
    }
    if (isCreatingSeries) return;
    setIsCreatingSeries(true);
    setSeriesError(null);
    try {
      const selectedCount = selectedMangaIds.size;
      const created = await createSeries(title, [...selectedMangaIds.keys()]);
      setSelectedMangaIds(new Map());
      setNewSeriesTitle('');
      setIsCreateSeriesOpen(false);
      await onSeriesChanged?.();
      if (openSeriesAfterCreate) {
        onOpenSeriesDetail?.(created.id);
      } else {
        setCreatedSeriesToast({
          seriesId: created.id,
          title: created.title,
          count: selectedCount,
        });
      }
    } catch (reason) {
      setSeriesError(reason instanceof Error ? reason.message : 'Could not create series.');
    } finally {
      setIsCreatingSeries(false);
    }
  };

  const handleAddToExistingSeries = async () => {
    if (!targetExistingSeriesId) {
      setSeriesError('Please select an existing series to add to.');
      return;
    }
    if (selectedMangaIds.size === 0) {
      setSeriesError('Please select at least one manga.');
      return;
    }
    if (isCreatingSeries) return;
    setIsCreatingSeries(true);
    setSeriesError(null);
    try {
      const targetId = targetExistingSeriesId;
      const targetObj = allExistingSeries.find((s) => s.id === targetId);
      const updated = await addMangaToSeries(targetId, Array.from(selectedMangaIds.keys()));
      setSelectedMangaIds(new Map());
      setIsCreateSeriesOpen(false);
      await onSeriesChanged?.();
      if (openSeriesAfterCreate) {
        onOpenSeriesDetail?.(targetId);
      } else {
        setCreatedSeriesToast({
          seriesId: targetId,
          title: updated.title || targetObj?.title || 'Series',
          count: updated.members.length,
        });
      }
    } catch (reason) {
      setSeriesError(reason instanceof Error ? reason.message : 'Could not add manga to series.');
    } finally {
      setIsCreatingSeries(false);
    }
  };

  const handleMoveSelected = (targetTitle: string) => {
    const cleanTitle = targetTitle.trim() || 'Ungrouped';
    let targetImages: FinishedImage[] = [];

    if (singleImageToMove) {
      targetImages = [singleImageToMove];
    } else {
      targetImages = allLoadedImages.filter((img) => selectedImageIds.has(img.id));
    }

    const pageIds = targetImages
      .map((img) => img.id)
      .filter(Boolean);
    const folders = targetImages.map((img) => img.folder || img.id).filter(Boolean);

    if (pageIds.length > 0) {
      onUpdateMangaTitle?.(pageIds, cleanTitle, undefined, undefined, folders);
      setMangaImages((prev) => {
        const next = { ...prev };
        const movedIds = new Set(targetImages.map((i) => i.id));
        Object.keys(next).forEach((k) => {
          next[k] = next[k].filter((i) => !movedIds.has(i.id));
        });
        if (next[cleanTitle]) {
          const updatedTargetImages = targetImages.map((i) => ({ ...i, mangaTitle: cleanTitle }));
          next[cleanTitle] = [...updatedTargetImages, ...next[cleanTitle]];
        }
        return next;
      });
    }

    setSelectedImageIds(new Set());
    lastSelectedGalleryIdRef.current = null;
    setSingleImageToMove(null);
    setIsMoveModalOpen(false);
    setTargetMangaName('');
  };

  const handleReadManga = async (
    groupTitle: string,
    existingImages?: FinishedImage[],
    initialPageIndex?: number,
    requestedGroupId?: string,
    updateRoute = true,
  ) => {
    if (readerLoadingTitle) return;
    setReaderLoadingTitle(groupTitle);
    setReaderLoadError(null);

    try {
      const group = requestedGroupId
        ? mangaGroups.find((item) => item.id === requestedGroupId)
        : mangaGroups.find((item) => item.title === groupTitle);
      const groupId = requestedGroupId || group?.id || mangaIdForTitle(groupTitle);
      let images = existingImages;
      if (!group?.isLoaded || !images || images.length === 0) {
        images = await loadMangaImagesIfNeeded(groupTitle, 'reader', groupId);
      }
      const resolvedTitle = groupTitle || images?.[0]?.mangaTitle || initialReaderManga || 'Manga';
      if (images && images.length > 0) {
        if (updateRoute && onOpenReader) {
          onOpenReader(groupId, initialPageIndex);
        }
        setReadingManga({
          groupId,
          title: resolvedTitle,
          images: sortMangaPages(images),
          initialPageIndex,
          series: null,
        });
        void fetchGroupSeries(groupId)
          .then((series) => setReadingManga((current) =>
            current?.groupId === groupId ? { ...current, series } : current,
          ))
          .catch(() => {});
      } else {
        setReaderLoadError(groupTitle);
      }
    } finally {
      setReaderLoadingTitle(null);
    }
  };

  const handleSelectSeriesMember = async (member: SeriesMember) => {
    if (!readingManga || readerLoadingTitle) return;
    setReaderLoadingTitle(member.title);
    setReaderLoadError(null);
    try {
      const images = await loadMangaImagesIfNeeded(member.title, 'reader', member.id);
      if (!images.length) {
        setReaderLoadError(member.title);
        return;
      }
      onOpenReader?.(member.id, 0, true);
      setReadingManga({
        groupId: member.id,
        title: member.title,
        images: sortMangaPages(images),
        initialPageIndex: 0,
        series: readingManga.series,
      });
    } finally {
      setReaderLoadingTitle(null);
    }
  };

  useEffect(() => {
    const members = readingManga?.series?.members;
    if (!members || members.length < 2) return;
    const index = members.findIndex((member) => member.id === readingManga.groupId);
    for (const member of [members[index - 1], members[index + 1]]) {
      if (member && !mangaImages[member.title]) {
        void loadMangaImagesIfNeeded(member.title, 'reader', member.id);
      }
    }
  }, [readingManga?.groupId, readingManga?.series, mangaImages]);

  const handleSummarize = async (title: string, regenerate = false, refreshText = false) => {
    if (summarizingTitle) return;
    const retainedSummary = summaryState?.title === title ? summaryState.data : null;
    setSummarizingTitle(title);
    setSummaryCopied(false);
    const groupId = mangaGroups.find((group) => group.title === title)?.id;

    try {
      let existing: MangaSummary | null = null;
      if (!regenerate) {
        const statusResponse = await fetch(apiUrl(`/api/results/group/summary?${groupId ? `groupId=${encodeURIComponent(groupId)}&` : ''}title=${encodeURIComponent(title)}`));
        const statusPayload = await statusResponse.json().catch(() => ({}));
        if (!statusResponse.ok) {
          throw new Error(statusPayload.detail || 'Could not load the manga summary.');
        }
        existing = statusPayload as MangaSummary;
      }

      // If valid existing summary is ready and not regenerating, open modal immediately
      if (existing?.summary && !existing.stale && !regenerate) {
        setSummaryAvailability({ title, state: 'summarized' });
        setSummaryState({ title, data: existing, loading: false, error: null });
        setSummarizingTitle(null);
        return;
      }

      // If generating / regenerating, run in background without blocking the viewport
      setSummaryAvailability({ title, state: 'generating' });

      // If user had already opened the summary modal for this title, keep showing its loading state
      if (summaryState?.title === title) {
        setSummaryState({ title, data: existing?.summary ? existing : retainedSummary, loading: true, error: null });
      }

      const response = await fetch(apiUrl('/api/results/group/summary'), {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          groupId: groupId || undefined,
          mangaTitle: title,
          summaryModel,
          regenerate: Boolean(regenerate || refreshText || existing?.stale),
          refreshText,
        }),
      });
      const payload = await response.json().catch(() => ({}));
      if (!response.ok) {
        throw new Error(payload.detail || 'The selected model could not generate a summary.');
      }
      const newSummary = payload as MangaSummary;

      if (isSummaryPending(newSummary)) {
        setSummaryAvailability({ title, state: getMangaSummaryAvailability(newSummary) });
        setSummaryState((previous) => {
          if (previous?.title === title) {
            return { title, data: newSummary.summary ? newSummary : previous.data, loading: true, error: null };
          }
          return previous;
        });
        return;
      }

      setSummaryAvailability({ title, state: getMangaSummaryAvailability(newSummary) });

      setSummaryState((previous) => {
        if (previous?.title === title) {
          return { title, data: newSummary, loading: false, error: null };
        }
        return previous;
      });
    } catch (error) {
      const errText = error instanceof Error ? error.message : 'Could not generate a summary.';
      setSummaryAvailability({ title, state: 'error' });
      setSummaryState((previous) => {
        if (previous?.title === title) {
          return {
            title,
            data: previous?.data || null,
            loading: false,
            error: errText,
          };
        }
        return previous;
      });
    } finally {
      setSummarizingTitle(null);
    }
  };

  useEffect(() => {
    const summaryIsPending = summaryAvailability?.state === 'queued' || summaryAvailability?.state === 'generating' || summaryAvailability?.state === 'paused';
    if (!summaryIsPending) return;
    let cancelled = false;
    const title = summaryAvailability.title;
    const groupId = summaryState?.title === title
      ? summaryState.data?.groupId || mangaGroups.find((group) => group.title === title)?.id
      : mangaGroups.find((group) => group.title === title)?.id;

    const poll = async () => {
      try {
        const query = new URLSearchParams({ title });
        if (groupId) query.set('groupId', groupId);
        const response = await fetch(apiUrl(`/api/results/group/summary?${query.toString()}`), { cache: 'no-store' });
        if (!response.ok) return;
        const data = await response.json() as MangaSummary;
        if (cancelled) return;
        setSummaryAvailability({ title, state: getMangaSummaryAvailability(data) });
        if (isSummaryPending(data)) {
          setSummaryState((previous) => {
            if (previous?.title !== title) return previous;
            return {
              ...previous,
              data: { ...previous.data, ...data },
              loading: true,
            };
          });
          return;
        }
        setSummaryState((previous) => {
          if (previous?.title !== title || !previous.loading) return previous;
          return {
            title,
            data: data.summary ? data : previous.data,
            loading: false,
            error: data.jobStatus === 'error' ? data.jobError || 'Could not generate a summary.' : null,
          };
        });
      } catch {
        // Keep polling; transient network failures should not hide the modal.
      }
    };

    const interval = window.setInterval(() => void poll(), 2000);
    return () => {
      cancelled = true;
      window.clearInterval(interval);
    };
  }, [mangaGroups, summaryAvailability?.state, summaryAvailability?.title, summaryState?.data?.groupId, summaryState?.title]);

  const copySummary = async () => {
    const text = summaryState?.data?.summary;
    if (!text) return;
    try {
      if (navigator?.clipboard?.writeText) {
        await navigator.clipboard.writeText(text);
      } else {
        const textarea = document.createElement('textarea');
        textarea.value = text;
        document.body.appendChild(textarea);
        textarea.select();
        document.execCommand('copy');
        document.body.removeChild(textarea);
      }
      setSummaryCopied(true);
      window.setTimeout(() => setSummaryCopied(false), 1500);
    } catch {
      setSummaryState((previous) => previous ? { ...previous, error: 'Could not copy the synopsis.' } : previous);
    }
  };

  const handleViewSummary = useCallback(async (title: string) => {
    setSummaryCopied(false);
    setSummaryState({ title, data: null, loading: true, error: null });
    try {
      const groupId = mangaGroups.find((group) => group.title === title)?.id;
      const statusResponse = await fetch(apiUrl(`/api/results/group/summary?${groupId ? `groupId=${encodeURIComponent(groupId)}&` : ''}title=${encodeURIComponent(title)}`));
      const statusPayload = await statusResponse.json().catch(() => ({}));
      if (!statusResponse.ok) {
        throw new Error(statusPayload.detail || 'Could not load the manga summary.');
      }
      const data = statusPayload as MangaSummary;
      setSummaryState({ title, data, loading: false, error: null });
    } catch (error) {
      const errText = error instanceof Error ? error.message : 'Could not load the summary.';
      setSummaryState({ title, data: null, loading: false, error: errText });
    }
  }, [mangaGroups]);

  const handleToggleSelectAll = async (groupTitle: string, existingImages?: FinishedImage[]) => {
    let images = existingImages;
    if (!images || images.length === 0) {
      setExpandedGroups((prev) => ({ ...prev, [groupTitle]: true }));
      images = await loadMangaImagesIfNeeded(groupTitle);
    }
    if (images && images.length > 0) {
      toggleSelectAllInGroup(images);
    }
  };

  const handleDeleteImage = (image: FinishedImage) => {
    onDeleteImage?.(image);
    const manga = (image.mangaTitle || 'Ungrouped').trim() || 'Ungrouped';
    setMangaImages((prev) => {
      if (!prev[manga]) return prev;
      return {
        ...prev,
        [manga]: prev[manga].filter((img) => img.id !== image.id),
      };
    });
  };

  const handleDeleteMangaGroup = async (title: string, images: FinishedImage[]) => {
    const deletion = onDeleteManga?.(images, title);
    const groupId = mangaGroups.find((group) => group.title === title)?.id;
    if (groupId) {
      setSelectedMangaIds((previous) => {
        const next = new Map(previous);
        next.delete(groupId);
        return next;
      });
    }
    setMangaImages((prev) => {
      const next = { ...prev };
      delete next[title];
      return next;
    });
    setConfirmDeleteManga(null);
    if (activeMangaFilter === title) {
      await deletion;
      closeMangaDetail();
    }
  };

  const handleDeleteSelectedPages = async () => {
    if (selectedImageIds.size === 0) return;
    setIsDeletingSelectedPages(true);
    try {
      const idsToDelete = new Set(selectedImageIds);
      const allPossibleImages = [
        ...allLoadedImages,
        ...(currentSingleGroup?.images || []),
        ...(finishedImages || []),
      ];
      const seen = new Set<string>();
      const finalImagesToDelete: FinishedImage[] = [];
      for (const img of allPossibleImages) {
        if (idsToDelete.has(img.id) && !seen.has(img.id)) {
          seen.add(img.id);
          finalImagesToDelete.push(img);
        }
      }

      if (onDeleteImages) {
        await onDeleteImages(finalImagesToDelete);
      } else if (onDeleteImage) {
        await Promise.allSettled(
          finalImagesToDelete.map((img) => Promise.resolve(onDeleteImage(img)))
        );
      }

      setMangaImages((prev) => {
        const next = { ...prev };
        for (const groupTitle of Object.keys(next)) {
          next[groupTitle] = next[groupTitle].filter((img) => !idsToDelete.has(img.id));
        }
        return next;
      });

      setSelectedImageIds(new Set());
      lastSelectedGalleryIdRef.current = null;
      setConfirmDeleteSelectedPages(false);
    } finally {
      setIsDeletingSelectedPages(false);
    }
  };

  const handleDeleteSelectedMangas = async () => {
    if (selectedMangaIds.size === 0) return;
    setIsDeletingSelectedMangas(true);
    try {
      const selectedEntries = Array.from(selectedMangaIds.entries());
      const mangaListToDelete: Array<{ title: string; images: FinishedImage[] }> = [];

      for (const [groupId, title] of selectedEntries) {
        const targetGroup = mangaGroups.find((g) => g.id === groupId || g.title === title);
        mangaListToDelete.push({
          title,
          images: targetGroup?.images || mangaImages[title] || [],
        });
      }

      if (onDeleteMangas) {
        await onDeleteMangas(mangaListToDelete);
      } else if (onDeleteManga) {
        await Promise.allSettled(
          mangaListToDelete.map(({ title, images }) => Promise.resolve(onDeleteManga(images, title)))
        );
      }

      setSelectedMangaIds(new Map());
      setMangaImages((prev) => {
        const next = { ...prev };
        for (const [, title] of selectedEntries) {
          delete next[title];
        }
        return next;
      });
      setConfirmDeleteSelectedMangas(false);

      if (activeMangaFilter !== 'all' && selectedEntries.some(([, title]) => title === activeMangaFilter)) {
        closeMangaDetail();
      }
    } finally {
      setIsDeletingSelectedMangas(false);
    }
  };

  const openImageModal = (image: FinishedImage) => {
    setSelectedImage(image);
    setIsModalOpen(true);
    setZoomLevel(1);
    if (onOpenPageView && image.folder) {
      onOpenPageView(image.folder);
    }
  };

  const closeImageModal = () => {
    setIsModalOpen(false);
    setSelectedImage(null);
    setZoomLevel(1);
    if (onCloseOverlay) {
      onCloseOverlay();
    } else if (onCloseExternalModal) {
      onCloseExternalModal();
    }
  };

  // Sync route-owned initialPageViewFolder deep link
  useEffect(() => {
    if (!initialPageViewFolder) {
      if (isModalOpen && !selectedImageForModal) {
        setIsModalOpen(false);
        setSelectedImage(null);
      }
      return;
    }
    if (selectedImage?.folder === initialPageViewFolder && isModalOpen) return;

    const found = (finishedImages || []).find((img) => img.folder === initialPageViewFolder);
    if (found) {
      setSelectedImage(found);
      setIsModalOpen(true);
      return;
    }

    for (const imgs of Object.values(mangaImages)) {
      const match = imgs.find((img) => img.folder === initialPageViewFolder);
      if (match) {
        setSelectedImage(match);
        setIsModalOpen(true);
        return;
      }
    }

    let isCancelled = false;
    fetch(apiUrl(`/api/results/${encodeURIComponent(initialPageViewFolder)}`))
      .then((res) => (res.ok ? res.json() : null))
      .then((data) => {
        if (isCancelled || !data) return;
        const img: FinishedImage = {
          id: data.id || data.folder,
          groupId: data.groupId || null,
          originalName: (data.originalName && data.originalName !== 'Unknown') ? data.originalName : `${data.folder}.png`,
          pageOrder: data.pageOrder ?? null,
          sourcePath: data.sourcePath ?? null,
          result: data.resultUrl ? apiUrl(data.resultUrl) : apiUrl(`/result/${data.folder}/final.png`),
          sourceType: data.sourceType === 'original' ? 'original' : 'translated',
          inputUrl: data.inputUrl ? apiUrl(data.inputUrl) : apiUrl(`/result/${data.folder}/input.png`),
          inpaintedUrl: data.inpaintedUrl ? apiUrl(data.inpaintedUrl) : null,
          textRegionsUrl: data.textRegionsUrl ? apiUrl(data.textRegionsUrl) : null,
          bubbleMaskUrl: data.bubbleMaskUrl ? apiUrl(data.bubbleMaskUrl) : null,
          hasTextRegions: data.hasTextRegions ?? Boolean(data.textRegionsUrl),
          folder: data.folder,
          mangaTitle: data.mangaTitle || 'Ungrouped',
          finishedAt: data.finishedAt ? new Date(data.finishedAt) : new Date(),
          startedAt: data.startedAt ? new Date(data.startedAt) : null,
          durationMs: data.durationMs ?? null,
          settings: data.settings || {},
        };
        setSelectedImage(img);
        setIsModalOpen(true);
      })
      .catch((err) => console.error('Failed to load page for modal:', err));

    return () => {
      isCancelled = true;
    };
  }, [initialPageViewFolder, finishedImages, mangaImages]);

  // Sync route-owned initialPageEditFolder deep link
  useEffect(() => {
    if (!initialPageEditFolder) {
      if (editingImage) {
        setEditingImage(null);
      }
      return;
    }
    if (editingImage?.folder === initialPageEditFolder) return;

    const found = (finishedImages || []).find((img) => img.folder === initialPageEditFolder);
    if (found) {
      setEditingImage(found.sourceType === 'original' ? null : found);
      return;
    }

    for (const imgs of Object.values(mangaImages)) {
      const match = imgs.find((img) => img.folder === initialPageEditFolder);
      if (match) {
        setEditingImage(match.sourceType === 'original' ? null : match);
        return;
      }
    }

    let isCancelled = false;
    fetch(apiUrl(`/api/results/${encodeURIComponent(initialPageEditFolder)}`))
      .then((res) => (res.ok ? res.json() : null))
      .then((data) => {
        if (isCancelled || !data) return;
        const img: FinishedImage = {
          id: data.id || data.folder,
          groupId: data.groupId || null,
          originalName: (data.originalName && data.originalName !== 'Unknown') ? data.originalName : `${data.folder}.png`,
          pageOrder: data.pageOrder ?? null,
          sourcePath: data.sourcePath ?? null,
          result: data.resultUrl ? apiUrl(data.resultUrl) : apiUrl(`/result/${data.folder}/final.png`),
          sourceType: data.sourceType === 'original' ? 'original' : 'translated',
          inputUrl: data.inputUrl ? apiUrl(data.inputUrl) : apiUrl(`/result/${data.folder}/input.png`),
          inpaintedUrl: data.inpaintedUrl ? apiUrl(data.inpaintedUrl) : null,
          textRegionsUrl: data.textRegionsUrl ? apiUrl(data.textRegionsUrl) : null,
          bubbleMaskUrl: data.bubbleMaskUrl ? apiUrl(data.bubbleMaskUrl) : null,
          hasTextRegions: data.hasTextRegions ?? Boolean(data.textRegionsUrl),
          folder: data.folder,
          mangaTitle: data.mangaTitle || 'Ungrouped',
          finishedAt: data.finishedAt ? new Date(data.finishedAt) : new Date(),
          startedAt: data.startedAt ? new Date(data.startedAt) : null,
          durationMs: data.durationMs ?? null,
          settings: data.settings || {},
        };
        setEditingImage(img.sourceType === 'original' ? null : img);
      })
      .catch((err) => console.error('Failed to load page for edit:', err));

    return () => {
      isCancelled = true;
    };
  }, [initialPageEditFolder, finishedImages, mangaImages]);

  // Sync route-owned reader deep link
  useEffect(() => {
    const readerTitle = initialReaderMangaId
      ? mangaGroups.find((group) => group.id === initialReaderMangaId)?.title || initialReaderManga || ''
      : initialReaderManga;
    if (!initialReaderMangaId && !readerTitle) {
      if (readingManga) {
        setReadingManga(null);
      }
      return;
    }
    if (
      readingManga &&
      ((initialReaderMangaId && readingManga.groupId === initialReaderMangaId) ||
        (!initialReaderMangaId && readingManga.title === readerTitle))
    ) return;
    void handleReadManga(readerTitle || '', undefined, undefined, initialReaderMangaId || undefined, false);
  }, [initialReaderMangaId, initialReaderManga, mangaGroups, readingManga]);

  // Track previous readingManga to ensure exit position is captured even if closed via route change / back button
  const prevReadingMangaRef = useRef<typeof readingManga>(null);
  useEffect(() => {
    if (prevReadingMangaRef.current && !readingManga) {
      const exited = prevReadingMangaRef.current;
      const progress = getStoredMangaReadProgress(exited.title, exited.images.length);
      const targetIdx = progress.page ? progress.page - 1 : 0;
      const targetImg = exited.images[targetIdx];
      setLastExitedReadPosition((prev) => prev ?? {
        mangaTitle: exited.title,
        mangaId: exited.groupId,
        pageIndex: targetIdx,
        imageId: targetImg?.id,
      });
    }
    prevReadingMangaRef.current = readingManga;
  }, [readingManga]);

  const effectiveSingleMangaTitle = activeMangaFilter !== 'all' ? activeMangaFilter : null;
  const currentSingleGroup = effectiveSingleMangaTitle
    ? mangaGroups.find((g) => g.title === effectiveSingleMangaTitle) || null
    : null;

  useEffect(() => {
    setPageSort('order');
  }, [currentSingleGroup?.id]);

  useEffect(() => {
    const title = currentSingleGroup?.title;
    if (!title) {
      setSummaryAvailability(null);
      return;
    }
    let cancelled = false;
    setSummaryAvailability({ title, state: 'loading' });
    fetch(apiUrl(`/api/results/group/summary?${currentSingleGroup?.id ? `groupId=${encodeURIComponent(currentSingleGroup.id)}&` : ''}title=${encodeURIComponent(title)}`))
      .then(async (response) => {
        if (!response.ok) throw new Error(`Summary status failed (${response.status})`);
        return response.json();
      })
      .then((data: MangaSummary) => {
        if (!cancelled) {
          setSummaryAvailability({ title, state: getMangaSummaryAvailability(data) });
        }
      })
      .catch(() => {
        if (!cancelled) setSummaryAvailability({ title, state: 'unavailable' });
      });
    return () => {
      cancelled = true;
    };
  }, [currentSingleGroup?.id, currentSingleGroup?.title]);

  // Scroll to last read position when exiting reading mode
  useEffect(() => {
    if (!lastExitedReadPosition) return;
    const { mangaTitle, mangaId, pageIndex, imageId } = lastExitedReadPosition;

    let frameId: number | null = null;
    let timeoutId: ReturnType<typeof setTimeout> | null = null;

    const performScroll = () => {
      let cardEl: HTMLElement | null = null;
      // 1. Try finding individual page image card (for Manga Detail view or expanded row)
      const isSingleView = activeMangaFilter === mangaTitle;
      const isRowExpanded = viewMode === 'rows' && expandedGroups[mangaTitle];
      if (isSingleView || isRowExpanded) {
        if (imageId) {
          cardEl = document.querySelector<HTMLElement>(`[data-image-id="${imageId}"]`);
        }
        if (!cardEl && typeof pageIndex === 'number' && pageIndex >= 0) {
          cardEl = document.querySelector<HTMLElement>(`[data-page-index="${pageIndex}"]`);
        }
      }

      // 2. If not found or in gallery overview (grid or collapsed row), find the manga card or row
      if (!cardEl && mangaId) {
        cardEl = document.querySelector<HTMLElement>(`[data-manga-id="${mangaId}"]`);
      }
      if (!cardEl && mangaTitle) {
        cardEl = document.querySelector<HTMLElement>(`[data-manga-id="${mangaIdForTitle(mangaTitle)}"]`);
      }

      if (cardEl) {
        const prefersReducedMotion =
          typeof window !== 'undefined' &&
          window.matchMedia?.('(prefers-reduced-motion: reduce)')?.matches;

        cardEl.scrollIntoView({
          behavior: prefersReducedMotion ? 'auto' : 'smooth',
          block: 'center',
        });

        if (imageId && (isSingleView || isRowExpanded)) {
          setHighlightedImageId(imageId);
          timeoutId = setTimeout(() => {
            setHighlightedImageId((current) => (current === imageId ? null : current));
          }, 2000);
        } else if (mangaId) {
          setHighlightedMangaId(mangaId);
          timeoutId = setTimeout(() => {
            setHighlightedMangaId((current) => (current === mangaId ? null : current));
          }, 2000);
        }
        setLastExitedReadPosition(null);
      }
    };

    // Double RAF allows modal teardown and DOM reflow to finish cleanly
    frameId = requestAnimationFrame(() => {
      frameId = requestAnimationFrame(performScroll);
    });

    return () => {
      if (frameId) cancelAnimationFrame(frameId);
      if (timeoutId) clearTimeout(timeoutId);
    };
  }, [lastExitedReadPosition, activeMangaFilter, viewMode, expandedGroups, currentSingleGroup?.images]);

  const navigateImage = (direction: 'prev' | 'next') => {
    if (!selectedImage) return;

    const currentIndex = currentModalImages.findIndex((img) => img.id === selectedImage.id);
    if (currentIndex === -1) return;

    let newIndex: number;
    if (direction === 'prev') {
      newIndex = currentIndex === 0 ? currentModalImages.length - 1 : currentIndex - 1;
    } else {
      newIndex = currentIndex === currentModalImages.length - 1 ? 0 : currentIndex + 1;
    }

    setSelectedImage(currentModalImages[newIndex]);
    setZoomLevel(1);
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (!isModalOpen) return;

    switch (e.key) {
      case 'Escape':
        closeImageModal();
        break;
      case 'ArrowLeft':
        navigateImage('prev');
        break;
      case 'ArrowRight':
        navigateImage('next');
        break;
      case '+':
      case '=':
        setZoomLevel((prev) => Math.min(3, prev + 0.25));
        break;
      case '-':
        setZoomLevel((prev) => Math.max(0.5, prev - 0.25));
        break;
      case '0':
        setZoomLevel(1);
        break;
    }
  };

  const downloadSingle = async (img: FinishedImage) => {
    const isOriginal = img.sourceType === 'original';
    const source = isOriginal ? img.inputUrl : img.result;
    let url: string;
    let isBlob = false;
    if (source instanceof Blob) {
      if (!isOriginal && source.size < 1000 && img.folder) {
        url = apiUrl(`/result/${img.folder}/final.png`);
      } else {
        url = URL.createObjectURL(source);
        isBlob = true;
      }
    } else if (typeof source === 'string' && source) {
      try {
        const response = await fetch(apiUrl(source));
        if (!response.ok) throw new Error(`Download failed: ${response.status}`);
        url = URL.createObjectURL(await response.blob());
        isBlob = true;
      } catch (error) {
        console.error('Failed to download image:', error);
        return;
      }
    } else if (img.folder) {
      url = apiUrl(`/result/${img.folder}/${isOriginal ? 'input.png' : 'final.png'}`);
    } else {
      return;
    }
    const a = document.createElement('a');
    a.href = url;
    a.download = `${isOriginal ? 'original' : 'translated'}_${img.originalName}`;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    if (isBlob) {
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    }
  };

  // Download Manga as CBZ archive
  const handleDownloadCbz = async (mangaTitle: string, images: FinishedImage[]) => {
    setDownloadingCbz((prev) => ({ ...prev, [mangaTitle]: true }));
    const groupId = mangaGroups.find((group) => group.title === mangaTitle)?.id || images[0]?.groupId;
    try {
      // For named manga series, direct GET stream allows browser native download streaming
      // avoiding massive JS heap memory allocations and connection timeouts
      if (mangaTitle && mangaTitle !== 'Ungrouped') {
        const a = document.createElement('a');
        a.href = apiUrl(`/api/results/export/cbz?groupId=${encodeURIComponent(groupId || mangaTitle)}&manga=${encodeURIComponent(mangaTitle)}`);
        const safeTitle = mangaTitle.replace(/[^a-zA-Z0-9_\u4e00-\u9fa5\u3040-\u30ff\uac00-\ud7af.\-]/g, '_') || 'manga';
        a.download = `${safeTitle}.cbz`;
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        return;
      }

      const folders = images.map((img) => img.folder).filter(Boolean) as string[];
      const response = await fetch(apiUrl('/api/results/export/cbz'), {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ groupId: groupId || undefined, mangaTitle, folders }),
      });

      if (!response.ok) {
        throw new Error(`Export failed: ${response.statusText}`);
      }

      const blob = await response.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      const safeTitle = mangaTitle.replace(/[^a-zA-Z0-9_\u4e00-\u9fa5\u3040-\u30ff\uac00-\ud7af.\-]/g, '_') || 'manga';
      a.download = `${safeTitle}.cbz`;
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      setTimeout(() => URL.revokeObjectURL(url), 2000);
    } catch (error) {
      console.error('Failed to download CBZ:', error);
      alert('Could not download CBZ archive from server.');
    } finally {
      setTimeout(() => {
        setDownloadingCbz((prev) => ({ ...prev, [mangaTitle]: false }));
      }, 1500);
    }
  };

  const searchedMangaGroups = useMemo(() => {
    const q = mangaSearchQuery.trim().toLowerCase();
    if (!q) return mangaGroups;
    return mangaGroups.filter((g) => g.title.toLowerCase().includes(q));
  }, [mangaGroups, mangaSearchQuery]);

  const filteredGroups = useMemo(() => {
    const base = mangaSearchQuery.trim() && !onGallerySearchChange ? searchedMangaGroups : mangaGroups;
    const statusFiltered = statusFilter !== 'all'
      ? filterMangaGroupsByStatus(base, statusFilter)
      : base;
    return activeMangaFilter === 'all'
      ? statusFiltered
      : statusFiltered.filter((g) => g.title === activeMangaFilter);
  }, [mangaGroups, searchedMangaGroups, activeMangaFilter, mangaSearchQuery, statusFilter, onGallerySearchChange]);

  const galleryMangaCount = totalMangaCount > 0
    ? totalMangaCount
    : (activeMangaFilter !== 'all' || mangaSearchQuery.trim() || statusFilter !== 'all'
        ? filteredGroups.length
        : mangaGroups.length);
  const galleryPageCount = Math.max(1, Math.ceil(galleryMangaCount / requestedGalleryPageSize));
  const visibleGroups = filteredGroups;
  const reviewCount = reviewOnly
    ? totalImagesCount
    : activeSummaries.reduce((total, group) => total + (group.needsReviewCount || 0), 0);

  useEffect(() => {
    setGalleryPage(Math.max(1, requestedGalleryPage));
  }, [requestedGalleryPage]);

  useEffect(() => {
    if (activeMangaFilter !== 'all') return;
    const correctedPage = getGalleryPageCorrection(galleryPage, galleryPageCount, Boolean(effectiveIsLoading));
    if (correctedPage !== null) {
      setGalleryPage(correctedPage);
      onGalleryPageChange?.(correctedPage);
    }
  }, [activeMangaFilter, effectiveIsLoading, galleryPage, galleryPageCount, onGalleryPageChange]);

  const setGalleryPageAndRoute = (page: number) => {
    const nextPage = Math.min(Math.max(1, page), Math.max(1, galleryPageCount));
    setGalleryPage(nextPage);
    onGalleryPageChange?.(nextPage);
  };

  const setGalleryPageSizeAndRoute = (pageSize: number) => {
    setGalleryPage(1);
    onGalleryPageSizeChange?.(pageSize);
  };

  const allCollapsed = useMemo(() => {
    return visibleGroups.length > 0 && visibleGroups.every((g) => !expandedGroups[g.title]);
  }, [visibleGroups, expandedGroups]);

  const toggleAllCollapse = () => {
    const next: Record<string, boolean> = { ...expandedGroups };
    const targetExpanded = allCollapsed; // If currently all collapsed, expand all; otherwise collapse all
    visibleGroups.forEach((g) => {
      next[g.title] = targetExpanded;
      if (targetExpanded) {
        loadMangaImagesIfNeeded(g.title);
      }
    });
    setExpandedGroups(next);
  };

  // Stable memoized callbacks for GalleryCard, MangaCard, and RowGroupCards
  const handleCardClick = useCallback((image: FinishedImage) => {
    openImageModal(image);
  }, [onOpenPageView]);

  const handleCardDownload = useCallback((image: FinishedImage) => {
    void downloadSingle(image);
  }, []);

  const handleCardDelete = useCallback((image: FinishedImage) => {
    handleDeleteImage(image);
  }, [onDeleteImage]);

  const handleCardEdit = useCallback((image: FinishedImage) => {
    if (image.hasTextRegions && image.sourceType !== 'original') {
      if (onOpenPageEdit && image.folder) {
        onOpenPageEdit(image.folder);
      } else {
        setEditingImage(image);
      }
    }
  }, [onOpenPageEdit]);

  const handleCardMove = useCallback((image: FinishedImage) => {
    setSingleImageToMove(image);
    setIsMoveModalOpen(true);
  }, []);

  const handleSingleGroupToggleSelect = useCallback((id: string, shiftKey = false) => {
    toggleSelectImage(id, currentSingleGroup?.images || [], shiftKey);
  }, [toggleSelectImage, currentSingleGroup?.images]);

  const handleSingleGroupReadFromHere = useCallback((pageIndex: number) => {
    if (currentSingleGroup) {
      void handleReadManga(currentSingleGroup.title, currentSingleGroup.images, pageIndex);
    }
  }, [currentSingleGroup, handleReadManga]);

  const handleRowGroupReadFromHere = useCallback((title: string, images: FinishedImage[], pageIndex: number) => {
    void handleReadManga(title, images, pageIndex);
  }, [handleReadManga]);

  const handleMangaCardOpenDetails = useCallback((title: string, reviewOnlyOverride = false) => {
    openMangaDetail(title, reviewOnlyOverride);
  }, [mangaGroups, onOpenMangaDetail]);

  const handleMangaCardRead = useCallback((title: string, images?: FinishedImage[]) => {
    void handleReadManga(title, images);
  }, [handleReadManga]);

  const handleMangaCardSummarize = useCallback((title: string) => {
    void handleSummarize(title);
  }, [summaryModel]);

  const handleMangaCardViewSummary = useCallback((title: string) => {
    void handleViewSummary(title);
  }, [handleViewSummary]);

  const handleMangaCardDownloadCbz = useCallback((title: string, images?: FinishedImage[]) => {
    void handleDownloadCbz(title, images || []);
  }, []);

  const handleMangaCardStartRename = useCallback((title: string) => {
    handleStartRename(title);
  }, []);

  const handleMangaCardDelete = useCallback((title: string) => {
    setConfirmDeleteManga(title);
  }, []);

  const handleMangaCardToggleSelect = useCallback((groupId: string) => {
    toggleMangaSeriesSelection(groupId);
  }, [mangaGroups]);

  const handlePageDrop = useCallback(async (
    group: { id: string; title: string; images: FinishedImage[] },
    sourceId: string,
    targetId: string,
  ) => {
    if (!onReorderMangaPages || !sourceId || sourceId === targetId) return;
    const sourceIndex = group.images.findIndex((image) => image.id === sourceId);
    const targetIndex = group.images.findIndex((image) => image.id === targetId);
    if (sourceIndex < 0 || targetIndex < 0) return;

    const previousImages = group.images;
    const reordered = [...group.images];
    const [moved] = reordered.splice(sourceIndex, 1);
    reordered.splice(targetIndex, 0, moved);
    const optimistic = reordered.map((image, index) => ({ ...image, pageOrder: index + 1 }));
    setMangaImages((previous) => ({ ...previous, [group.title]: optimistic }));
    setPageOrderError(null);
    setReorderingGroupId(group.id);
    try {
      await onReorderMangaPages(group.id, optimistic.map((image) => image.id));
    } catch (error) {
      setMangaImages((previous) => ({ ...previous, [group.title]: previousImages }));
      setPageOrderError(error instanceof Error ? error.message : 'Could not save page order.');
    } finally {
      setReorderingGroupId(null);
    }
  }, [onReorderMangaPages]);

  const handlePageSort = useCallback((sortMode: PageSortOption) => {
    setPageSort(sortMode);
    setPageOrderError(null);
  }, []);

  const handleSavePageSort = useCallback(async () => {
    if (!currentSingleGroup || !onReorderMangaPages || pageSort === 'order') return;

    const previousImages = currentSingleGroup.images;
    const sorted = sortMangaPagesForOrder(previousImages, pageSort);
    if (sorted.every((image, index) => image.id === previousImages[index]?.id)) {
      setPageSort('order');
      return;
    }

    const optimistic = sorted.map((image, index) => ({ ...image, pageOrder: index + 1 }));
    setMangaImages((previous) => ({ ...previous, [currentSingleGroup.title]: optimistic }));
    setPageOrderError(null);
    setReorderingGroupId(currentSingleGroup.id);
    try {
      await onReorderMangaPages(currentSingleGroup.id, optimistic.map((image) => image.id));
      setPageSort('order');
    } catch (error) {
      setMangaImages((previous) => ({ ...previous, [currentSingleGroup.title]: previousImages }));
      setPageOrderError(error instanceof Error ? error.message : 'Could not save page order.');
    } finally {
      setReorderingGroupId(null);
    }
  }, [currentSingleGroup, onReorderMangaPages, pageSort]);

  const duplicateSinglePageNames = useMemo(() => {
    if (!currentSingleGroup) return new Set<string>();
    const counts = new Map<string, number>();
    currentSingleGroup.images.forEach((image) => {
      const key = image.originalName.toLocaleLowerCase();
      counts.set(key, (counts.get(key) || 0) + 1);
    });
    return new Set([...counts].filter(([, count]) => count > 1).map(([name]) => name));
  }, [currentSingleGroup]);

  const displayedPageImages = useMemo(() => {
    if (!currentSingleGroup) return [];
    return sortMangaPagesForOrder(currentSingleGroup.images, pageSort);
  }, [currentSingleGroup, pageSort]);

  const pageSortIsDirty = Boolean(
    currentSingleGroup &&
    pageSort !== 'order' &&
    displayedPageImages.some((image, index) => image.id !== currentSingleGroup.images[index]?.id),
  );

  const canReorderCurrentGroup = Boolean(onReorderMangaPages && !reviewOnly);

  if (gallerySection === 'series') {
    return (
      <SeriesLibrary
        seriesId={initialSeriesId}
        onOpenSeriesDetail={onOpenSeriesDetail || (() => {})}
        onCloseSeriesDetail={onCloseSeriesDetail || (() => {})}
        onOpenMangaDetail={onOpenMangaDetail}
        onOpenReader={onOpenReader}
        onSeriesChanged={onSeriesChanged}
      />
    );
  }

  if (effectiveIsLoading && activeSummaries.length === 0) {
    return (
      <div className="space-y-6 animate-pulse" aria-busy="true" aria-label="Loading manga gallery">
        {/* Top Gallery Header Skeleton */}
        <div className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-zinc-200 bg-white p-3 shadow-xs dark:border-zinc-800 dark:bg-zinc-900 sm:p-4">
          <div className="flex min-w-0 flex-wrap items-center gap-2 sm:gap-3">
            <div className="flex items-center space-x-2">
              <Icon icon="carbon:book" className="w-5 h-5 text-indigo-600/70 dark:text-indigo-400/70" />
              <h3 className="text-base font-semibold text-zinc-900 dark:text-zinc-100">
                Manga Gallery
              </h3>
            </div>
            <div className="flex items-center space-x-1.5">
              <span className="flex items-center space-x-1.5 rounded-full bg-indigo-50 dark:bg-indigo-950/60 border border-indigo-200/60 dark:border-indigo-800/60 px-2.5 py-0.5 text-xs font-medium text-indigo-700 dark:text-indigo-300">
                <Icon icon="carbon:renew" className="w-3 h-3 animate-spin text-indigo-600 dark:text-indigo-400" />
                <span>Loading library...</span>
              </span>
            </div>
          </div>

          {/* Skeletons for search & sort controls */}
          <div className="flex w-full flex-wrap items-center justify-end gap-2 sm:w-auto">
            <div className="h-7 w-36 sm:w-48 rounded-lg bg-zinc-100 dark:bg-zinc-800" />
            <div className="h-7 w-32 rounded-lg bg-zinc-100 dark:bg-zinc-800" />
          </div>
        </div>

        {/* Skeleton Grid */}
        {viewMode === 'cards' ? (
          <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-5 gap-4 sm:gap-5">
            {Array.from({ length: 12 }).map((_, i) => (
              <div
                key={i}
                className="flex flex-col rounded-2xl border border-zinc-200 dark:border-zinc-800 bg-white dark:bg-zinc-900 overflow-hidden shadow-xs"
              >
                <div className="relative aspect-[3/4] w-full bg-zinc-100 dark:bg-zinc-950 flex flex-col items-center justify-center p-4">
                  <div className="w-12 h-12 rounded-xl bg-zinc-200/80 dark:bg-zinc-800/60 flex items-center justify-center mb-2 shadow-inner">
                    <Icon icon="carbon:book" className="w-6 h-6 text-zinc-300 dark:text-zinc-700" />
                  </div>
                  <div className="absolute top-2.5 left-2.5 h-4 w-14 rounded-full bg-zinc-200 dark:bg-zinc-800" />
                </div>
                <div className="p-3 space-y-2">
                  <div className="h-3.5 bg-zinc-200 dark:bg-zinc-700/60 rounded-md w-3/4" />
                  <div className="h-3 bg-zinc-100 dark:bg-zinc-800/80 rounded-md w-2/5" />
                </div>
              </div>
            ))}
          </div>
        ) : (
          <div className="space-y-4">
            {Array.from({ length: 5 }).map((_, i) => (
              <div
                key={i}
                className="flex items-center justify-between p-4 rounded-2xl border border-zinc-200 dark:border-zinc-800 bg-white dark:bg-zinc-900 shadow-xs"
              >
                <div className="flex items-center space-x-3">
                  <div className="w-12 h-16 rounded-xl bg-zinc-100 dark:bg-zinc-800 flex items-center justify-center">
                    <Icon icon="carbon:book" className="w-5 h-5 text-zinc-300 dark:text-zinc-700" />
                  </div>
                  <div className="space-y-2">
                    <div className="h-4 w-40 rounded bg-zinc-200 dark:bg-zinc-700/60" />
                    <div className="h-3 w-20 rounded bg-zinc-100 dark:bg-zinc-800" />
                  </div>
                </div>
                <div className="h-7 w-24 rounded-lg bg-zinc-100 dark:bg-zinc-800" />
              </div>
            ))}
          </div>
        )}
      </div>
    );
  }

  if (reviewOnly && mangaGroups.length === 0 && totalImagesCount === 0 && !effectiveIsLoading) {
    return (
      <div className="flex flex-col items-center justify-center rounded-2xl border border-emerald-800/70 bg-emerald-950/20 px-6 py-16 text-center text-emerald-100">
        <Icon icon="carbon:checkmark-filled" className="mb-3 h-10 w-10 text-emerald-400" />
        <p className="text-base font-semibold">Review queue is clear</p>
        <p className="mt-1 max-w-md text-xs leading-5 text-emerald-200/75">
          Every flagged page is approved. You can return to the full gallery whenever you’re ready.
        </p>
        <button
          type="button"
          onClick={() => onGalleryReviewChange?.(false)}
          className="mt-4 rounded-lg bg-emerald-600 px-3.5 py-2 text-xs font-semibold text-white transition-colors hover:bg-emerald-500 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-emerald-400"
        >
          Back to gallery
        </button>
      </div>
    );
  }

  if (
    shouldShowEmptyLibraryState({
      mangaGroupsLength: mangaGroups.length,
      totalImagesCount,
      mangaSearchQuery,
      statusFilter,
      activeMangaFilter,
      reviewOnly,
    })
  ) {
    return (
      <div className="text-center py-16 rounded-2xl border border-dashed border-zinc-200 dark:border-zinc-800 text-zinc-400">
        <Icon icon="carbon:image" className="w-12 h-12 mx-auto mb-3 text-zinc-300 dark:text-zinc-700" />
        <p className="text-base font-semibold text-zinc-700 dark:text-zinc-300">
          No manga yet
        </p>
        <p className="text-xs text-zinc-500 mt-1">
          Upload manga pages from Studio to create a manga.
        </p>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      {/* Top Gallery Header */}
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
                const nextSort = e.target.value as SortOption;
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

          {/* Clear Gallery button */}
          <button
            type="button"
            onClick={() => {
              setMangaImages({});
              setSelectedImageIds(new Set());
              setSelectedMangaIds(new Map());
              lastSelectedGalleryIdRef.current = null;
              onClearGallery();
            }}
            className="flex items-center space-x-1 rounded-lg px-2.5 py-1.5 text-xs font-medium text-zinc-500 dark:text-zinc-400 hover:text-red-600 dark:hover:text-red-400 hover:bg-zinc-100 dark:hover:bg-zinc-800 transition-colors cursor-pointer"
          >
            <Icon icon="carbon:trash-can" className="h-4 w-4" />
            <span>Clear Gallery</span>
          </button>
        </div>
      </div>

      {isCreateSeriesOpen && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-2 sm:p-4 backdrop-blur-xs" role="dialog" aria-modal="true" aria-labelledby="series-modal-title">
          <div className="w-full max-w-xl rounded-2xl sm:rounded-3xl bg-white p-3.5 sm:p-5 shadow-2xl dark:bg-zinc-900 border border-zinc-200 dark:border-zinc-800 flex flex-col max-h-[92vh] sm:max-h-[90vh]">
            <div className="shrink-0">
              <div className="flex items-center justify-between gap-2">
                <h2 id="series-modal-title" className="text-base sm:text-lg font-semibold text-zinc-900 dark:text-zinc-100">
                  {seriesModalMode === 'create' ? 'Create New Series' : 'Add to Existing Series'}
                </h2>
                <button
                  type="button"
                  onClick={() => setIsCreateSeriesOpen(false)}
                  className="rounded-lg p-1 text-zinc-400 hover:bg-zinc-100 hover:text-zinc-600 dark:hover:bg-zinc-800 dark:hover:text-zinc-200 cursor-pointer"
                  aria-label="Close dialog"
                >
                  <Icon icon="carbon:close" className="h-5 w-5" />
                </button>
              </div>

              {/* Mode Segmented Tab Switcher */}
              <div className="mt-2.5 flex rounded-xl bg-zinc-100 p-1 dark:bg-zinc-800/80">
                <button
                  type="button"
                  onClick={() => {
                    setSeriesModalMode('create');
                    setSeriesError(null);
                  }}
                  className={`flex flex-1 items-center justify-center gap-1.5 rounded-lg py-1.5 text-xs font-semibold transition-all cursor-pointer ${
                    seriesModalMode === 'create'
                      ? 'bg-white text-zinc-900 shadow-xs dark:bg-zinc-700 dark:text-zinc-100'
                      : 'text-zinc-600 hover:text-zinc-900 dark:text-zinc-400 dark:hover:text-zinc-200'
                  }`}
                >
                  <Icon icon="carbon:folder-add" className="h-3.5 w-3.5 sm:h-4 sm:w-4" />
                  <span>Create New Series</span>
                </button>
                <button
                  type="button"
                  onClick={() => {
                    setSeriesModalMode('add');
                    setSeriesError(null);
                    void loadGalleryAllSeries();
                  }}
                  className={`flex flex-1 items-center justify-center gap-1.5 rounded-lg py-1.5 text-xs font-semibold transition-all cursor-pointer ${
                    seriesModalMode === 'add'
                      ? 'bg-white text-zinc-900 shadow-xs dark:bg-zinc-700 dark:text-zinc-100'
                      : 'text-zinc-600 hover:text-zinc-900 dark:text-zinc-400 dark:hover:text-zinc-200'
                  }`}
                >
                  <Icon icon="carbon:catalog" className="h-3.5 w-3.5 sm:h-4 sm:w-4" />
                  <span>Add to Existing Series</span>
                </button>
              </div>
            </div>

            {seriesModalMode === 'create' ? (
              <div className="flex min-w-0 flex-1 flex-col overflow-hidden pt-2">
                <p className="text-xs text-zinc-500 dark:text-zinc-400">
                  Arrange the manga in your preferred series order. Drag items or use the up/down buttons to reorder.
                </p>

                <label className="mt-3 block text-xs font-semibold text-zinc-600 dark:text-zinc-300">
                  Series title
                  <input
                    autoFocus
                    value={newSeriesTitle}
                    onChange={(event) => setNewSeriesTitle(event.target.value)}
                    maxLength={MANGA_TITLE_MAX_LENGTH}
                    onKeyDown={(event) => { if (event.key === 'Enter') void handleCreateSeries(); }}
                    placeholder="e.g. My Favorite Manga"
                    className="mt-1 block w-full rounded-lg border border-zinc-200 bg-zinc-50 px-3 py-2 text-sm text-zinc-900 dark:border-zinc-700 dark:bg-zinc-800 dark:text-zinc-100 focus:outline-hidden focus:ring-2 focus:ring-indigo-500/50"
                  />
                </label>

                {selectedMangaIds.size < 2 && (
                  <div className="mt-2.5 rounded-xl border border-amber-200 bg-amber-50/80 p-2.5 text-xs text-amber-800 dark:border-amber-900/50 dark:bg-amber-950/30 dark:text-amber-300">
                    <p className="font-semibold">Need at least 2 manga to create a new series.</p>
                    <p className="mt-0.5 text-[11px] opacity-90">
                      Currently only {selectedMangaIds.size} manga is selected. You can switch to the <strong>“Add to Existing Series”</strong> tab to add this manga to an existing series, or select more manga in the gallery.
                    </p>
                  </div>
                )}

                {/* Ordered list of selected manga with full names and sorting */}
                <div className="mt-3 flex min-w-0 flex-1 flex-col overflow-hidden">
                  <div className="flex flex-wrap items-center justify-between gap-1.5 sm:gap-2 mb-2 shrink-0">
                    <span className="text-xs font-semibold text-zinc-700 dark:text-zinc-300">
                      Manga Order ({selectedMangaIds.size})
                    </span>
                    <div className="flex items-center gap-1">
                      <button
                        type="button"
                        onClick={() => sortSelectedManga('natural')}
                        className="flex items-center gap-1 rounded-md px-2 py-1 text-[11px] font-medium text-zinc-600 hover:bg-zinc-100 hover:text-indigo-600 dark:text-zinc-400 dark:hover:bg-zinc-800 dark:hover:text-indigo-400 border border-zinc-200 dark:border-zinc-700 cursor-pointer transition-colors touch-manipulation"
                        title="Sort naturally by title (e.g. Vol 1 before Vol 2)"
                      >
                        <Icon icon="carbon:sort-ascending" className="h-3.5 w-3.5" />
                        <span>A → Z</span>
                      </button>
                      <button
                        type="button"
                        onClick={() => sortSelectedManga('alpha-desc')}
                        className="flex items-center gap-1 rounded-md px-2 py-1 text-[11px] font-medium text-zinc-600 hover:bg-zinc-100 hover:text-indigo-600 dark:text-zinc-400 dark:hover:bg-zinc-800 dark:hover:text-indigo-400 border border-zinc-200 dark:border-zinc-700 cursor-pointer transition-colors touch-manipulation"
                        title="Sort in reverse alphabetical order"
                      >
                        <Icon icon="carbon:sort-descending" className="h-3.5 w-3.5" />
                        <span>Z → A</span>
                      </button>
                      <button
                        type="button"
                        onClick={() => sortSelectedManga('reverse')}
                        className="flex items-center gap-1 rounded-md px-2 py-1 text-[11px] font-medium text-zinc-600 hover:bg-zinc-100 hover:text-indigo-600 dark:text-zinc-400 dark:hover:bg-zinc-800 dark:hover:text-indigo-400 border border-zinc-200 dark:border-zinc-700 cursor-pointer transition-colors touch-manipulation"
                        title="Reverse current order"
                      >
                        <Icon icon="carbon:arrows-vertical" className="h-3.5 w-3.5" />
                        <span>Reverse</span>
                      </button>
                    </div>
                  </div>

                  <div className="flex-1 overflow-y-auto max-h-56 sm:max-h-60 rounded-xl border border-zinc-200 bg-zinc-50/50 p-1.5 sm:p-2 space-y-1.5 dark:border-zinc-800 dark:bg-zinc-950/40 overscroll-contain">
                    {Array.from(selectedMangaIds.entries()).map(([id, title], index, arr) => (
                      <div
                        key={id}
                        draggable={!isCreatingSeries}
                        onDragStart={(event) => {
                          setCreateSeriesDraggedId(id);
                          event.dataTransfer.effectAllowed = 'move';
                          event.dataTransfer.setData('text/plain', id);
                        }}
                        onDragOver={(event) => {
                          event.preventDefault();
                          event.dataTransfer.dropEffect = 'move';
                          setCreateSeriesDragOverId(id);
                        }}
                        onDrop={(event) => {
                          event.preventDefault();
                          dropSelectedManga(event.dataTransfer.getData('text/plain') || createSeriesDraggedId || '', id);
                        }}
                        onDragEnd={() => {
                          setCreateSeriesDraggedId(null);
                          setCreateSeriesDragOverId(null);
                        }}
                        className={`flex items-center gap-2 sm:gap-2.5 rounded-xl border bg-white p-2 sm:p-2.5 transition-all dark:bg-zinc-900 ${
                          createSeriesDragOverId === id
                            ? 'border-indigo-500 ring-2 ring-indigo-200 dark:ring-indigo-900/60'
                            : 'border-zinc-200/80 dark:border-zinc-800 shadow-2xs'
                        } ${createSeriesDraggedId === id ? 'opacity-40' : ''}`}
                      >
                        <span
                          className="cursor-grab select-none text-base leading-none text-zinc-400 active:cursor-grabbing hover:text-zinc-600 dark:hover:text-zinc-200 shrink-0 touch-none"
                          title="Drag to reorder"
                          aria-hidden="true"
                        >
                          ⋮⋮
                        </span>
                        <span className="flex h-5 w-5 shrink-0 items-center justify-center rounded-md bg-zinc-100 text-[11px] font-bold text-zinc-500 dark:bg-zinc-800 dark:text-zinc-400">
                          {index + 1}
                        </span>

                        {/* FULL NAME DISPLAY (Unclipped, wraps naturally) */}
                        <div className="min-w-0 flex-1">
                          <span className="block text-xs font-semibold text-zinc-900 dark:text-zinc-100 break-words leading-snug">
                            {title}
                          </span>
                        </div>

                        <div className="flex items-center gap-0.5 sm:gap-1 shrink-0">
                          <button
                            type="button"
                            onClick={() => moveSelectedManga(index, -1)}
                            disabled={index === 0 || isCreatingSeries}
                            className="rounded-lg p-1.5 text-zinc-500 hover:bg-zinc-100 hover:text-zinc-800 disabled:opacity-30 dark:text-zinc-400 dark:hover:bg-zinc-800 dark:hover:text-zinc-100 cursor-pointer disabled:cursor-not-allowed touch-manipulation min-w-[28px] min-h-[28px] flex items-center justify-center"
                            aria-label={`Move ${title} up`}
                            title="Move up"
                          >
                            <Icon icon="carbon:chevron-up" className="h-4 w-4" />
                          </button>
                          <button
                            type="button"
                            onClick={() => moveSelectedManga(index, 1)}
                            disabled={index === arr.length - 1 || isCreatingSeries}
                            className="rounded-lg p-1.5 text-zinc-500 hover:bg-zinc-100 hover:text-zinc-800 disabled:opacity-30 dark:text-zinc-400 dark:hover:bg-zinc-800 dark:hover:text-zinc-100 cursor-pointer disabled:cursor-not-allowed touch-manipulation min-w-[28px] min-h-[28px] flex items-center justify-center"
                            aria-label={`Move ${title} down`}
                            title="Move down"
                          >
                            <Icon icon="carbon:chevron-down" className="h-4 w-4" />
                          </button>
                          <button
                            type="button"
                            onClick={() => toggleMangaSeriesSelection(id)}
                            disabled={isCreatingSeries}
                            className="rounded-lg p-1.5 text-zinc-400 hover:bg-red-50 hover:text-red-600 dark:hover:bg-red-950/40 dark:hover:text-red-400 cursor-pointer touch-manipulation min-w-[28px] min-h-[28px] flex items-center justify-center"
                            title={`Remove ${title}`}
                          >
                            <Icon icon="carbon:close" className="h-4 w-4" />
                          </button>
                        </div>
                      </div>
                    ))}
                  </div>
                </div>
              </div>
            ) : (
              <div className="flex min-w-0 flex-1 flex-col overflow-hidden pt-2">
                {/* Manga Selected Preview Chips */}
                <div className="mb-3 shrink-0">
                  <div className="flex items-center justify-between text-xs font-semibold text-zinc-700 dark:text-zinc-300 mb-1.5">
                    <span>Selected manga to add ({selectedMangaIds.size})</span>
                  </div>
                  <div className="flex flex-wrap gap-1.5 max-h-16 overflow-y-auto p-1.5 rounded-lg bg-zinc-50 border border-zinc-200/70 dark:bg-zinc-800/50 dark:border-zinc-700/60">
                    {Array.from(selectedMangaIds.entries()).map(([id, title]) => (
                      <span
                        key={id}
                        className="inline-flex items-center gap-1 rounded-md bg-indigo-50 px-2 py-0.5 text-[11px] font-medium text-indigo-700 dark:bg-indigo-950/60 dark:text-indigo-300 max-w-full truncate border border-indigo-200/50 dark:border-indigo-800/40"
                        title={title}
                      >
                        <span className="truncate">{title}</span>
                        <button
                          type="button"
                          onClick={() => toggleMangaSeriesSelection(id)}
                          className="text-indigo-400 hover:text-indigo-700 dark:hover:text-indigo-200 ml-0.5 cursor-pointer"
                          title={`Remove ${title}`}
                        >
                          ×
                        </button>
                      </span>
                    ))}
                  </div>
                </div>

                {/* Search Input */}
                <div className="relative shrink-0 mb-2.5">
                  <Icon
                    icon="carbon:search"
                    className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-zinc-400 h-4 w-4"
                  />
                  <input
                    autoFocus
                    type="text"
                    value={existingSeriesSearch}
                    onChange={(e) => setExistingSeriesSearch(e.target.value)}
                    placeholder="Search series by name..."
                    className="w-full rounded-xl border border-zinc-200 bg-zinc-50 py-2 pl-9 pr-8 text-xs text-zinc-900 focus:border-indigo-500 focus:bg-white focus:outline-hidden dark:border-zinc-700 dark:bg-zinc-800 dark:text-zinc-100 dark:focus:bg-zinc-900"
                  />
                  {existingSeriesSearch && (
                    <button
                      type="button"
                      onClick={() => setExistingSeriesSearch('')}
                      className="absolute right-2.5 top-1/2 -translate-y-1/2 text-zinc-400 hover:text-zinc-600 dark:hover:text-zinc-200 cursor-pointer"
                    >
                      <Icon icon="carbon:close-filled" className="h-3.5 w-3.5" />
                    </button>
                  )}
                </div>

                {/* Scrollable Series Selection List */}
                <div className="flex-1 overflow-y-auto max-h-56 sm:max-h-60 rounded-xl border border-zinc-200 bg-zinc-50/50 p-1.5 sm:p-2 space-y-1.5 dark:border-zinc-800 dark:bg-zinc-950/40 overscroll-contain">
                  {isLoadingExistingSeries ? (
                    <div className="py-8 text-center text-xs text-zinc-500">
                      <Icon icon="carbon:renew" className="mx-auto mb-2 h-5 w-5 animate-spin text-indigo-500" />
                      Loading series...
                    </div>
                  ) : filteredExistingSeries.length === 0 ? (
                    <div className="py-8 text-center text-xs text-zinc-500">
                      <Icon icon="carbon:catalog" className="mx-auto mb-2 h-7 w-7 text-zinc-400 opacity-60" />
                      <p className="font-medium text-zinc-700 dark:text-zinc-300">
                        {existingSeriesSearch ? 'No matching series found' : 'No existing series found'}
                      </p>
                      <p className="mt-1 text-zinc-400">Switch to the “Create New Series” tab to make a new series.</p>
                    </div>
                  ) : (
                    filteredExistingSeries.map((s) => {
                      const isSelected = targetExistingSeriesId === s.id;
                      const coverVal = s.cover?.coverUrl || s.cover?.thumbnailUrl || (typeof s.cover?.result === 'string' ? s.cover.result : null);
                      const thumb = coverVal ? apiUrl(coverVal) : null;
                      return (
                        <label
                          key={s.id}
                          onClick={() => setTargetExistingSeriesId(s.id)}
                          className={`flex items-center justify-between gap-3 rounded-xl border p-2.5 transition-all cursor-pointer ${
                            isSelected
                              ? 'border-indigo-500 ring-2 ring-indigo-200 bg-indigo-50/50 dark:border-indigo-500 dark:ring-indigo-900/60 dark:bg-indigo-950/30 shadow-2xs'
                              : 'border-zinc-200 bg-white hover:border-indigo-300 hover:bg-zinc-50/80 dark:border-zinc-800 dark:bg-zinc-900 dark:hover:border-indigo-800 dark:hover:bg-zinc-800/80'
                          }`}
                        >
                          <div className="flex items-center gap-2.5 min-w-0 flex-1">
                            <input
                              type="radio"
                              name="targetExistingSeries"
                              checked={isSelected}
                              onChange={() => setTargetExistingSeriesId(s.id)}
                              className="h-4 w-4 text-indigo-600 focus:ring-indigo-500 border-zinc-300 dark:border-zinc-700 shrink-0"
                            />
                            {thumb ? (
                              <img
                                src={thumb}
                                alt=""
                                className="h-10 w-8 shrink-0 rounded-md object-cover border border-zinc-200/60 dark:border-zinc-800"
                              />
                            ) : (
                              <div className="flex h-10 w-8 shrink-0 items-center justify-center rounded-md bg-zinc-100 text-zinc-400 dark:bg-zinc-800">
                                <Icon icon="carbon:book" className="h-3.5 w-3.5" />
                              </div>
                            )}
                            <div className="min-w-0 flex-1">
                              <p className="truncate text-xs font-semibold text-zinc-900 dark:text-zinc-100">
                                {s.title}
                              </p>
                              <p className="mt-0.5 text-[11px] text-zinc-500 dark:text-zinc-400">
                                {s.memberCount} {s.memberCount === 1 ? 'manga' : 'manga'}
                              </p>
                            </div>
                          </div>
                        </label>
                      );
                    })
                  )}
                </div>
              </div>
            )}

            <div className="shrink-0 pt-3">
              {/* Checkbox for direct navigation option */}
              <label className="flex items-center gap-2 text-xs text-zinc-600 dark:text-zinc-400 cursor-pointer select-none">
                <input
                  type="checkbox"
                  checked={openSeriesAfterCreate}
                  onChange={(e) => setOpenSeriesAfterCreate(e.target.checked)}
                  className="h-4 w-4 rounded border-zinc-300 text-indigo-600 focus:ring-indigo-500 dark:border-zinc-700 dark:bg-zinc-800"
                />
                <span>Open series detail view immediately</span>
              </label>

              {seriesError && <p role="alert" className="mt-2.5 text-xs text-red-600 dark:text-red-400">{seriesError}</p>}
              <div className="mt-4 sm:mt-5 flex items-center justify-end gap-2">
                <button
                  type="button"
                  onClick={() => setIsCreateSeriesOpen(false)}
                  disabled={isCreatingSeries}
                  className="flex-1 sm:flex-initial rounded-xl border border-zinc-200 px-3.5 py-2 text-xs font-semibold text-zinc-700 hover:bg-zinc-50 dark:border-zinc-700 dark:text-zinc-300 dark:hover:bg-zinc-800 transition-colors cursor-pointer touch-manipulation text-center"
                >
                  Cancel
                </button>
                {seriesModalMode === 'create' ? (
                  <button
                    type="button"
                    onClick={() => void handleCreateSeries()}
                    disabled={isCreatingSeries || selectedMangaIds.size < 2 || !newSeriesTitle.trim()}
                    className="flex-1 sm:flex-initial rounded-xl bg-indigo-600 px-4 py-2 text-xs font-semibold text-white hover:bg-indigo-500 disabled:opacity-50 transition-colors cursor-pointer touch-manipulation text-center"
                  >
                    {isCreatingSeries ? 'Creating…' : 'Create series'}
                  </button>
                ) : (
                  <button
                    type="button"
                    onClick={() => void handleAddToExistingSeries()}
                    disabled={isCreatingSeries || selectedMangaIds.size === 0 || !targetExistingSeriesId}
                    className="flex-1 sm:flex-initial rounded-xl bg-indigo-600 px-4 py-2 text-xs font-semibold text-white hover:bg-indigo-500 disabled:opacity-50 transition-colors cursor-pointer touch-manipulation text-center"
                  >
                    {isCreatingSeries ? 'Adding…' : 'Add to series'}
                  </button>
                )}
              </div>
            </div>
          </div>
        </div>
      )}

      {/* Single Manga Detail View */}
      {currentSingleGroup && (
        <div className="space-y-6">
          {/* Manga Header Banner */}
          <div className="rounded-2xl border border-zinc-200 bg-white p-4 shadow-xs dark:border-zinc-800 dark:bg-zinc-900/70">
            <div className="flex min-w-0 flex-wrap items-start gap-4">
              <button
                type="button"
                onClick={closeMangaDetail}
                className="inline-flex h-10 shrink-0 items-center gap-2 rounded-lg border border-zinc-200 bg-white px-3 text-xs font-semibold text-zinc-700 shadow-2xs transition-colors hover:bg-zinc-100 dark:border-zinc-700 dark:bg-zinc-800 dark:text-zinc-200 dark:hover:bg-zinc-700 cursor-pointer"
                title="Return to all manga cards"
              >
                <Icon icon="carbon:arrow-left" className="w-3.5 h-3.5" />
                <span>All Manga</span>
              </button>

              {currentSingleGroup.coverImage && (
                <MangaGroupThumbnail image={currentSingleGroup.coverImage} />
              )}

              <div className="min-w-0 flex-1">
                {renamingManga === currentSingleGroup.title ? (
                  <div className="flex items-center gap-1" onClick={(e) => e.stopPropagation()}>
                    <input
                      type="text"
                      value={renameInputValue}
                      onChange={(e) => setRenameInputValue(e.target.value)}
                      maxLength={MANGA_TITLE_MAX_LENGTH}
                      onKeyDown={(e) => {
                        if (e.key === 'Enter') handleSaveRename(currentSingleGroup.title);
                        if (e.key === 'Escape') setRenamingManga(null);
                      }}
                      autoFocus
                      className="min-w-0 max-w-full rounded border border-indigo-400 bg-white px-2 py-1 text-sm font-semibold text-zinc-900 dark:border-indigo-600 dark:bg-zinc-900 dark:text-zinc-100"
                    />
                    <button
                      type="button"
                      onClick={() => handleSaveRename(currentSingleGroup.title)}
                      className="rounded-md p-1.5 text-emerald-600 hover:bg-emerald-50 hover:text-emerald-700 dark:text-emerald-400 dark:hover:bg-emerald-950/40 cursor-pointer"
                      title="Save title"
                    >
                      <Icon icon="carbon:checkmark" className="w-4 h-4" />
                    </button>
                    <button
                      type="button"
                      onClick={() => setRenamingManga(null)}
                      className="rounded-md p-1.5 text-zinc-400 hover:bg-zinc-100 hover:text-zinc-600 dark:hover:bg-zinc-800 cursor-pointer"
                      title="Cancel"
                    >
                      <Icon icon="carbon:close" className="w-4 h-4" />
                    </button>
                  </div>
                ) : (
                  <div className="flex min-w-0 items-center gap-1.5">
                    <h3
                      className="min-w-0 truncate text-base font-bold text-zinc-900 transition-colors hover:text-indigo-600 dark:text-zinc-100 dark:hover:text-indigo-400 cursor-pointer"
                      onClick={() => handleStartRename(currentSingleGroup.title)}
                      title="Click to rename Manga"
                    >
                      {currentSingleGroup.title}
                    </h3>
                    <button
                      type="button"
                      onClick={() => handleStartRename(currentSingleGroup.title)}
                      className="shrink-0 rounded-md p-1.5 text-zinc-400 transition-colors hover:bg-zinc-100 hover:text-zinc-600 dark:hover:bg-zinc-800 dark:hover:text-zinc-300 cursor-pointer"
                      title="Rename Manga"
                    >
                      <Icon icon="carbon:edit" className="w-3.5 h-3.5" />
                    </button>
                  </div>
                )}

                <div className="mt-2 flex flex-wrap items-center gap-2">
                  <span className="rounded-full bg-zinc-100 px-2.5 py-1 text-xs font-medium text-zinc-600 dark:bg-zinc-800 dark:text-zinc-400">
                    {currentSingleGroup.count} {currentSingleGroup.count === 1 ? 'page' : 'pages'}
                  </span>
                  {(() => {
                    const currentSingleReadProgress = getStoredMangaReadProgress(currentSingleGroup.title, currentSingleGroup.count);
                    return <MangaReadBadge progress={currentSingleReadProgress} pageCount={currentSingleGroup.count} />;
                  })()}
                  {currentSingleGroup.seriesTitle ? (
                    <button
                      type="button"
                      onClick={() => setAssigningManga({
                        id: currentSingleGroup.id || mangaIdForTitle(currentSingleGroup.title),
                        title: currentSingleGroup.title,
                        seriesId: currentSingleGroup.seriesId,
                        seriesTitle: currentSingleGroup.seriesTitle,
                      })}
                      className="inline-flex items-center gap-1 rounded-full bg-indigo-100 px-2.5 py-1 text-xs font-semibold text-indigo-700 transition-colors hover:bg-indigo-200 dark:bg-indigo-950/60 dark:text-indigo-300 dark:hover:bg-indigo-900/80 cursor-pointer"
                      title={`In series: ${currentSingleGroup.seriesTitle}. Click to change or move series`}
                    >
                      <Icon icon="carbon:catalog" className="h-3.5 w-3.5" />
                      <span>Series: {currentSingleGroup.seriesTitle}</span>
                    </button>
                  ) : (
                    <button
                      type="button"
                      onClick={() => setAssigningManga({
                        id: currentSingleGroup.id || mangaIdForTitle(currentSingleGroup.title),
                        title: currentSingleGroup.title,
                        seriesId: null,
                        seriesTitle: null,
                      })}
                      className="inline-flex items-center gap-1.5 rounded-lg border border-indigo-200 bg-white px-2.5 py-1 text-xs font-semibold text-indigo-700 transition-colors hover:bg-indigo-50 dark:border-indigo-800 dark:bg-zinc-800 dark:text-indigo-300 dark:hover:bg-indigo-950/50 cursor-pointer"
                      title="Add this manga to a series"
                    >
                      <Icon icon="carbon:add" className="h-3.5 w-3.5" />
                      <span>Add to Series</span>
                    </button>
                  )}
                  {summaryAvailability?.title === currentSingleGroup.title && (
                    <span
                      className={`inline-flex items-center gap-1 rounded-full px-2.5 py-1 text-xs font-semibold ${
                        summaryAvailability.state === 'summarized'
                          ? 'bg-emerald-100 text-emerald-800 dark:bg-emerald-950/60 dark:text-emerald-300'
                            : summaryAvailability.state === 'queued' || summaryAvailability.state === 'generating'
                              ? 'bg-indigo-100 text-indigo-800 dark:bg-indigo-950/60 dark:text-indigo-300'
                            : summaryAvailability.state === 'paused'
                              ? 'bg-amber-100 text-amber-800 dark:bg-amber-950/60 dark:text-amber-300'
                            : summaryAvailability.state === 'stale' || summaryAvailability.state === 'error'
                              ? 'bg-amber-100 text-amber-800 dark:bg-amber-950/60 dark:text-amber-300'
                              : 'bg-zinc-100 text-zinc-600 dark:bg-zinc-800 dark:text-zinc-300'
                      }`}
                      role="status"
                      title={summaryAvailability.state === 'stale' ? 'The manga text changed since this summary was generated.' : undefined}
                    >
                      <Icon
                        icon={summaryAvailability.state === 'summarized' ? 'carbon:checkmark-filled' : summaryAvailability.state === 'queued' ? 'carbon:time' : summaryAvailability.state === 'generating' ? 'carbon:renew' : summaryAvailability.state === 'paused' ? 'carbon:pause-outline' : 'carbon:document'}
                        className={`h-3 w-3 ${summaryAvailability.state === 'generating' ? 'animate-spin' : ''}`}
                      />
                      {summaryAvailability.state === 'summarized'
                        ? 'Summarized'
                        : summaryAvailability.state === 'not-summarized'
                          ? 'Not summarized'
                            : summaryAvailability.state === 'queued'
                              ? 'Summary queued'
                              : summaryAvailability.state === 'generating'
                              ? 'Summary in progress'
                              : summaryAvailability.state === 'paused'
                              ? 'Summary paused'
                            : summaryAvailability.state === 'stale'
                              ? 'Summary needs update'
                              : summaryAvailability.state === 'error'
                                ? 'Summary failed'
                                : summaryAvailability.state === 'loading'
                                  ? 'Checking summary'
                                  : 'Summary status unavailable'}
                    </span>
                  )}
                </div>
              </div>
            </div>

            {/* Actions: Select All, Read, Download CBZ, Delete */}
            <div className="mt-4 flex flex-col gap-3 border-t border-zinc-200 pt-4 dark:border-zinc-800 sm:flex-row sm:items-center sm:justify-between">
              <div className="flex flex-wrap items-center gap-2">
                <button
                  type="button"
                  onClick={() => handleToggleSelectAll(currentSingleGroup.title, currentSingleGroup.images)}
                  className="inline-flex h-10 items-center gap-2 rounded-lg border border-zinc-200 bg-white px-3 text-xs font-medium text-zinc-700 transition-colors hover:bg-zinc-50 dark:border-zinc-700 dark:bg-zinc-800 dark:text-zinc-300 dark:hover:bg-zinc-700 cursor-pointer"
                >
                  <Icon
                    icon={
                      currentSingleGroup.images.length > 0 &&
                      currentSingleGroup.images.every((img) => selectedImageIds.has(img.id))
                        ? 'carbon:checkbox-checked'
                        : 'carbon:checkbox'
                    }
                    className="h-4 w-4"
                  />
                  <span>
                    {currentSingleGroup.images.length > 0 &&
                    currentSingleGroup.images.every((img) => selectedImageIds.has(img.id))
                      ? 'Deselect All'
                      : 'Select All'}
                  </span>
                </button>

                {onRerenderImages && (
                  <button
                    type="button"
                    onClick={() => requestRerender(currentSingleGroup.images)}
                    className="inline-flex h-10 items-center gap-2 rounded-lg border border-indigo-200 bg-white px-3 text-xs font-semibold text-indigo-700 transition-colors hover:bg-indigo-50 dark:border-indigo-800 dark:bg-zinc-800 dark:text-indigo-300 dark:hover:bg-indigo-950/50 cursor-pointer"
                    title="Rerun layout and render for translated pages in this manga"
                  >
                    <Icon icon="carbon:reset" className="h-4 w-4" />
                    <span>Rerun layout</span>
                  </button>
                )}

                {(() => {
                  const currentSingleReadProgress = getStoredMangaReadProgress(currentSingleGroup.title, currentSingleGroup.count);
                  return (
                    <button
                      type="button"
                      onClick={() => handleReadManga(currentSingleGroup.title, currentSingleGroup.images)}
                      disabled={Boolean(readerLoadingTitle)}
                      className="inline-flex h-10 items-center gap-2 rounded-lg bg-indigo-600 px-3.5 text-xs font-semibold text-white shadow-xs transition-colors hover:bg-indigo-500 disabled:cursor-not-allowed disabled:opacity-60 cursor-pointer"
                      title={
                        currentSingleReadProgress.page && !currentSingleReadProgress.complete
                          ? `Continue reading from page ${currentSingleReadProgress.page}`
                          : 'Read manga in continuous scroll mode'
                      }
                      aria-label={
                        readerLoadError === currentSingleGroup.title
                          ? `Retry reading ${currentSingleGroup.title}`
                          : currentSingleReadProgress.complete
                          ? `Read ${currentSingleGroup.title} again`
                          : currentSingleReadProgress.page
                          ? `Continue reading ${currentSingleGroup.title}`
                          : `Read ${currentSingleGroup.title}`
                      }
                    >
                      {readerLoadingTitle === currentSingleGroup.title ? (
                        <Icon icon="carbon:renew" className="w-3.5 h-3.5 animate-spin" />
                      ) : (
                        <Icon icon="carbon:book-open" className="w-3.5 h-3.5" />
                      )}
                      <span>
                        {readerLoadingTitle === currentSingleGroup.title
                          ? 'Loading…'
                          : readerLoadError === currentSingleGroup.title
                          ? 'Retry Read'
                          : currentSingleReadProgress.complete
                          ? 'Read again'
                          : currentSingleReadProgress.page
                          ? 'Continue'
                          : 'Read'}
                      </span>
                    </button>
                  );
                })()}
              </div>

              <div className="flex flex-wrap items-center gap-2 sm:justify-end">

                <button
                  type="button"
                  onClick={() => void handleSummarize(currentSingleGroup.title)}
                  disabled={Boolean(summarizingTitle === currentSingleGroup.title)}
                  className="inline-flex h-10 items-center gap-2 rounded-lg border border-indigo-200 bg-white px-3 text-xs font-semibold text-indigo-700 transition-colors hover:bg-indigo-50 disabled:cursor-not-allowed disabled:opacity-60 dark:border-indigo-800 dark:bg-zinc-800 dark:text-indigo-300 dark:hover:bg-indigo-950/50 cursor-pointer"
                  title="Summarize the original text in this manga"
                >
                  <Icon
                    icon={summarizingTitle === currentSingleGroup.title ? 'carbon:renew' : 'carbon:document'}
                    className={`w-3.5 h-3.5 ${summarizingTitle === currentSingleGroup.title ? 'animate-spin' : ''}`}
                  />
                  <span>{summarizingTitle === currentSingleGroup.title ? 'Summarizing…' : 'Summary'}</span>
                </button>

                <button
                  type="button"
                  onClick={() => handleDownloadCbz(currentSingleGroup.title, currentSingleGroup.images)}
                  disabled={Boolean(downloadingCbz[currentSingleGroup.title])}
                  className="inline-flex h-10 items-center gap-2 rounded-lg border border-zinc-200 bg-white px-3 text-xs font-semibold text-zinc-700 transition-colors hover:bg-zinc-50 disabled:cursor-not-allowed disabled:opacity-50 dark:border-zinc-700 dark:bg-zinc-800 dark:text-zinc-200 dark:hover:bg-zinc-700 cursor-pointer"
                  title="Download this entire manga as a CBZ comic archive"
                >
                  {downloadingCbz[currentSingleGroup.title] ? (
                    <>
                      <Icon icon="carbon:renew" className="w-3.5 h-3.5 animate-spin" />
                      <span>Packaging CBZ...</span>
                    </>
                  ) : (
                    <>
                      <Icon icon="carbon:catalog" className="w-3.5 h-3.5" />
                      <span>Download CBZ</span>
                    </>
                  )}
                </button>

                {onDeleteManga && (
                  <button
                    type="button"
                    onClick={() => setConfirmDeleteManga(currentSingleGroup.title)}
                    className="inline-flex h-10 w-10 items-center justify-center rounded-lg text-zinc-400 transition-colors hover:bg-red-50 hover:text-red-500 dark:hover:bg-red-950/50 dark:hover:text-red-400 cursor-pointer"
                    title="Delete this entire manga from library"
                    aria-label="Delete this entire manga from library"
                  >
                    <Icon icon="carbon:trash-can" className="w-4 h-4" />
                  </button>
                )}
              </div>
            </div>

            {readerLoadError === currentSingleGroup.title && (
              <div className="mt-3 flex items-center justify-end gap-2 text-xs text-red-400" role="status">
                <span>Couldn’t load all pages.</span>
                <button
                  type="button"
                  onClick={() => handleReadManga(currentSingleGroup.title, currentSingleGroup.images)}
                  className="font-semibold text-red-300 underline underline-offset-2 hover:text-white"
                >
                  Try again
                </button>
              </div>
            )}

            {reviewOnly && currentSingleGroup.needsReviewCount > 0 && (
              <div className="mt-3 flex flex-wrap items-center justify-between gap-3 rounded-xl border border-amber-800/70 bg-amber-950/30 px-3.5 py-3 text-amber-100">
                <div className="flex min-w-0 items-start gap-2.5">
                  <Icon icon="carbon:warning-alt" className="mt-0.5 h-4 w-4 shrink-0 text-amber-300" />
                  <p className="text-xs leading-5 text-amber-200/85">
                    {currentSingleGroup.needsReviewCount} flagged {currentSingleGroup.needsReviewCount === 1 ? 'page' : 'pages'} remain. Open a page, fix the highlighted bubble, then save to approve it.
                  </p>
                </div>
                <button
                  type="button"
                  onClick={() => {
                    const firstReviewPage = currentSingleGroup.images[0];
                    if (firstReviewPage) handleCardEdit(firstReviewPage);
                  }}
                  disabled={!currentSingleGroup.images[0]}
                  className="shrink-0 rounded-lg bg-amber-500 px-3 py-1.5 text-xs font-semibold text-amber-950 transition-colors hover:bg-amber-400 disabled:cursor-not-allowed disabled:opacity-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-amber-300"
                >
                  Review next page
                </button>
              </div>
            )}
          </div>

          {/* Bulk Selection Action Bar inside single manga view */}
          {selectedImageIds.size > 0 && (
            <div className="flex flex-wrap items-center justify-between gap-2 rounded-xl border border-indigo-200 bg-indigo-50 p-3 shadow-sm dark:border-indigo-800 dark:bg-indigo-950/70">
              <div className="flex items-center space-x-2 text-xs font-semibold text-indigo-900 dark:text-indigo-200">
                <Icon icon="carbon:checkbox-checked-filled" className="w-4 h-4 text-indigo-600 dark:text-indigo-400" />
                <span>{selectedImageIds.size} {selectedImageIds.size === 1 ? 'page' : 'pages'} selected</span>
              </div>
              <div className="flex items-center space-x-2">
                <button
                  type="button"
                  onClick={() => {
                    setSingleImageToMove(null);
                    setIsMoveModalOpen(true);
                  }}
                  className="flex items-center space-x-1 rounded-lg bg-indigo-600 px-3 py-1.5 text-xs font-semibold text-white shadow-xs hover:bg-indigo-500 transition-colors cursor-pointer"
                >
                  <Icon icon="carbon:folder-move-to" className="w-4 h-4" />
                  <span>Move to Manga...</span>
                </button>
                {(onDeleteImage || onDeleteImages) && (
                  <button
                    type="button"
                    onClick={() => setConfirmDeleteSelectedPages(true)}
                    className="flex items-center space-x-1 rounded-lg bg-red-600 px-3 py-1.5 text-xs font-semibold text-white shadow-xs hover:bg-red-500 transition-colors cursor-pointer"
                    title={`Delete ${selectedImageIds.size} selected ${selectedImageIds.size === 1 ? 'page' : 'pages'}`}
                  >
                    <Icon icon="carbon:trash-can" className="w-4 h-4" />
                    <span>Delete...</span>
                  </button>
                )}
                {onRerenderImages && (
                  <button
                    type="button"
                    onClick={() => requestRerender(currentSingleGroup.images.filter((image) => selectedImageIds.has(image.id)))}
                    className="flex items-center space-x-1 rounded-lg bg-indigo-600 px-3 py-1.5 text-xs font-semibold text-white shadow-xs hover:bg-indigo-500 transition-colors cursor-pointer"
                  >
                    <Icon icon="carbon:reset" className="w-4 h-4" />
                    <span>Rerun layout</span>
                  </button>
                )}
                <button
                  type="button"
                  onClick={() => { setSelectedImageIds(new Set()); lastSelectedGalleryIdRef.current = null; }}
                  className="rounded-lg px-2.5 py-1.5 text-xs text-zinc-600 dark:text-zinc-300 hover:bg-indigo-100 dark:hover:bg-indigo-900/50 transition-colors cursor-pointer"
                >
                  Deselect All
                </button>
              </div>
            </div>
          )}

          {/* Pages Grid */}
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
                    />
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>
      )}

      {/* All Manga Library View */}
      {!currentSingleGroup && (
        <>
          {/* Card View (Default) */}
          {viewMode === 'cards' && (
            <>
              {filteredGroups.length === 0 ? (
                <div className="text-center py-16 rounded-2xl border border-dashed border-zinc-200 dark:border-zinc-800 text-zinc-400">
                  <Icon icon={statusFilter !== 'all' ? "carbon:filter" : "carbon:search"} className="w-10 h-10 mx-auto mb-2 text-zinc-400" />
                  <p className="text-sm font-semibold text-zinc-700 dark:text-zinc-300">
                    {mangaSearchQuery.trim() && statusFilter !== 'all'
                      ? `No ${statusFilter === 'original' ? 'original (raw)' : statusFilter === 'translated' ? 'translated' : statusFilter === 'summarized' ? 'summarized' : 'needs review'} manga matching “${mangaSearchQuery}”`
                      : mangaSearchQuery.trim()
                      ? `No manga matching “${mangaSearchQuery}”`
                      : `No ${statusFilter === 'original' ? 'original (raw)' : statusFilter === 'translated' ? 'translated' : statusFilter === 'summarized' ? 'summarized' : 'needs review'} manga found`}
                  </p>
                  <button
                    type="button"
                    onClick={() => {
                      setMangaSearchInput('');
                      handleSearchSubmit('');
                      handleStatusFilterChange('all');
                      if (activeMangaFilter !== 'all') {
                        closeMangaDetail();
                      }
                    }}
                    className="mt-3 inline-flex items-center space-x-1 text-xs text-indigo-600 dark:text-indigo-400 hover:underline font-medium cursor-pointer"
                  >
                    <span>Reset filters</span>
                  </button>
                </div>
              ) : (
                <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-5 gap-4 sm:gap-5">
                  {visibleGroups.map((group, index) => (
                    <MangaCard
                      key={group.id}
                      mangaId={group.id}
                      title={group.title}
                      count={group.count}
                      needsReviewCount={group.needsReviewCount}
                      reviewOnly={reviewOnly}
                      readProgress={getStoredMangaReadProgress(group.title, group.count)}
                      coverImage={group.coverImage}
                      isDownloading={Boolean(downloadingCbz[group.title])}
                      isReaderLoading={readerLoadingTitle === group.title}
                      isPriority={index < 5}
                      isSummarizing={summarizingTitle === group.title}
                      hasSummary={group.hasSummary}
                      onViewSummary={handleMangaCardViewSummary}
                      onOpenDetails={handleMangaCardOpenDetails}
                      onRead={handleMangaCardRead}
                      onSummarize={handleMangaCardSummarize}
                      onDownloadCbz={handleMangaCardDownloadCbz}
                      onStartRename={handleMangaCardStartRename}
                      onDelete={onDeleteManga ? handleMangaCardDelete : undefined}
                      seriesTitle={group.seriesTitle}
                      isSelected={selectedMangaIds.has(group.id)}
                      isAssigned={Boolean(group.seriesId)}
                      isHighlighted={highlightedMangaId === group.id}
                      onToggleSelect={handleMangaCardToggleSelect}
                    />
                  ))}
                </div>
              )}
            </>
          )}

          {/* Row / Accordion View */}
          {viewMode === 'rows' && (
            <div className="space-y-6">
              {/* Bulk Selection Action Bar for row view */}
              {selectedImageIds.size > 0 && (
                <div className="flex flex-wrap items-center justify-between gap-2 rounded-xl border border-indigo-200 bg-indigo-50 p-3 shadow-sm dark:border-indigo-800 dark:bg-indigo-950/70">
                  <div className="flex items-center space-x-2 text-xs font-semibold text-indigo-900 dark:text-indigo-200">
                    <Icon icon="carbon:checkbox-checked-filled" className="w-4 h-4 text-indigo-600 dark:text-indigo-400" />
                    <span>{selectedImageIds.size} {selectedImageIds.size === 1 ? 'page' : 'pages'} selected</span>
                  </div>
                  <div className="flex items-center space-x-2">
                    <button
                      type="button"
                      onClick={() => {
                        setSingleImageToMove(null);
                        setIsMoveModalOpen(true);
                      }}
                      className="flex items-center space-x-1 rounded-lg bg-indigo-600 px-3 py-1.5 text-xs font-semibold text-white shadow-xs hover:bg-indigo-500 transition-colors cursor-pointer"
                    >
                      <Icon icon="carbon:folder-move-to" className="w-4 h-4" />
                      <span>Move to Manga...</span>
                    </button>
                    {(onDeleteImage || onDeleteImages) && (
                      <button
                        type="button"
                        onClick={() => setConfirmDeleteSelectedPages(true)}
                        className="flex items-center space-x-1 rounded-lg bg-red-600 px-3 py-1.5 text-xs font-semibold text-white shadow-xs hover:bg-red-500 transition-colors cursor-pointer"
                        title={`Delete ${selectedImageIds.size} selected ${selectedImageIds.size === 1 ? 'page' : 'pages'}`}
                      >
                        <Icon icon="carbon:trash-can" className="w-4 h-4" />
                        <span>Delete...</span>
                      </button>
                    )}
                    {onRerenderImages && (
                      <button
                        type="button"
                        onClick={() => requestRerender(allLoadedImages.filter((image) => selectedImageIds.has(image.id)))}
                        className="flex items-center space-x-1 rounded-lg bg-indigo-600 px-3 py-1.5 text-xs font-semibold text-white shadow-xs hover:bg-indigo-500 transition-colors cursor-pointer"
                      >
                        <Icon icon="carbon:reset" className="w-4 h-4" />
                        <span>Rerun layout</span>
                      </button>
                    )}
                    <button
                      type="button"
                      onClick={() => { setSelectedImageIds(new Set()); lastSelectedGalleryIdRef.current = null; }}
                      className="rounded-lg px-2.5 py-1.5 text-xs text-zinc-600 dark:text-zinc-300 hover:bg-indigo-100 dark:hover:bg-indigo-900/50 transition-colors cursor-pointer"
                    >
                      Deselect All
                    </button>
                  </div>
                </div>
              )}

              {filteredGroups.length === 0 ? (
                <div className="text-center py-16 rounded-2xl border border-dashed border-zinc-200 dark:border-zinc-800 text-zinc-400">
                  <Icon icon={statusFilter !== 'all' ? "carbon:filter" : "carbon:search"} className="w-10 h-10 mx-auto mb-2 text-zinc-400" />
                  <p className="text-sm font-semibold text-zinc-700 dark:text-zinc-300">
                    {mangaSearchQuery.trim() && statusFilter !== 'all'
                      ? `No ${statusFilter === 'original' ? 'original (raw)' : statusFilter === 'translated' ? 'translated' : statusFilter === 'summarized' ? 'summarized' : 'needs review'} manga matching “${mangaSearchQuery}”`
                      : mangaSearchQuery.trim()
                      ? `No manga matching “${mangaSearchQuery}”`
                      : `No ${statusFilter === 'original' ? 'original (raw)' : statusFilter === 'translated' ? 'translated' : statusFilter === 'summarized' ? 'summarized' : 'needs review'} manga found`}
                  </p>
                  <button
                    type="button"
                    onClick={() => {
                      setMangaSearchInput('');
                      handleSearchSubmit('');
                      handleStatusFilterChange('all');
                      if (activeMangaFilter !== 'all') {
                        closeMangaDetail();
                      }
                    }}
                    className="mt-3 inline-flex items-center space-x-1 text-xs text-indigo-600 dark:text-indigo-400 hover:underline font-medium cursor-pointer"
                  >
                    <span>Reset filters</span>
                  </button>
                </div>
              ) : (
                visibleGroups.map((group) => {
                  const isCollapsed = !expandedGroups[group.title];
                  const isDownloading = Boolean(downloadingCbz[group.title]);
                  const isRenaming = renamingManga === group.title;
                  const allInGroupSelected = group.images.length > 0 && group.images.every((img) => selectedImageIds.has(img.id));
                  const readProgress = getStoredMangaReadProgress(group.title, group.count);
                  const isRowHighlighted = highlightedMangaId === group.id;

                  return (
                    <div
                      key={group.id}
                      data-manga-id={group.id}
                      className={`rounded-2xl border ${
                        isRowHighlighted
                          ? 'border-indigo-500 ring-4 ring-indigo-500/80 shadow-lg shadow-indigo-500/20'
                          : 'border-zinc-200 dark:border-zinc-800'
                      } bg-white dark:bg-zinc-900/50 overflow-hidden shadow-xs transition-all duration-300`}
                    >
                      {/* Manga Group Header */}
                      <div
                        className={`flex flex-wrap items-center justify-between gap-3 p-4 bg-zinc-50/70 dark:bg-zinc-800/50 ${
                          !isCollapsed ? 'border-b border-zinc-200 dark:border-zinc-800' : ''
                        }`}
                      >
                        <div className="flex items-center space-x-3">
                          <button
                            type="button"
                            onClick={() => toggleGroupCollapse(group.title)}
                            className="p-1 rounded text-zinc-400 hover:text-zinc-700 dark:hover:text-zinc-200 transition-colors cursor-pointer"
                            title={isCollapsed ? 'Expand Manga' : 'Collapse Manga'}
                          >
                            <Icon
                              icon={isCollapsed ? 'carbon:chevron-right' : 'carbon:chevron-down'}
                              className="w-4 h-4"
                            />
                          </button>

                          <div className="flex items-center space-x-2">
                            {group.coverImage && (
                              <button
                                type="button"
                                onClick={() => toggleGroupCollapse(group.title)}
                                className="cursor-pointer focus:outline-hidden rounded-md transition-opacity hover:opacity-80 shrink-0"
                                title={isCollapsed ? 'Expand Manga' : 'Collapse Manga'}
                              >
                                <MangaGroupThumbnail image={group.coverImage} />
                              </button>
                            )}

                            {isRenaming ? (
                              <div className="flex items-center space-x-1" onClick={(e) => e.stopPropagation()}>
                                <input
                                  type="text"
                                  value={renameInputValue}
                                  onChange={(e) => setRenameInputValue(e.target.value)}
                                  maxLength={MANGA_TITLE_MAX_LENGTH}
                                  onKeyDown={(e) => {
                                    if (e.key === 'Enter') handleSaveRename(group.title);
                                    if (e.key === 'Escape') setRenamingManga(null);
                                  }}
                                  autoFocus
                                  className="text-sm font-semibold rounded border border-indigo-400 dark:border-indigo-600 px-2 py-0.5 bg-white dark:bg-zinc-900 text-zinc-900 dark:text-zinc-100"
                                />
                                <button
                                  type="button"
                                  onClick={() => handleSaveRename(group.title)}
                                  className="p-1 text-emerald-600 hover:text-emerald-700 dark:text-emerald-400 cursor-pointer"
                                  title="Save title"
                                >
                                  <Icon icon="carbon:checkmark" className="w-4 h-4" />
                                </button>
                                <button
                                  type="button"
                                  onClick={() => setRenamingManga(null)}
                                  className="p-1 text-zinc-400 hover:text-zinc-600 cursor-pointer"
                                  title="Cancel"
                                >
                                  <Icon icon="carbon:close" className="w-4 h-4" />
                                </button>
                              </div>
                            ) : (
                              <div className="flex items-center space-x-1.5">
                                <h4
                                  className="text-sm font-semibold text-zinc-900 dark:text-zinc-100 cursor-pointer hover:text-indigo-600 dark:hover:text-indigo-400 transition-colors"
                                  onClick={() => handleStartRename(group.title)}
                                  title="Click to rename Manga"
                                >
                                  {group.title}
                                </h4>
                                <button
                                  type="button"
                                  onClick={() => handleStartRename(group.title)}
                                  className="text-zinc-400 hover:text-zinc-600 dark:hover:text-zinc-300 p-0.5 cursor-pointer"
                                  title="Rename Manga"
                                >
                                  <Icon icon="carbon:edit" className="w-3.5 h-3.5" />
                                </button>
                              </div>
                            )}
                          </div>

                          <span className="rounded-full bg-zinc-200 dark:bg-zinc-700 px-2 py-0.5 text-[11px] font-medium text-zinc-700 dark:text-zinc-300">
                            {group.count} {group.count === 1 ? 'page' : 'pages'}
                          </span>
                          {group.seriesTitle && (
                            <button
                              type="button"
                              onClick={() => setAssigningManga({
                                id: group.id,
                                title: group.title,
                                seriesId: group.seriesId,
                                seriesTitle: group.seriesTitle,
                              })}
                              className="inline-flex items-center gap-1 rounded-full bg-indigo-100 px-2 py-0.5 text-[11px] font-medium text-indigo-700 hover:bg-indigo-200 dark:bg-indigo-950/60 dark:text-indigo-300 dark:hover:bg-indigo-900/80 transition-colors cursor-pointer"
                              title={`In series: ${group.seriesTitle}. Click to change or move series`}
                            >
                              <Icon icon="carbon:catalog" className="w-3 h-3" />
                              <span className="truncate max-w-28 sm:max-w-40">{group.seriesTitle}</span>
                            </button>
                          )}
                          <MangaReadBadge progress={readProgress} pageCount={group.count} />
                        </div>

                        {/* Manga Actions: Detail, Read, Download CBZ & Select All */}
                        <div className="flex flex-wrap items-center justify-end gap-2">
                          <button
                            type="button"
                            onClick={() => openMangaDetail(group.title)}
                            className="flex items-center space-x-1 rounded-lg border border-zinc-200 dark:border-zinc-700 bg-white dark:bg-zinc-800 px-2.5 py-1 text-xs font-semibold text-zinc-700 dark:text-zinc-300 hover:bg-zinc-50 dark:hover:bg-zinc-700 transition-colors cursor-pointer"
                            title={`Open ${group.title} manga detail page`}
                          >
                            <Icon icon="carbon:launch" className="w-3.5 h-3.5" />
                            <span>Detail</span>
                          </button>

                          <button
                            type="button"
                            onClick={() => handleToggleSelectAll(group.title, group.images)}
                            className="flex items-center space-x-1 rounded-lg border border-zinc-200 dark:border-zinc-700 bg-white dark:bg-zinc-800 px-2.5 py-1 text-xs text-zinc-700 dark:text-zinc-300 hover:bg-zinc-50 dark:hover:bg-zinc-700 transition-colors cursor-pointer"
                          >
                            <Icon
                              icon={allInGroupSelected ? 'carbon:checkbox-checked' : 'carbon:checkbox'}
                              className="w-3.5 h-3.5"
                            />
                            <span>{allInGroupSelected ? 'Deselect' : 'Select All'}</span>
                          </button>

                          {!group.seriesId && (
                            <button
                              type="button"
                              onClick={() => setAssigningManga({
                                id: group.id,
                                title: group.title,
                                seriesId: null,
                                seriesTitle: null,
                              })}
                              className="flex items-center space-x-1 rounded-lg border border-indigo-200 dark:border-indigo-800 bg-white dark:bg-zinc-800 px-2.5 py-1 text-xs text-indigo-700 dark:text-indigo-300 hover:bg-indigo-50 dark:hover:bg-indigo-950/50 transition-colors cursor-pointer"
                              title="Add to series"
                            >
                              <Icon icon="carbon:catalog" className="w-3.5 h-3.5" />
                              <span>+ Series</span>
                            </button>
                          )}

                          {/* Read Button — opens scroll reader */}
                          <button
                            type="button"
                            onClick={() => handleReadManga(group.title, group.images)}
                            disabled={Boolean(readerLoadingTitle)}
                            className="flex items-center space-x-1.5 rounded-lg bg-emerald-600 px-3 py-1.5 text-xs font-semibold text-white shadow-xs hover:bg-emerald-500 disabled:opacity-60 transition-colors cursor-pointer"
                            title="Read manga in continuous scroll mode"
                            aria-label={readerLoadError === group.title ? `Retry reading ${group.title}` : `Read ${group.title}`}
                          >
                            {readerLoadingTitle === group.title ? (
                              <Icon icon="carbon:renew" className="w-3.5 h-3.5 animate-spin" />
                            ) : (
                              <Icon icon="carbon:book-open" className="w-3.5 h-3.5" />
                            )}
                            <span>{readerLoadingTitle === group.title ? 'Loading…' : readerLoadError === group.title ? 'Retry Read' : 'Read'}</span>
                          </button>

                          <button
                            type="button"
                            onClick={() => void handleSummarize(group.title)}
                            disabled={Boolean(summarizingTitle)}
                            className="flex items-center space-x-1.5 rounded-lg border border-indigo-200 dark:border-indigo-800 bg-white dark:bg-zinc-800 px-3 py-1.5 text-xs font-semibold text-indigo-700 dark:text-indigo-300 hover:bg-indigo-50 dark:hover:bg-indigo-950/50 disabled:opacity-60 transition-colors cursor-pointer"
                            title="Summarize the original text in this manga"
                          >
                            <Icon icon={summarizingTitle === group.title ? 'carbon:renew' : 'carbon:document'} className={`w-3.5 h-3.5 ${summarizingTitle === group.title ? 'animate-spin' : ''}`} />
                            <span>{summarizingTitle === group.title ? 'Summarizing…' : 'Summary'}</span>
                          </button>

                          {/* Primary Manga CBZ Export Button */}
                          <button
                            type="button"
                            onClick={() => handleDownloadCbz(group.title, group.images)}
                            disabled={isDownloading}
                            className="flex items-center space-x-1.5 rounded-lg bg-indigo-600 px-3 py-1.5 text-xs font-semibold text-white shadow-xs hover:bg-indigo-500 disabled:opacity-50 transition-colors cursor-pointer"
                            title="Download this entire manga as a CBZ comic archive"
                          >
                            {isDownloading ? (
                              <>
                                <Icon icon="carbon:renew" className="w-3.5 h-3.5 animate-spin" />
                                <span>Packaging CBZ...</span>
                              </>
                            ) : (
                              <>
                                <Icon icon="carbon:catalog" className="w-3.5 h-3.5" />
                                <span>Download CBZ</span>
                              </>
                            )}
                          </button>

                          {/* Delete Manga */}
                          {onDeleteManga && (
                            <button
                              type="button"
                              onClick={() => setConfirmDeleteManga(group.title)}
                              className="rounded-lg p-1.5 text-zinc-400 hover:text-red-500 dark:hover:text-red-400 hover:bg-red-50 dark:hover:bg-red-950/50 transition-colors cursor-pointer"
                              title="Delete this entire manga from library"
                            >
                              <Icon icon="carbon:trash-can" className="w-4 h-4" />
                            </button>
                          )}
                        </div>

                        {readerLoadError === group.title && (
                          <div className="basis-full flex items-center justify-end gap-2 text-xs text-red-300" role="status">
                            <span>Couldn’t load all pages.</span>
                            <button
                              type="button"
                              onClick={() => handleReadManga(group.title, group.images)}
                              className="font-semibold text-red-200 underline underline-offset-2 hover:text-white"
                            >
                              Try again
                            </button>
                          </div>
                        )}
                      </div>

                      {/* Group Pages Grid */}
                      {!isCollapsed && (
                        <div className="p-4">
                          {group.isLoading ? (
                            <div className="flex items-center justify-center py-12 text-zinc-400 space-x-2">
                              <Icon icon="carbon:renew" className="w-5 h-5 animate-spin text-indigo-500" />
                              <span className="text-sm font-medium">Loading pages for {group.title}...</span>
                            </div>
                          ) : group.images.length === 0 ? (
                            <div className="text-center py-8 text-zinc-400 text-xs">
                              No pages available for this manga.
                            </div>
                          ) : (
                            <RowGroupCards
                              title={group.title}
                              images={group.images}
                              highlightedImageId={highlightedImageId}
                              selectedImageIds={selectedImageIds}
                              onToggleSelectImage={toggleSelectImage}
                              onMoveImage={handleCardMove}
                              onReadFromHere={handleRowGroupReadFromHere}
                              onClickImage={handleCardClick}
                              onDownloadImage={handleCardDownload}
                              onDeleteImage={onDeleteImage ? handleCardDelete : undefined}
                              onEditImage={handleCardEdit}
                              onRerenderImage={onRerenderImage}
                            />
                          )}
                        </div>
                      )}
                    </div>
                  );
                })
              )}
            </div>
          )}

          {galleryPageCount > 1 && (
            <Pagination
              currentPage={galleryPage}
              totalPages={galleryPageCount}
              totalItems={galleryMangaCount}
              pageSize={requestedGalleryPageSize}
              itemLabel="manga"
              pageSizeOptions={GALLERY_PAGE_SIZE_OPTIONS}
              onPageSizeChange={setGalleryPageSizeAndRoute}
              onPageChange={setGalleryPageAndRoute}
              ariaLabel="Manga gallery pages"
            />
          )}
        </>
      )}

      {/* Confirm Delete Manga Modal */}
      {confirmDeleteManga && (
        <div
          className="fixed inset-0 z-50 bg-black/60 backdrop-blur-xs flex items-center justify-center p-4"
        >
          <div
            className="w-full max-w-sm rounded-2xl border border-zinc-200 dark:border-zinc-800 bg-white dark:bg-zinc-900 p-5 shadow-2xl space-y-4"
          >
            <div className="flex items-center space-x-3 text-red-600 dark:text-red-400">
              <Icon icon="carbon:warning" className="w-6 h-6 shrink-0" />
              <h3 className="text-base font-semibold text-zinc-900 dark:text-zinc-100">
                Delete Manga
              </h3>
            </div>
            <p className="text-xs text-zinc-600 dark:text-zinc-400">
              Are you sure you want to delete &ldquo;<strong className="text-zinc-900 dark:text-zinc-200">{confirmDeleteManga}</strong>&rdquo;? All {
                mangaGroups.find((g) => g.title === confirmDeleteManga)?.count || ''
              } pages will be removed from your gallery.
            </p>
            <div className="flex items-center justify-end space-x-2 pt-2">
              <button
                type="button"
                onClick={() => setConfirmDeleteManga(null)}
                className="rounded-lg px-3 py-1.5 text-xs text-zinc-600 dark:text-zinc-300 hover:bg-zinc-100 dark:hover:bg-zinc-800 transition-colors cursor-pointer"
              >
                Cancel
              </button>
              <button
                type="button"
                onClick={() => {
                  const targetGroup = mangaGroups.find((g) => g.title === confirmDeleteManga);
                  void handleDeleteMangaGroup(confirmDeleteManga, targetGroup?.images || []);
                }}
                className="rounded-lg bg-red-600 px-3 py-1.5 text-xs font-semibold text-white hover:bg-red-500 transition-colors cursor-pointer"
              >
                Delete Manga
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Confirm Delete Multiple Pages Modal */}
      {confirmDeleteSelectedPages && (
        <div
          className="fixed inset-0 z-50 bg-black/60 backdrop-blur-xs flex items-center justify-center p-4"
          role="dialog"
          aria-modal="true"
          aria-labelledby="confirm-delete-selected-pages-title"
        >
          <div
            className="w-full max-w-sm rounded-2xl border border-zinc-200 dark:border-zinc-800 bg-white dark:bg-zinc-900 p-5 shadow-2xl space-y-4"
          >
            <div className="flex items-center space-x-3 text-red-600 dark:text-red-400">
              <Icon icon="carbon:warning" className="w-6 h-6 shrink-0" />
              <h3 id="confirm-delete-selected-pages-title" className="text-base font-semibold text-zinc-900 dark:text-zinc-100">
                Delete Selected Pages
              </h3>
            </div>
            <p className="text-xs text-zinc-600 dark:text-zinc-400">
              Are you sure you want to delete <strong className="text-zinc-900 dark:text-zinc-200">{selectedImageIds.size}</strong> selected {selectedImageIds.size === 1 ? 'page' : 'pages'}? This action cannot be undone.
            </p>
            <div className="flex items-center justify-end space-x-2 pt-2">
              <button
                type="button"
                disabled={isDeletingSelectedPages}
                onClick={() => setConfirmDeleteSelectedPages(false)}
                className="rounded-lg px-3 py-1.5 text-xs text-zinc-600 dark:text-zinc-300 hover:bg-zinc-100 dark:hover:bg-zinc-800 transition-colors cursor-pointer"
              >
                Cancel
              </button>
              <button
                type="button"
                disabled={isDeletingSelectedPages}
                onClick={() => void handleDeleteSelectedPages()}
                className="flex items-center space-x-1.5 rounded-lg bg-red-600 px-3 py-1.5 text-xs font-semibold text-white hover:bg-red-500 disabled:opacity-50 transition-colors cursor-pointer"
              >
                {isDeletingSelectedPages ? (
                  <Icon icon="carbon:renew" className="w-4 h-4 animate-spin" />
                ) : (
                  <Icon icon="carbon:trash-can" className="w-4 h-4" />
                )}
                <span>Delete {selectedImageIds.size} {selectedImageIds.size === 1 ? 'Page' : 'Pages'}</span>
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Confirm Delete Multiple Manga Groups Modal */}
      {confirmDeleteSelectedMangas && (
        <div
          className="fixed inset-0 z-50 bg-black/60 backdrop-blur-xs flex items-center justify-center p-4"
          role="dialog"
          aria-modal="true"
          aria-labelledby="confirm-delete-selected-mangas-title"
        >
          <div
            className="w-full max-w-md rounded-2xl border border-zinc-200 dark:border-zinc-800 bg-white dark:bg-zinc-900 p-5 shadow-2xl space-y-4"
          >
            <div className="flex items-center space-x-3 text-red-600 dark:text-red-400">
              <Icon icon="carbon:warning" className="w-6 h-6 shrink-0" />
              <h3 id="confirm-delete-selected-mangas-title" className="text-base font-semibold text-zinc-900 dark:text-zinc-100">
                Delete Selected Manga
              </h3>
            </div>
            <div className="space-y-2">
              <p className="text-xs text-zinc-600 dark:text-zinc-400">
                Are you sure you want to delete <strong className="text-zinc-900 dark:text-zinc-200">{selectedMangaIds.size}</strong> selected {selectedMangaIds.size === 1 ? 'manga' : 'manga groups'}? All pages inside will be permanently removed.
              </p>
              <div className="max-h-36 overflow-y-auto rounded-lg border border-zinc-200 dark:border-zinc-800 bg-zinc-50 dark:bg-zinc-800/50 p-2 space-y-1">
                {Array.from(selectedMangaIds.entries()).map(([id, title]) => (
                  <div key={id} className="text-xs text-zinc-700 dark:text-zinc-300 font-medium truncate flex items-center gap-1.5">
                    <Icon icon="carbon:book" className="w-3.5 h-3.5 text-zinc-400 shrink-0" />
                    <span className="truncate">{title}</span>
                  </div>
                ))}
              </div>
            </div>
            <div className="flex items-center justify-end space-x-2 pt-2">
              <button
                type="button"
                disabled={isDeletingSelectedMangas}
                onClick={() => setConfirmDeleteSelectedMangas(false)}
                className="rounded-lg px-3 py-1.5 text-xs text-zinc-600 dark:text-zinc-300 hover:bg-zinc-100 dark:hover:bg-zinc-800 transition-colors cursor-pointer"
              >
                Cancel
              </button>
              <button
                type="button"
                disabled={isDeletingSelectedMangas}
                onClick={() => void handleDeleteSelectedMangas()}
                className="flex items-center space-x-1.5 rounded-lg bg-red-600 px-3 py-1.5 text-xs font-semibold text-white hover:bg-red-500 disabled:opacity-50 transition-colors cursor-pointer"
              >
                {isDeletingSelectedMangas ? (
                  <Icon icon="carbon:renew" className="w-4 h-4 animate-spin" />
                ) : (
                  <Icon icon="carbon:trash-can" className="w-4 h-4" />
                )}
                <span>Delete {selectedMangaIds.size} Manga</span>
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Move to Manga Modal */}
      {isMoveModalOpen && (
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
                onClick={() => setIsMoveModalOpen(false)}
                className="rounded-lg p-1 text-zinc-400 hover:text-zinc-600 dark:hover:text-zinc-200"
              >
                <Icon icon="carbon:close" className="w-5 h-5" />
              </button>
            </div>

            <p className="text-xs text-zinc-500">
              {singleImageToMove
                ? `Assign "${singleImageToMove.originalName}" to a manga group:`
                : `Assign ${selectedImageIds.size} selected pages to a manga group:`}
            </p>

            {/* Existing Manga choices */}
            {mangaGroups.length > 0 && (
              <div className="space-y-1.5">
                <label className="text-xs font-medium text-zinc-700 dark:text-zinc-300">
                  Select existing manga:
                </label>
                <div className="max-h-36 overflow-y-auto space-y-1 rounded-lg border border-zinc-200 dark:border-zinc-800 p-1.5">
                  {mangaGroups.map((g) => (
                    <button
                      key={g.title}
                      type="button"
                      onClick={() => handleMoveSelected(g.title)}
                      className="w-full flex items-center justify-between rounded-md px-2.5 py-1.5 text-xs text-left text-zinc-800 dark:text-zinc-200 hover:bg-indigo-50 dark:hover:bg-indigo-950/50 hover:text-indigo-600 dark:hover:text-indigo-400 transition-colors"
                    >
                      <span className="truncate">{g.title}</span>
                      <span className="text-[10px] text-zinc-400">{g.images.length} pages</span>
                    </button>
                  ))}
                </div>
              </div>
            )}

            {/* Create new Manga */}
            <div className="space-y-1.5">
              <label className="text-xs font-medium text-zinc-700 dark:text-zinc-300">
                Or create new manga collection:
              </label>
              <div className="flex items-center space-x-2">
                <input
                  type="text"
                  value={targetMangaName}
                  onChange={(e) => setTargetMangaName(e.target.value)}
                  maxLength={MANGA_TITLE_MAX_LENGTH}
                  placeholder="e.g. One Piece Chapter 100"
                  onKeyDown={(e) => {
                    if (e.key === 'Enter' && targetMangaName.trim()) {
                      handleMoveSelected(targetMangaName);
                    }
                  }}
                  className="flex-1 rounded-lg border border-zinc-200 dark:border-zinc-700 bg-zinc-50 dark:bg-zinc-800 px-3 py-1.5 text-xs text-zinc-900 dark:text-zinc-100 placeholder-zinc-400 outline-hidden focus:border-indigo-500"
                />
                <button
                  type="button"
                  onClick={() => handleMoveSelected(targetMangaName)}
                  disabled={!targetMangaName.trim()}
                  className="rounded-lg bg-indigo-600 px-3 py-1.5 text-xs font-semibold text-white shadow-xs hover:bg-indigo-500 disabled:opacity-50 transition-colors"
                >
                  Create & Move
                </button>
              </div>
            </div>
          </div>
        </div>
      )}

      {/* Saved manga synopsis modal */}
      {summaryState && (
        <div
          className="fixed inset-0 z-60 flex items-center justify-center bg-black/70 p-4 isolate"
          onClick={() => setSummaryState(null)}
        >
          <div
            role="dialog"
            aria-modal="true"
            aria-labelledby="manga-summary-title"
            className="w-full max-w-2xl rounded-2xl border border-zinc-200 bg-white shadow-2xl dark:border-zinc-700 dark:bg-zinc-900 transform-gpu"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="flex items-start justify-between gap-4 border-b border-zinc-200 px-5 py-4 dark:border-zinc-800">
              <div className="min-w-0">
                <p className="text-xs font-semibold uppercase tracking-wide text-indigo-600 dark:text-indigo-400">Manga synopsis</p>
                <h3 id="manga-summary-title" className="mt-1 truncate text-lg font-semibold text-zinc-900 dark:text-zinc-100">
                  {summaryState.title}
                </h3>
              </div>
              <div className="flex items-center gap-2">
                {summaryState.loading && (
                  <button
                    type="button"
                    onClick={() => setSummaryState(null)}
                    className="flex items-center space-x-1 rounded-lg border border-indigo-200 dark:border-indigo-800 bg-indigo-50 dark:bg-indigo-950/60 px-2.5 py-1 text-xs font-semibold text-indigo-700 dark:text-indigo-300 hover:bg-indigo-100 dark:hover:bg-indigo-900/60 transition-colors cursor-pointer"
                    title="Continue generation in the background"
                  >
                    <Icon icon="carbon:minimize" className="w-3.5 h-3.5" />
                    <span>Run in background</span>
                  </button>
                )}
                <button
                  type="button"
                  onClick={() => setSummaryState(null)}
                  className="rounded-lg p-1.5 text-zinc-400 hover:bg-zinc-100 hover:text-zinc-700 dark:hover:bg-zinc-800 dark:hover:text-zinc-200"
                  title="Close summary"
                  aria-label="Close summary"
                >
                  <Icon icon="carbon:close" className="h-5 w-5" />
                </button>
              </div>
            </div>

            <div className="max-h-[60vh] overflow-y-auto px-5 py-5 synopsis-scroll-container overscroll-contain">
              {summaryState.loading ? (
                <div className="flex flex-col items-center justify-center gap-3 py-16 text-sm text-zinc-500 dark:text-zinc-400" aria-live="polite">
                  <div className="flex items-center gap-2">
                    {summaryState.data?.jobStatus === 'paused' ? (
                      <Icon icon="carbon:pause-outline" className="h-5 w-5 text-amber-500" />
                    ) : (
                      <Icon icon="carbon:renew" className="h-5 w-5 animate-spin text-indigo-500" />
                    )}
                    <span>
                      {summaryState.data?.jobStatus === 'paused'
                        ? 'Synopsis generation paused'
                        : summaryState.data?.jobStage === 'summarizing'
                        ? 'Synthesizing manga synopsis…'
                        : summaryState.data?.summary
                        ? 'Regenerating synopsis…'
                        : 'Generating synopsis…'}
                    </span>
                  </div>
                  {typeof summaryState.data?.jobProgress === 'number' && summaryState.data.jobProgress > 0 && (
                    <div className="w-48 bg-zinc-200 dark:bg-zinc-700 rounded-full h-1.5 overflow-hidden">
                      <div
                        className="bg-indigo-600 h-1.5 rounded-full transition-all duration-300"
                        style={{ width: `${Math.min(100, Math.max(0, summaryState.data.jobProgress))}%` }}
                      />
                    </div>
                  )}
                  <div className="flex items-center gap-2 mt-2">
                    {summaryState.data?.jobStatus === 'paused' ? (
                      <button
                        type="button"
                        onClick={async () => {
                          const groupId = summaryState.data?.groupId || mangaGroups.find((g) => g.title === summaryState.title)?.id;
                          await resumeSummaryJob({ groupId, title: summaryState.title });
                          setSummaryState((prev) => prev ? { ...prev, data: prev.data ? { ...prev.data, jobStatus: 'generating' } : null } : null);
                          setSummaryAvailability({ title: summaryState.title, state: 'generating' });
                        }}
                        className="inline-flex items-center gap-1 rounded-lg border border-indigo-300 dark:border-indigo-700 bg-white dark:bg-zinc-800 px-3 py-1.5 text-xs font-semibold text-indigo-600 dark:text-indigo-400 hover:bg-indigo-50 dark:hover:bg-indigo-950/40 cursor-pointer"
                      >
                        <Icon icon="carbon:play" className="h-3.5 w-3.5" />
                        <span>Resume</span>
                      </button>
                    ) : (
                      <button
                        type="button"
                        onClick={async () => {
                          const groupId = summaryState.data?.groupId || mangaGroups.find((g) => g.title === summaryState.title)?.id;
                          await pauseSummaryJob({ groupId, title: summaryState.title });
                          setSummaryState((prev) => prev ? { ...prev, data: prev.data ? { ...prev.data, jobStatus: 'paused' } : null } : null);
                          setSummaryAvailability({ title: summaryState.title, state: 'paused' });
                        }}
                        className="inline-flex items-center gap-1 rounded-lg border border-amber-300 dark:border-amber-700 bg-white dark:bg-zinc-800 px-3 py-1.5 text-xs font-semibold text-amber-600 dark:text-amber-400 hover:bg-amber-50 dark:hover:bg-amber-950/40 cursor-pointer"
                      >
                        <Icon icon="carbon:pause" className="h-3.5 w-3.5" />
                        <span>Pause</span>
                      </button>
                    )}
                    <button
                      type="button"
                      onClick={async () => {
                        const groupId = summaryState.data?.groupId || mangaGroups.find((g) => g.title === summaryState.title)?.id;
                        await stopSummaryJob({ groupId, title: summaryState.title });
                        setSummaryState(null);
                        setSummaryAvailability({ title: summaryState.title, state: 'not-summarized' });
                      }}
                      className="inline-flex items-center gap-1 rounded-lg border border-rose-300 dark:border-rose-700 bg-white dark:bg-zinc-800 px-3 py-1.5 text-xs font-semibold text-rose-600 dark:text-rose-400 hover:bg-rose-50 dark:hover:bg-rose-950/40 cursor-pointer"
                    >
                      <Icon icon="carbon:stop-filled-alt" className="h-3.5 w-3.5" />
                      <span>Stop</span>
                    </button>
                  </div>
                  <button
                    type="button"
                    onClick={() => setSummaryState(null)}
                    className="text-xs text-indigo-600 dark:text-indigo-400 hover:underline cursor-pointer font-medium mt-1"
                  >
                    Minimize and continue in background
                  </button>
                </div>
              ) : summaryState.data?.summary ? (
                <>
                  {summaryState.data.stale && (
                    <div className="mb-4 rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-xs font-medium text-amber-900 dark:border-amber-800 dark:bg-amber-950/40 dark:text-amber-200" role="status">
                      This synopsis is outdated because the manga text changed.
                    </div>
                  )}
                  {summaryState.error && (
                    <div className="mb-4 rounded-lg border border-rose-200 bg-rose-50 px-3 py-2 text-xs font-medium text-rose-900 dark:border-rose-800 dark:bg-rose-950/40 dark:text-rose-200" role="alert">
                      {summaryState.error}
                    </div>
                  )}
                  <p className="whitespace-pre-wrap text-sm leading-7 text-zinc-700 dark:text-zinc-200">
                    {summaryState.data.summary}
                  </p>
                  <p className="mt-5 text-xs text-zinc-500 dark:text-zinc-400">
                    {summaryState.data.pageCount} pages · {summaryState.data.textPageCount} with original text
                    {summaryState.data.language ? ` · ${summaryState.data.language}` : ''}
                    {summaryState.data.model ? ` · ${summaryState.data.model}` : ''}
                    {summaryState.data.skippedPages?.length ? ` · ${summaryState.data.skippedPages.length} page${summaryState.data.skippedPages.length === 1 ? '' : 's'} skipped` : ''}
                  </p>
                  {summaryState.data.skippedPages?.length ? (
                    <p className="mt-1 text-xs text-amber-700 dark:text-amber-300">
                      Skipped: {summaryState.data.skippedPages.join(', ')}
                    </p>
                  ) : null}
                </>
              ) : (
                <div className="py-8 text-sm text-zinc-600 dark:text-zinc-300" role="alert">
                  <p>{summaryState.error || 'No synopsis is available yet.'}</p>
                </div>
              )}
            </div>

            {!summaryState.loading && (
              <div className="flex flex-wrap items-center justify-between gap-2 border-t border-zinc-200 px-5 py-4 dark:border-zinc-800">
                <Link
                  to={buildMangaDetailIdUrl(summaryState.data?.groupId || mangaGroups.find((group) => group.title === summaryState.title)?.id || mangaIdForTitle(summaryState.title))}
                  className="inline-flex min-h-10 items-center gap-1.5 rounded-lg border border-zinc-200 px-3 py-2 text-xs font-semibold text-zinc-700 hover:bg-zinc-100 dark:border-zinc-700 dark:text-zinc-200 dark:hover:bg-zinc-800"
                >
                  <Icon icon="carbon:launch" className="h-3.5 w-3.5" /> Open manga details
                </Link>
                <div className="flex flex-wrap items-center justify-end gap-2">
                {summaryState.data?.summary && (
                  <button
                    type="button"
                    onClick={() => void copySummary()}
                    className="rounded-lg border border-zinc-200 bg-white px-3 py-2 text-xs font-semibold text-zinc-700 hover:bg-zinc-50 dark:border-zinc-700 dark:bg-zinc-800 dark:text-zinc-200 dark:hover:bg-zinc-700"
                  >
                    <Icon icon={summaryCopied ? 'carbon:checkmark' : 'carbon:copy'} className="mr-1 inline h-3.5 w-3.5" />
                    {summaryCopied ? 'Copied' : 'Copy'}
                  </button>
                )}
                <button
                  type="button"
                  onClick={() => void handleSummarize(summaryState.title, true)}
                  disabled={Boolean(summarizingTitle)}
                  className="rounded-lg bg-indigo-600 px-3 py-2 text-xs font-semibold text-white hover:bg-indigo-500 disabled:opacity-60"
                >
                  <Icon icon="carbon:renew" className="mr-1 inline h-3.5 w-3.5" />
                  {summaryState.data?.summary ? 'Regenerate' : 'Try again'}
                </button>
                <button
                  type="button"
                  onClick={() => void handleSummarize(summaryState.title, true, true)}
                  disabled={Boolean(summarizingTitle)}
                  className="rounded-lg border border-indigo-200 bg-indigo-50 px-3 py-2 text-xs font-semibold text-indigo-700 hover:bg-indigo-100 disabled:opacity-60 dark:border-indigo-800 dark:bg-indigo-950/50 dark:text-indigo-300 dark:hover:bg-indigo-900/60"
                  title="Run detection, OCR, text merging, and cleanup again for every page, then overwrite the saved text and summary"
                >
                  <Icon icon="carbon:reset" className="mr-1 inline h-3.5 w-3.5" />
                  Re-read & regenerate
                </button>
                </div>
              </div>
            )}
          </div>
        </div>
      )}

      {/* Fullscreen interactive page detail modal */}
      {isModalOpen && selectedImage && (
        <PageDetailModal
          image={selectedImage}
          onClose={closeImageModal}
          images={currentModalImages}
          currentIndex={Math.max(0, currentModalImages.findIndex((img) => img.id === selectedImage.id))}
          onNavigate={(index) => {
            if (currentModalImages[index]) {
              setSelectedImage(currentModalImages[index]);
              if (onOpenPageView && currentModalImages[index].folder) {
                onOpenPageView(currentModalImages[index].folder);
              }
            }
          }}
          onDownload={downloadSingle}
          onDelete={
            onDeleteImage
              ? (toDelete) => {
                  closeImageModal();
                  onDeleteImage(toDelete);
                }
              : undefined
          }
          onEdit={
            selectedImage.hasTextRegions && selectedImage.sourceType !== 'original'
              ? (img) => {
                  if (onOpenPageEdit && img.folder) {
                    onOpenPageEdit(img.folder);
                  } else {
                    setEditingImage(img);
                  }
                }
              : undefined
          }
          onRetry={
            onRetryImage && selectedImage.sourceType !== 'original'
              ? onRetryImage
              : undefined
          }
          onRerender={
            onRerenderImage && selectedImage.sourceType !== 'original' && selectedImage.hasTextRegions
              ? onRerenderImage
              : undefined
          }
          titlePrefix="Gallery manga page"
        />
      )}

      {/* Interactive Typesetter & Visual Editor Modal */}
      {editingImage && (
        <MangaEditorModal
          image={editingImage}
          onClose={() => {
            setEditingImage(null);
            onCloseOverlay?.();
          }}
          onSave={(updated) => {
            onUpdateImage?.(updated);
            const title = (updated.mangaTitle || editingImage?.mangaTitle || 'Ungrouped').trim() || 'Ungrouped';
            const existing = mangaImages[title] || [];
            const next = existing
              .map((candidate) => candidate.id === updated.id || candidate.folder === updated.folder ? updated : candidate)
              .filter((candidate) => !reviewOnly || candidate.reviewStatus === 'pending');
            setMangaImages((previous) => ({ ...previous, [title]: next }));
            if (selectedImage && selectedImage.id === updated.id) {
              setSelectedImage(updated);
            }
            setEditingImage(null);
            if (reviewOnly && next.length === 0) {
              onCloseMangaDetail?.();
            } else {
              onCloseOverlay?.();
            }
          }}
        />
      )}

      {/* Manga Reader Modal (Infinite Scroll & Single Page Modes) */}
      {(initialReaderMangaId || initialReaderManga) && !readingManga && (
        <div className="fixed inset-0 z-[100] grid place-items-center bg-zinc-950 text-zinc-100" role="status" aria-label="Opening reader">
          <Icon icon="carbon:renew" className="h-6 w-6 animate-spin" />
        </div>
      )}
      {readingManga && (
        <MangaReaderModal
          key={readingManga.groupId}
          mangaId={readingManga.groupId}
          mangaTitle={readingManga.title}
          images={readingManga.images}
          initialPageIndex={readingManga.initialPageIndex}
          series={readingManga.series}
          isLoadingManga={Boolean(readerLoadingTitle)}
          seriesError={readerLoadError}
          onSelectManga={handleSelectSeriesMember}
          onClose={(lastPageIndex, lastImageId) => {
            const finalIndex = typeof lastPageIndex === 'number' && lastPageIndex >= 0
              ? lastPageIndex
              : (() => {
                  const p = getStoredMangaReadProgress(readingManga.title, readingManga.images.length);
                  return p.page ? p.page - 1 : 0;
                })();
            const finalImageId = lastImageId || readingManga.images[finalIndex]?.id;
            setLastExitedReadPosition({
              mangaTitle: readingManga.title,
              mangaId: readingManga.groupId,
              pageIndex: finalIndex,
              imageId: finalImageId,
            });
            setReadingManga(null);
            onCloseOverlay?.();
          }}
          onEditImage={(img) => {
            const imgIndex = readingManga.images.findIndex((item) => item.id === img.id);
            setLastExitedReadPosition({
              mangaTitle: readingManga.title,
              mangaId: readingManga.groupId,
              pageIndex: imgIndex >= 0 ? imgIndex : 0,
              imageId: img.id,
            });
            setReadingManga(null);
            if (img.sourceType === 'original') return;
            if (onOpenPageEdit && img.folder) {
              onOpenPageEdit(img.folder);
            } else {
              setEditingImage(img);
            }
          }}
        />
      )}

      {/* Floating Selection Dock for Series Actions */}
      {selectedMangaIds.size > 0 && !currentSingleGroup && (
        <div
          role="region"
          aria-label="Series selection toolbar"
          className="fixed bottom-3 sm:bottom-6 left-1/2 -translate-x-1/2 z-40 w-[calc(100%-1.25rem)] sm:w-[calc(100%-2rem)] max-w-lg transition-all"
        >
          <div className="flex items-center justify-between gap-2.5 sm:gap-3 rounded-2xl border border-indigo-200/80 bg-white/95 p-2.5 sm:p-3 shadow-2xl backdrop-blur-md dark:border-indigo-900/80 dark:bg-zinc-900/95 dark:shadow-indigo-950/40">
            <div className="flex items-center gap-2 sm:gap-2.5 min-w-0">
              <span className="flex h-7 w-7 sm:h-8 sm:w-8 shrink-0 items-center justify-center rounded-xl bg-indigo-600 text-white font-bold text-xs shadow-xs">
                {selectedMangaIds.size}
              </span>
              <div className="min-w-0">
                <p className="text-xs font-semibold text-zinc-900 dark:text-zinc-100 truncate">
                  {selectedMangaIds.size} {selectedMangaIds.size === 1 ? 'manga' : 'manga'} selected
                </p>
                <p className="text-[10px] sm:text-[11px] text-zinc-500 dark:text-zinc-400 truncate">
                  {selectedMangaIds.size < 2 ? 'Add to series or select more to create new' : 'Ready to create or add to series'}
                </p>
              </div>
            </div>

            <div className="flex items-center gap-1.5 sm:gap-2 shrink-0">
              <button
                type="button"
                onClick={() => setSelectedMangaIds(new Map())}
                className="rounded-xl px-2.5 py-1.5 text-xs font-medium text-zinc-600 hover:bg-zinc-100 dark:text-zinc-400 dark:hover:bg-zinc-800 transition-colors cursor-pointer touch-manipulation"
                title="Clear selection (Esc)"
              >
                Clear
              </button>
              {(onDeleteManga || onDeleteMangas) && (
                <button
                  type="button"
                  onClick={() => setConfirmDeleteSelectedMangas(true)}
                  disabled={selectedMangaIds.size === 0}
                  className="flex items-center gap-1.5 rounded-xl bg-red-600 px-3 sm:px-3.5 py-2 text-xs font-semibold text-white shadow-md shadow-red-600/20 hover:bg-red-500 disabled:cursor-not-allowed disabled:opacity-50 transition-all cursor-pointer touch-manipulation"
                  title={`Delete ${selectedMangaIds.size} selected manga`}
                >
                  <Icon icon="carbon:trash-can" className="h-4 w-4" />
                  <span>Delete</span>
                </button>
              )}
              <button
                type="button"
                onClick={() => {
                  setSeriesError(null);
                  if (selectedMangaIds.size === 1) {
                    setSeriesModalMode('add');
                  } else {
                    setSeriesModalMode('create');
                    setNewSeriesTitle(getSuggestedSeriesTitle(selectedMangaIds));
                  }
                  void loadGalleryAllSeries();
                  setIsCreateSeriesOpen(true);
                }}
                disabled={selectedMangaIds.size === 0}
                className="flex items-center gap-1.5 rounded-xl bg-indigo-600 px-3 sm:px-3.5 py-2 text-xs font-semibold text-white shadow-md shadow-indigo-600/20 hover:bg-indigo-500 disabled:cursor-not-allowed disabled:opacity-50 transition-all cursor-pointer touch-manipulation"
                title="Create a new series or add selected manga to an existing series"
              >
                <Icon icon="carbon:folders" className="h-4 w-4" />
                <span>Series</span>
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Floating Success Toast for Series Creation */}
      {createdSeriesToast && (
        <div
          role="status"
          aria-live="polite"
          className="fixed bottom-3 sm:bottom-6 right-3 sm:right-6 z-50 w-[calc(100%-1.5rem)] sm:w-auto sm:max-w-sm transition-all"
        >
          <div className="flex items-start justify-between gap-3 rounded-2xl border border-emerald-200/80 bg-white/95 p-3 sm:p-3.5 shadow-2xl backdrop-blur-md dark:border-emerald-900/80 dark:bg-zinc-900/95">
            <div className="flex items-start gap-2.5 min-w-0">
              <span className="flex h-7 w-7 shrink-0 items-center justify-center rounded-xl bg-emerald-600 text-white shadow-xs">
                <Icon icon="carbon:checkmark" className="h-4 w-4" />
              </span>
              <div className="min-w-0">
                <p className="text-xs font-semibold text-zinc-900 dark:text-zinc-100">
                  Series created
                </p>
                <p className="text-xs text-zinc-600 dark:text-zinc-300 truncate" title={createdSeriesToast.title}>
                  “{createdSeriesToast.title}” ({createdSeriesToast.count} manga)
                </p>
                {onOpenSeriesDetail && (
                  <button
                    type="button"
                    onClick={() => {
                      const id = createdSeriesToast.seriesId;
                      setCreatedSeriesToast(null);
                      onOpenSeriesDetail(id);
                    }}
                    className="mt-1.5 inline-flex items-center gap-1 text-xs font-semibold text-indigo-600 hover:text-indigo-500 dark:text-indigo-400 dark:hover:text-indigo-300 transition-colors cursor-pointer touch-manipulation"
                  >
                    <span>View series</span>
                    <Icon icon="carbon:arrow-right" className="h-3 w-3" />
                  </button>
                )}
              </div>
            </div>
            <button
              type="button"
              onClick={() => setCreatedSeriesToast(null)}
              className="rounded-lg p-1 text-zinc-400 hover:bg-zinc-100 hover:text-zinc-600 dark:hover:bg-zinc-800 dark:hover:text-zinc-200 transition-colors cursor-pointer touch-manipulation"
              aria-label="Dismiss notification"
            >
              <Icon icon="carbon:close" className="h-4 w-4" />
            </button>
          </div>
        </div>
      )}

      {/* Assign / Move to Series Modal */}
      {assigningManga && (
        <AssignToSeriesModal
          isOpen={Boolean(assigningManga)}
          onClose={() => setAssigningManga(null)}
          mangaId={assigningManga.id}
          mangaTitle={assigningManga.title}
          currentSeriesId={assigningManga.seriesId}
          currentSeriesTitle={assigningManga.seriesTitle}
          onSeriesAssigned={async () => {
            await onSeriesChanged?.();
          }}
          onSeriesRemoved={async () => {
            await onSeriesChanged?.();
          }}
        />
      )}

    </div>
  );
};
 
