import { useEffect, useState, type Dispatch, type SetStateAction } from 'react';
import type { GallerySort } from '@/utils/routeState';
import type { MangaStatusFilter } from '@/utils/resultGallery';

interface GalleryFilterOptions {
  requestedGallerySort: GallerySort;
  galleryStatus?: MangaStatusFilter;
  reviewOnly: boolean;
  selectedMangaTitle?: string | null;
  gallerySearch: string;
  setGalleryPage: Dispatch<SetStateAction<number>>;
  onGalleryPageChange?: (page: number) => void;
  onGallerySearchChange?: (search: string) => void;
  onGalleryStatusChange?: (status: MangaStatusFilter) => void;
  onGalleryReviewChange?: (pending: boolean) => void;
  onCloseMangaDetail?: () => void;
}

export function useGalleryFilters({
  requestedGallerySort,
  galleryStatus,
  reviewOnly,
  selectedMangaTitle,
  gallerySearch,
  setGalleryPage,
  onGalleryPageChange,
  onGallerySearchChange,
  onGalleryStatusChange,
  onGalleryReviewChange,
  onCloseMangaDetail,
}: GalleryFilterOptions) {
  const [sortBy, setSortBy] = useState<GallerySort>(requestedGallerySort);
  const [statusFilter, setStatusFilter] = useState<MangaStatusFilter>(galleryStatus || (reviewOnly ? 'review' : 'all'));
  const [activeMangaFilter, setActiveMangaFilter] = useState<string>(selectedMangaTitle || 'all');
  const [mangaSearchQuery, setMangaSearchQuery] = useState(gallerySearch);
  const [mangaSearchInput, setMangaSearchInput] = useState(gallerySearch);
  const [viewMode] = useState<'cards' | 'rows'>('cards');

  useEffect(() => {
    if (galleryStatus) {
      setStatusFilter(galleryStatus);
    } else if (reviewOnly) {
      setStatusFilter('review');
    } else {
      setStatusFilter('all');
    }
  }, [galleryStatus, reviewOnly]);

  useEffect(() => {
    setMangaSearchQuery(gallerySearch);
    setMangaSearchInput(gallerySearch);
  }, [gallerySearch]);

  useEffect(() => {
    setSortBy(requestedGallerySort);
  }, [requestedGallerySort]);

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

  const closeMangaDetail = () => {
    if (onCloseMangaDetail) {
      onCloseMangaDetail();
    } else {
      setActiveMangaFilter('all');
    }
  };

  const handleSearchSubmit = (query: string) => {
    const search = query.trim();
    if (search === mangaSearchQuery.trim()) return;
    setMangaSearchQuery(search);
    setGalleryPage(1);
    if (onGallerySearchChange) {
      onGallerySearchChange(search);
    } else {
      setGalleryPage(1);
      onGalleryPageChange?.(1);
    }
    if (search && activeMangaFilter !== 'all') {
      closeMangaDetail();
    }
  };

  return {
    sortBy,
    setSortBy,
    statusFilter,
    activeMangaFilter,
    setActiveMangaFilter,
    mangaSearchQuery,
    mangaSearchInput,
    setMangaSearchInput,
    viewMode,
    handleStatusFilterChange,
    closeMangaDetail,
    handleSearchSubmit,
  };
}
