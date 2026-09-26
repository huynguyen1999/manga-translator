import type { FinishedImage, MangaGroupSummary, MangaSummary } from '@/types';
import { apiUrl } from './api';
import { mangaIdForTitle, type MangaStatusFilter } from './routeState';

export type { MangaStatusFilter } from './routeState';

export interface MangaReadProgress {
  page: number | null;
  complete: boolean;
}

export type PageSortOption = 'order' | 'name-asc' | 'name-desc' | 'created-asc' | 'created-desc';

type SortOption = 'alpha-asc' | 'alpha-desc' | 'date-desc' | 'date-asc';
type SummaryAvailabilityState = 'loading' | 'summarized' | 'not-summarized' | 'queued' | 'generating' | 'paused' | 'stale' | 'error' | 'unavailable';

// Natural sort comparison (e.g. page_1 before page_10)
export const naturalCompare = (a: string, b: string): number => {
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

export const loadedThumbnailUrls = new Set<string>();
const blobUrlCache = new WeakMap<Blob, string>();

export function getGalleryThumbnailUrl(
  image: FinishedImage | null | undefined,
  variant: 'detail' | 'cover' | boolean = 'detail'
): string | null {
  if (!image) return null;
  const preferThumbnail = variant !== false;
  const preferredUrl = variant === 'cover' ? image.coverUrl : image.detailPreviewUrl;
  if (preferThumbnail && preferredUrl) {
    return apiUrl(preferredUrl);
  }
  if (preferThumbnail && image.folder) {
    return apiUrl(`/result/${image.folder}/${variant === 'cover' ? 'cover.webp' : 'thumbnail.webp'}`);
  }
  if (typeof image.result === 'string') {
    return apiUrl(image.result);
  }
  if (image.result instanceof Blob) {
    if (image.result.size < 1000 && image.folder) {
      return apiUrl(`/result/${image.folder}/${preferThumbnail ? (variant === 'cover' ? 'cover.webp' : 'thumbnail.webp') : 'final.png'}`);
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
  variant: 'detail' | 'cover' = 'detail',
): string | null {
  if (!image) return null;
  if (variant === 'cover') {
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

export type GalleryMangaGroup = {
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
};

export function buildGalleryMangaGroups({
  activeSummaries,
  hasExplicitSummaries,
  mangaImages,
  finishedImages,
  activeMangaFilter,
  loadingManga,
  sortBy,
  locallySummarizedTitle,
  serverSummarizedTitle,
  reviewOnly,
}: {
  activeSummaries: MangaGroupSummary[];
  hasExplicitSummaries: boolean;
  mangaImages: Record<string, FinishedImage[]>;
  finishedImages: FinishedImage[];
  activeMangaFilter: string;
  loadingManga: Record<string, boolean>;
  sortBy: SortOption;
  locallySummarizedTitle?: string;
  serverSummarizedTitle?: string;
  reviewOnly: boolean;
}): GalleryMangaGroup[] {
  const summaryMap = new Map<string, MangaGroupSummary>();
  activeSummaries.forEach((summary) => {
    summaryMap.set(summary.title, summary);
  });

  const keys = buildMangaGroupTitles({
    activeSummaries,
    hasExplicitSummaries,
    mangaImages,
    finishedImages,
    activeMangaFilter,
  });

  const groups: GalleryMangaGroup[] = [];
  keys.forEach((key) => {
    const summary = summaryMap.get(key);
    const loaded = mangaImages[key];
    const sessionExtra = finishedImages.filter(
      (image) => ((image.mangaTitle || 'Ungrouped').trim() || 'Ungrouped') === key
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

    groups.push({
      id: summary?.id || mangaIdForTitle(key),
      title: key,
      count: reviewOnly ? needsReviewCount : Math.max(count, images.length),
      coverImage: cover,
      images,
      latestFinishedAt,
      seriesId: summary?.seriesId || null,
      seriesTitle: summary?.seriesTitle || null,
      hasSummary: Boolean(
        summary?.hasSummary || locallySummarizedTitle === key || serverSummarizedTitle === key
      ),
      needsReviewCount,
      isLoaded,
      isLoading: Boolean(loadingManga[key]) || (!isLoaded && key === activeMangaFilter),
    });
  });

  return sortMangaGroups(reviewOnly ? groups.filter((group) => group.needsReviewCount > 0) : groups, sortBy);
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

export function getMangaReadProgress(
  savedPage: string | null,
  savedPageCount: string | null,
  pageCount: number,
): MangaReadProgress {
  const page = Number(savedPage);
  if (pageCount <= 0 || Number(savedPageCount) !== pageCount || !Number.isFinite(page) || page < 1) {
    return { page: null, complete: false };
  }

  const lastPage = Math.min(page, pageCount);
  return { page: lastPage, complete: lastPage >= pageCount };
}

export function getStoredMangaReadProgress(title: string, pageCount: number): MangaReadProgress {
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
}

export function getLastReadPageIndex(
  savedPage: string | null,
  savedPageCount: string | null,
  pageCount: number,
): number | null {
  const progress = getMangaReadProgress(savedPage, savedPageCount, pageCount);
  return progress.page !== null ? progress.page - 1 : null;
}

export function mergeGalleryImages(
  loaded: FinishedImage[],
  sessionImages: FinishedImage[]
): FinishedImage[] {
  const loadedKeys = new Set(
    loaded.map((image) => image.folder ? `folder:${image.folder}` : `id:${image.id}`)
  );

  return [
    ...loaded,
    ...sessionImages.filter((image) =>
      !loadedKeys.has(image.folder ? `folder:${image.folder}` : `id:${image.id}`)
    ),
  ];
}
