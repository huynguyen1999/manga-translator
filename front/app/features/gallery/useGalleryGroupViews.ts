import { useMemo } from 'react';
import type { MangaGroupSummary } from '@/types';
import type { GalleryMangaGroup, MangaStatusFilter } from '@/utils/resultGallery';
import { filterMangaGroupsByStatus } from '@/utils/resultGallery';

interface GalleryGroupViewOptions {
  mangaGroups: GalleryMangaGroup[];
  mangaSearchQuery: string;
  moveMangaSearch: string;
  activeMangaFilter: string;
  statusFilter: MangaStatusFilter;
  onGallerySearchChange?: (search: string) => void;
  totalMangaCount: number;
  requestedGalleryPageSize: number;
  reviewOnly: boolean;
  totalImagesCount: number;
  activeSummaries: MangaGroupSummary[];
}

export function useGalleryGroupViews({
  mangaGroups,
  mangaSearchQuery,
  moveMangaSearch,
  activeMangaFilter,
  statusFilter,
  onGallerySearchChange,
  totalMangaCount,
  requestedGalleryPageSize,
  reviewOnly,
  totalImagesCount,
  activeSummaries,
}: GalleryGroupViewOptions) {
  const searchedMangaGroups = useMemo(() => {
    const q = mangaSearchQuery.trim().toLowerCase();
    if (!q) return mangaGroups;
    return mangaGroups.filter((g) => g.title.toLowerCase().includes(q));
  }, [mangaGroups, mangaSearchQuery]);

  const moveMangaGroups = useMemo(() => {
    const q = moveMangaSearch.trim().toLowerCase();
    if (!q) return mangaGroups;
    return mangaGroups.filter((g) => g.title.toLowerCase().includes(q));
  }, [mangaGroups, moveMangaSearch]);

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
  const reviewCount = reviewOnly
    ? totalImagesCount
    : activeSummaries.reduce((total, group) => total + (group.needsReviewCount || 0), 0);

  return {
    moveMangaGroups,
    filteredGroups,
    visibleGroups: filteredGroups,
    galleryMangaCount,
    galleryPageCount,
    reviewCount,
  };
}
