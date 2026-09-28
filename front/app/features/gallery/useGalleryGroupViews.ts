import { useMemo } from 'react';
import type { MangaGroupSummary } from '@/types';
import type { GalleryMangaGroup, MangaStatusFilter } from '@/utils/resultGallery';
import { filterMangaGroupsByStatus, matchesSearchQuery } from '@/utils/resultGallery';

interface GalleryGroupViewOptions {
  mangaGroups: GalleryMangaGroup[];
  mangaSearchQuery: string;
  moveMangaSearch: string;
  activeMangaFilter: string;
  statusFilter: MangaStatusFilter | string;
  minPages?: number;
  maxPages?: number;
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
  minPages,
  maxPages,
  onGallerySearchChange,
  totalMangaCount,
  requestedGalleryPageSize,
  reviewOnly,
  totalImagesCount,
  activeSummaries,
}: GalleryGroupViewOptions) {
  const searchedMangaGroups = useMemo(() => {
    if (!mangaSearchQuery.trim()) return mangaGroups;
    return mangaGroups.filter((g) => matchesSearchQuery(g.title, mangaSearchQuery));
  }, [mangaGroups, mangaSearchQuery]);

  const moveMangaGroups = useMemo(() => {
    if (!moveMangaSearch.trim()) return mangaGroups;
    return mangaGroups.filter((g) => matchesSearchQuery(g.title, moveMangaSearch));
  }, [mangaGroups, moveMangaSearch]);

  const filteredGroups = useMemo(() => {
    const base = mangaSearchQuery.trim() && !onGallerySearchChange ? searchedMangaGroups : mangaGroups;
    const isFiltered = (statusFilter && statusFilter !== 'all') || minPages != null || maxPages != null;
    const statusFiltered = isFiltered ? filterMangaGroupsByStatus(base, statusFilter, minPages, maxPages) : base;
    return activeMangaFilter === 'all'
      ? statusFiltered
      : statusFiltered.filter((g) => g.title === activeMangaFilter);
  }, [mangaGroups, searchedMangaGroups, activeMangaFilter, mangaSearchQuery, statusFilter, minPages, maxPages, onGallerySearchChange]);

  const hasFilters = activeMangaFilter !== 'all' || mangaSearchQuery.trim() || (statusFilter && statusFilter !== 'all') || minPages != null || maxPages != null;
  const galleryMangaCount = totalMangaCount > 0 ? totalMangaCount : (hasFilters ? filteredGroups.length : mangaGroups.length);
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
