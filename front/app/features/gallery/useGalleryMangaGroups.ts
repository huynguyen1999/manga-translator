import { useEffect, useMemo } from 'react';
import type { FinishedImage, MangaGroupSummary } from '@/types';
import type { GallerySort } from '@/utils/routeState';
import { buildGalleryMangaGroups, sortMangaPages } from '@/utils/resultGallery';
import type { MangaSummaryModalState } from './MangaSummaryModal';
import type { SummaryAvailabilityState } from './useMangaSummaryActions';

interface GalleryMangaGroupOptions {
  activeSummaries: MangaGroupSummary[];
  hasExplicitSummaries: boolean;
  mangaImages: Record<string, FinishedImage[]>;
  finishedImages: FinishedImage[];
  activeMangaFilter: string;
  loadingManga: Record<string, boolean>;
  sortBy: GallerySort;
  summaryState: MangaSummaryModalState | null;
  summaryAvailability: { title: string; state: SummaryAvailabilityState } | null;
  reviewOnly: boolean;
  galleryRevision: number;
  totalGalleryCount: number;
  selectedImage: FinishedImage | null;
  loadMangaImagesIfNeeded: (
    title: string,
    detail?: string,
    requestedGroupId?: string,
    force?: boolean,
  ) => Promise<FinishedImage[]>;
}

export function useGalleryMangaGroups({
  activeSummaries,
  hasExplicitSummaries,
  mangaImages,
  finishedImages,
  activeMangaFilter,
  loadingManga,
  sortBy,
  summaryState,
  summaryAvailability,
  reviewOnly,
  galleryRevision,
  totalGalleryCount,
  selectedImage,
  loadMangaImagesIfNeeded,
}: GalleryMangaGroupOptions) {
  const mangaGroups = useMemo(() => buildGalleryMangaGroups({
    activeSummaries,
    hasExplicitSummaries,
    mangaImages,
    finishedImages,
    activeMangaFilter,
    loadingManga,
    sortBy,
    locallySummarizedTitle: summaryState?.data?.summary ? summaryState.title : undefined,
    serverSummarizedTitle: summaryAvailability?.state === 'summarized' ? summaryAvailability.title : undefined,
    reviewOnly,
  }), [activeSummaries, hasExplicitSummaries, mangaImages, finishedImages, loadingManga, sortBy, activeMangaFilter, summaryState, summaryAvailability, reviewOnly]);

  useEffect(() => {
    if (activeMangaFilter !== 'all') {
      loadMangaImagesIfNeeded(activeMangaFilter, undefined, undefined, galleryRevision > 0);
    }
  }, [activeMangaFilter, galleryRevision]);

  useEffect(() => {
    if (mangaGroups.length === 1 && !mangaImages[mangaGroups[0].title]) {
      loadMangaImagesIfNeeded(mangaGroups[0].title);
    }
  }, [mangaGroups, mangaImages]);

  const allLoadedImages = useMemo(() => {
    const list: FinishedImage[] = [];
    mangaGroups.forEach((group) => list.push(...group.images));
    return list;
  }, [mangaGroups]);

  const totalImagesCount = useMemo(() => {
    if (typeof totalGalleryCount === 'number' && totalGalleryCount > 0) {
      return totalGalleryCount;
    }
    return mangaGroups.reduce((acc, group) => acc + group.count, 0);
  }, [totalGalleryCount, mangaGroups]);

  const currentModalImages = useMemo(() => {
    if (!selectedImage) return [];
    const targetGroupTitle = (selectedImage.mangaTitle || 'Ungrouped').trim() || 'Ungrouped';
    const group = mangaGroups.find((item) => item.title === targetGroupTitle);
    if (group && group.images.length > 0) return group.images;
    return allLoadedImages.length > 0 ? sortMangaPages(allLoadedImages) : [selectedImage];
  }, [selectedImage, mangaGroups, allLoadedImages]);

  return { mangaGroups, allLoadedImages, totalImagesCount, currentModalImages };
}
