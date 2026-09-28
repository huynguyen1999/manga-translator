import { useEffect, useState, type Dispatch, type SetStateAction } from 'react';
import type { GallerySort } from '@/utils/routeState';
import type { MangaStatusFilter } from '@/utils/resultGallery';

interface GalleryFilterOptions {
  requestedGallerySort: GallerySort; galleryStatus?: MangaStatusFilter | string;
  galleryMinPages?: number; galleryMaxPages?: number; reviewOnly: boolean;
  selectedMangaTitle?: string | null; gallerySearch: string;
  setGalleryPage: Dispatch<SetStateAction<number>>;
  onGalleryPageChange?: (page: number) => void; onGallerySearchChange?: (search: string) => void;
  onGalleryStatusChange?: (status: string) => void;
  onGalleryPageRangeChange?: (minPages?: number, maxPages?: number) => void;
  onGalleryReviewChange?: (pending: boolean) => void; onCloseMangaDetail?: () => void;
}

export function useGalleryFilters({
  requestedGallerySort, galleryStatus, galleryMinPages, galleryMaxPages, reviewOnly,
  selectedMangaTitle, gallerySearch, setGalleryPage, onGalleryPageChange, onGallerySearchChange,
  onGalleryStatusChange, onGalleryPageRangeChange, onGalleryReviewChange, onCloseMangaDetail,
}: GalleryFilterOptions) {
  const [sortBy, setSortBy] = useState<GallerySort>(requestedGallerySort);
  const [statusFilter, setStatusFilter] = useState<string>(galleryStatus || (reviewOnly ? 'review' : 'all'));
  const [minPages, setMinPages] = useState<number | undefined>(galleryMinPages);
  const [maxPages, setMaxPages] = useState<number | undefined>(galleryMaxPages);
  const [activeMangaFilter, setActiveMangaFilter] = useState<string>(selectedMangaTitle || 'all');
  const [mangaSearchQuery, setMangaSearchQuery] = useState(gallerySearch);
  const [mangaSearchInput, setMangaSearchInput] = useState(gallerySearch);
  const [viewMode] = useState<'cards' | 'rows'>('cards');

  useEffect(() => { setStatusFilter(galleryStatus || (reviewOnly ? 'review' : 'all')); }, [galleryStatus, reviewOnly]);
  useEffect(() => { setMinPages(galleryMinPages); setMaxPages(galleryMaxPages); }, [galleryMinPages, galleryMaxPages]);
  useEffect(() => { setMangaSearchQuery(gallerySearch); setMangaSearchInput(gallerySearch); }, [gallerySearch]);
  useEffect(() => { setSortBy(requestedGallerySort); }, [requestedGallerySort]);

  const handleStatusFilterChange = (nextFilter: string) => {
    const clean = nextFilter || 'all';
    setStatusFilter(clean);
    setGalleryPage(1);
    if (onGalleryStatusChange) {
      onGalleryStatusChange(clean);
    } else if (clean.split(',').includes('review')) {
      onGalleryReviewChange?.(true);
    } else if (reviewOnly) {
      onGalleryReviewChange?.(false);
    }
  };

  const handleSourceChange = (source: 'all' | 'translated' | 'original') => {
    const currentTokens = (statusFilter && statusFilter !== 'all') ? statusFilter.split(',').map(s => s.trim()).filter(Boolean) : [];
    const nonSourceTokens = currentTokens.filter(t => t !== 'translated' && t !== 'original');
    if (source !== 'all') nonSourceTokens.push(source);
    handleStatusFilterChange(nonSourceTokens.length > 0 ? nonSourceTokens.join(',') : 'all');
  };

  const toggleStatusFlag = (flag: 'summarized' | 'review') => {
    const currentTokens = (statusFilter && statusFilter !== 'all') ? statusFilter.split(',').map(s => s.trim()).filter(Boolean) : [];
    const nextTokens = currentTokens.includes(flag) ? currentTokens.filter(t => t !== flag) : [...currentTokens, flag];
    handleStatusFilterChange(nextTokens.length > 0 ? nextTokens.join(',') : 'all');
  };

  const handlePageRangeChange = (nextMin?: number, nextMax?: number) => {
    setMinPages(nextMin);
    setMaxPages(nextMax);
    setGalleryPage(1);
    onGalleryPageRangeChange?.(nextMin, nextMax);
  };

  const closeMangaDetail = () => {
    if (onCloseMangaDetail) onCloseMangaDetail();
    else setActiveMangaFilter('all');
  };

  const handleSearchSubmit = (query: string) => {
    const search = query.trim();
    if (search === mangaSearchQuery.trim()) return;
    setMangaSearchQuery(search);
    setGalleryPage(1);
    if (onGallerySearchChange) onGallerySearchChange(search);
    else {
      setGalleryPage(1);
      onGalleryPageChange?.(1);
    }
    if (search && activeMangaFilter !== 'all' && !onGallerySearchChange) closeMangaDetail();
  };

  const handleClearFilters = () => {
    handleStatusFilterChange('all');
    handlePageRangeChange(undefined, undefined);
    setMangaSearchInput('');
    handleSearchSubmit('');
  };

  return {
    sortBy, setSortBy, statusFilter, minPages, maxPages, activeMangaFilter, setActiveMangaFilter,
    mangaSearchQuery, mangaSearchInput, setMangaSearchInput, viewMode, handleStatusFilterChange,
    handleSourceChange, toggleStatusFlag, handlePageRangeChange, handleClearFilters, closeMangaDetail, handleSearchSubmit,
  };
}
