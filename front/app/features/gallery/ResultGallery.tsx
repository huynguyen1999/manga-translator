import React, { useState, useEffect, useLayoutEffect, useRef, useMemo, useCallback } from 'react';
import { Icon } from '@iconify/react';
import { Link, useLocation } from 'react-router';
import type {
  FinishedImage,
  MangaGroupSummary,
  MangaSummary,
} from '@/types';
import PreviewImage from '@/components/PreviewImage';
import { PageDetailModal } from '@/components/PageDetailModal';
import { GalleryCard } from '@/features/gallery/GalleryCard';
import { GalleryBulkSelectionBar } from '@/features/gallery/GalleryBulkSelectionBar';
import { GalleryLibraryView } from '@/features/gallery/GalleryLibraryView';
import { GalleryOverlays } from '@/features/gallery/GalleryOverlays';
import { CreateSeriesModal } from '@/features/gallery/CreateSeriesModal';
import { GalleryToolbar } from '@/features/gallery/GalleryToolbar';
import { type MangaSummaryModalState } from '@/features/gallery/MangaSummaryModal';
import { useMangaSummaryActions, type SummaryAvailabilityState } from '@/features/gallery/useMangaSummaryActions';
import { MangaDetailHeader } from '@/features/gallery/MangaDetailHeader';
import { MangaPagesGrid } from '@/features/gallery/MangaPagesGrid';
import {
  GalleryEmptyState,
  GalleryLoadingState,
  GalleryReviewClearState,
} from '@/features/gallery/GalleryStatusStates';
import { createGalleryBulkDeletionActions } from '@/features/gallery/bulkDeletionActions';
import { useGalleryPageOrdering } from '@/features/gallery/useGalleryPageOrdering';
import { useGalleryCardActions } from '@/features/gallery/useGalleryCardActions';
import { createGalleryRenameActions } from '@/features/gallery/renameActions';
import { createGalleryMoveActions } from '@/features/gallery/movePages';
import { downloadGalleryImage, downloadMangaCbzArchive } from '@/features/gallery/downloads';
import { applyGalleryEditorSave } from '@/features/gallery/editorSave';
import { usePageDragAutoScroll } from '@/features/gallery/usePageDragAutoScroll';
import { useGallerySelection } from '@/features/gallery/useGallerySelection';
import { useGalleryPageModalRoutes } from '@/features/gallery/useGalleryPageModalRoutes';
import { useMangaImages } from '@/features/gallery/useMangaImages';
import { useMangaReaderActions, type ExitedReadPosition, type ReadingMangaState } from '@/features/gallery/useMangaReaderActions';
import { useRestoreReaderScroll } from '@/features/gallery/useRestoreReaderScroll';
import { useGalleryKeyboard } from '@/features/gallery/useGalleryKeyboard';
import { useGallerySeriesActions } from '@/features/gallery/useGallerySeriesActions';
import { useGalleryFallbackSummaries } from '@/features/gallery/useGalleryFallbackSummaries';
import { useGalleryFilters } from '@/features/gallery/useGalleryFilters';
import { useGalleryMangaGroups } from '@/features/gallery/useGalleryMangaGroups';
import { useGalleryGroupViews } from '@/features/gallery/useGalleryGroupViews';
import { SeriesLibrary } from '@/components/SeriesLibrary';
import { Pagination } from '@/components/Pagination';
import {
  buildGalleryMangaGroups,
  buildMangaGroupTitles,
  calculateDragAutoScrollSpeed,
  filterMangaGroupsByStatus,
  getGalleryPageCorrection,
  getGalleryFallbackUrl,
  getGalleryThumbnailUrl,
  getStoredMangaReadProgress,
  getMangaSummaryAvailability,
  getSuggestedSeriesTitle,
  isSummaryPending,
  loadedThumbnailUrls,
  mergeGalleryImages,
  shouldShowEmptyLibraryState,
  sortMangaGroups,
  sortMangaPages,
  sortMangaPagesForOrder,
  type PageSortOption,
} from '@/utils/resultGallery';
import type { MangaStatusFilter } from '@/utils/resultGallery';
export type { MangaStatusFilter, PageSortOption } from '@/utils/resultGallery';
export {
  buildGalleryMangaGroups,
  buildMangaGroupTitles,
  calculateDragAutoScrollSpeed,
  filterMangaGroupsByStatus,
  getGalleryFallbackUrl,
  getGalleryPageCorrection,
  getGalleryThumbnailUrl,
  getMangaSummaryAvailability,
  getSuggestedSeriesTitle,
  isSummaryPending,
  loadedThumbnailUrls,
  shouldShowEmptyLibraryState,
  sortMangaGroups,
  sortMangaPages,
  sortMangaPagesForOrder,
};
import { apiUrl } from '@/utils/api';
import { MANGA_TITLE_MAX_LENGTH } from '@/config';
import { buildMangaDetailIdUrl, DEFAULT_GALLERY_PAGE_SIZE, GALLERY_PAGE_SIZE_OPTIONS, getNavigationOrigin, mangaIdForTitle, type GallerySection, type GallerySort } from '@/utils/routeState';

export { useGalleryThumbnail } from '@/features/gallery/useGalleryThumbnail';

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
  onDeleteImage?: (image: FinishedImage) => void;
  onDeleteImages?: (images: FinishedImage[]) => void | Promise<void>;
  onDeleteManga?: (images: FinishedImage[], mangaTitle?: string) => void | Promise<void>;
  onDeleteMangas?: (mangaList: Array<{ title: string; images: FinishedImage[] }>) => void | Promise<void>;
  onReorderMangaPages?: (groupId: string, pageIds: string[]) => Promise<void>;
  onRestoreBatchPages?: (groupId: string, title: string) => Promise<number>;
  onUpdateImage?: (image: FinishedImage) => void;
  onUpdateMangaTitle?: (pageIds: string[], newMangaTitle: string, oldMangaTitle?: string, groupId?: string, folders?: string[]) => void;
  onOpenPageView?: (folder: string) => void;
  onOpenPageEdit?: (folder: string) => void;
  onRetryImage?: (image: FinishedImage, fromStage?: string) => void | Promise<void>;
  onRetryFromStage?: (image: FinishedImage, stageId: string) => void | Promise<void>;
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

// ──────────────────────────────────────────────────────────────
// Manga Card — Visual card for a manga collection in gallery view
// ──────────────────────────────────────────────────────────────

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
  onDeleteImage,
  onDeleteImages,
  onDeleteManga,
  onDeleteMangas,
  onReorderMangaPages,
  onRestoreBatchPages,
  onUpdateImage,
  onUpdateMangaTitle,
  onOpenPageView,
  onOpenPageEdit,
  onRetryImage,
  onRetryFromStage,
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
  const location = useLocation();
  const pageViewState = useMemo(() => ({
    from: getNavigationOrigin(location.pathname, location.search, location.state?.from),
  }), [location.pathname, location.search, location.state]);

  const [selectedImage, setSelectedImage] = useState<FinishedImage | null>(null);
  const [editingImage, setEditingImage] = useState<FinishedImage | null>(null);
  const [isModalOpen, setIsModalOpen] = useState(false);
  const [zoomLevel, setZoomLevel] = useState(1);
  const modalContentRef = useRef<HTMLDivElement>(null);

  const [galleryPage, setGalleryPage] = useState(() => Math.max(1, requestedGalleryPage));
  const {
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
  } = useGalleryFilters({
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
  });

  // Detailed images loaded per manga on-demand when uncollapsed
  const [restoringBatchPages, setRestoringBatchPages] = useState<string | null>(null);
  const [restoreBatchMessage, setRestoreBatchMessage] = useState<string | null>(null);
  const [draggedPageId, setDraggedPageId] = useState<string | null>(null);
  const [dragOverPageId, setDragOverPageId] = useState<string | null>(null);
  const [reorderingGroupId, setReorderingGroupId] = useState<string | null>(null);
  const [pageSort, setPageSort] = useState<PageSortOption>('order');
  const [pageOrderError, setPageOrderError] = useState<string | null>(null);
  const { fallbackSummaries, isFallbackLoading } = useGalleryFallbackSummaries(mangaSummaries, isLoading);

  const stopPageDragAutoScroll = usePageDragAutoScroll(draggedPageId, setDraggedPageId, setDragOverPageId);

  const activeSummaries = useMemo(() => {
    return mangaSummaries !== undefined ? mangaSummaries : fallbackSummaries;
  }, [mangaSummaries, fallbackSummaries]);

  const {
    expandedGroups,
    setExpandedGroups,
    mangaImages,
    setMangaImages,
    loadingManga,
    loadMangaImagesIfNeeded,
  } = useMangaImages(activeSummaries, reviewOnly, galleryRevision);

  // Selection state for moving/reorganizing pages
  const {
    selectedImageIds,
    toggleSelectImage,
    toggleSelectAllInGroup,
    clearSelectedImages,
  } = useGallerySelection();
  const [isMoveModalOpen, setIsMoveModalOpen] = useState(false);
  const [targetMangaName, setTargetMangaName] = useState('');
  const [moveMangaSearch, setMoveMangaSearch] = useState('');
  const [singleImageToMove, setSingleImageToMove] = useState<FinishedImage | null>(null);

  useEffect(() => {
    if (isMoveModalOpen) setMoveMangaSearch('');
  }, [isMoveModalOpen]);

  // Manga groups selected for a new series; IDs survive pagination and search changes.
  const [selectedMangaIds, setSelectedMangaIds] = useState<Map<string, string>>(new Map());
  const {
    isCreateSeriesOpen,
    setIsCreateSeriesOpen,
    newSeriesTitle,
    setNewSeriesTitle,
    seriesError,
    setSeriesError,
    isCreatingSeries,
    openSeriesAfterCreate,
    setOpenSeriesAfterCreate,
    createSeriesDraggedId,
    setCreateSeriesDraggedId,
    createSeriesDragOverId,
    setCreateSeriesDragOverId,
    createdSeriesToast,
    setCreatedSeriesToast,
    seriesModalMode,
    setSeriesModalMode,
    isLoadingExistingSeries,
    existingSeriesSearch,
    setExistingSeriesSearch,
    targetExistingSeriesId,
    setTargetExistingSeriesId,
    loadGalleryAllSeries,
    filteredExistingSeries,
    moveSelectedManga,
    dropSelectedManga,
    sortSelectedManga,
    handleCreateSeries,
    handleAddToExistingSeries,
  } = useGallerySeriesActions({
    selectedMangaIds,
    setSelectedMangaIds,
    onSeriesChanged,
    onOpenSeriesDetail,
  });
  const [assigningManga, setAssigningManga] = useState<{
    id: string;
    title: string;
    seriesId?: string | null;
    seriesTitle?: string | null;
  } | null>(null);

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
  const [readingManga, setReadingManga] = useState<ReadingMangaState | null>(null);
  const [lastExitedReadPosition, setLastExitedReadPosition] = useState<ExitedReadPosition | null>(null);
  const [highlightedImageId, setHighlightedImageId] = useState<string | null>(null);
  const [highlightedMangaId, setHighlightedMangaId] = useState<string | null>(null);
  const [readerLoadingTitle, setReaderLoadingTitle] = useState<string | null>(null);
  const [readerLoadError, setReaderLoadError] = useState<string | null>(null);
  const [summaryState, setSummaryState] = useState<MangaSummaryModalState | null>(null);
  const [summaryAvailability, setSummaryAvailability] = useState<{
    title: string;
    state: SummaryAvailabilityState;
  } | null>(null);
  const [summarizingTitle, setSummarizingTitle] = useState<string | null>(null);
  const [summaryCopied, setSummaryCopied] = useState(false);
  useGalleryKeyboard({
    summaryOpen: Boolean(summaryState),
    createSeriesOpen: isCreateSeriesOpen,
    moveMangaOpen: isMoveModalOpen,
    deleteMangaOpen: Boolean(confirmDeleteManga),
    deletePagesOpen: confirmDeleteSelectedPages,
    deleteMangasOpen: confirmDeleteSelectedMangas,
    selectedMangaCount: selectedMangaIds.size,
    setSummaryState,
    setCreateSeriesOpen: setIsCreateSeriesOpen,
    setMoveMangaOpen: setIsMoveModalOpen,
    setDeleteManga: setConfirmDeleteManga,
    setDeletePagesOpen: setConfirmDeleteSelectedPages,
    setDeleteMangasOpen: setConfirmDeleteSelectedMangas,
    setSelectedMangaIds,
  });

  // Lock body scroll when synopsis modal is active to prevent scroll contention and lag
  useEffect(() => {
    if (!summaryState) return;
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    return () => {
      document.body.style.overflow = previousOverflow;
    };
  }, [Boolean(summaryState)]);


  const effectiveIsLoading = isLoading !== undefined ? isLoading : isFallbackLoading;

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
  const {
    mangaGroups,
    allLoadedImages,
    totalImagesCount,
    currentModalImages,
  } = useGalleryMangaGroups({
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
  });

  const toggleGroupCollapse = (title: string) => {
    const willBeExpanded = !expandedGroups[title];
    setExpandedGroups((prev) => ({ ...prev, [title]: willBeExpanded }));
    if (willBeExpanded) {
      loadMangaImagesIfNeeded(title);
    }
  };

  const requestRerender = useCallback((images: FinishedImage[]) => {
    if (!onRerenderImages) return;
    void Promise.resolve(onRerenderImages(images)).catch((error) => {
      window.alert(error instanceof Error ? error.message : 'Could not queue rerender.');
    });
  }, [onRerenderImages]);

  const { handleStartRename, handleSaveRename } = createGalleryRenameActions({
    mangaGroups,
    renameInputValue,
    activeMangaFilter,
    onUpdateMangaTitle,
    onOpenMangaDetail,
    setActiveMangaFilter,
    setMangaImages,
    setExpandedGroups,
    setSelectedMangaIds,
    setRenamingManga,
    setRenameInputValue,
  });

  const toggleMangaSeriesSelection = (groupId: string) => {
    const group = mangaGroups.find((item) => item.id === groupId);
    if (!group) return;
    setSelectedMangaIds((previous) => {
      const next = new Map(previous);
      next.has(groupId) ? next.delete(groupId) : next.set(groupId, group.title);
      return next;
    });
  };

  const { handleMoveSelected } = createGalleryMoveActions({
    singleImageToMove,
    selectedImageIds,
    allLoadedImages,
    onUpdateMangaTitle,
    clearSelectedImages,
    setMangaImages,
    setSingleImageToMove,
    setIsMoveModalOpen,
    setTargetMangaName,
  });

  const { handleReadManga, handleSelectSeriesMember, handleCloseReader, handleEditReaderImage } = useMangaReaderActions({
    mangaGroups,
    mangaImages,
    loadMangaImagesIfNeeded,
    readingManga,
    setReadingManga,
    readerLoadingTitle,
    setReaderLoadingTitle,
    setReaderLoadError,
    setLastExitedReadPosition,
    setEditingImage,
    onOpenPageEdit,
    onCloseOverlay,
    initialReaderManga,
    onOpenReader,
  });

  const {
    handleSummarize,
    handleViewSummary,
    copySummary,
    handleResumeSummaryJob,
    handlePauseSummaryJob,
    handleStopSummaryJob,
  } = useMangaSummaryActions({
    summaryModel,
    mangaGroups,
    summaryState,
    setSummaryState,
    summaryAvailability,
    setSummaryAvailability,
    summarizingTitle,
    setSummarizingTitle,
    setSummaryCopied,
  });

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
    }
  };

  useGalleryPageModalRoutes({
    initialPageViewFolder,
    initialPageEditFolder,
    finishedImages,
    mangaImages,
    selectedImage,
    isModalOpen,
    editingImage,
    setSelectedImage,
    setIsModalOpen,
    setEditingImage,
  });

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

  const effectiveSingleMangaTitle = activeMangaFilter !== 'all' ? activeMangaFilter : null;
  const currentSingleGroup = effectiveSingleMangaTitle
    ? mangaGroups.find((g) => g.title === effectiveSingleMangaTitle) || null
    : null;

  const {
    handleDeleteSelectedPages,
    handleDeleteSelectedMangas,
  } = createGalleryBulkDeletionActions({
    selectedImageIds,
    selectedMangaIds,
    allLoadedImages,
    currentSingleGroup,
    finishedImages,
    mangaGroups,
    mangaImages,
    activeMangaFilter,
    onDeleteImages,
    onDeleteImage,
    onDeleteMangas,
    onDeleteManga,
    clearSelectedImages,
    closeMangaDetail,
    setMangaImages,
    setSelectedMangaIds,
    setConfirmDeleteSelectedPages,
    setConfirmDeleteSelectedMangas,
    setIsDeletingSelectedPages,
    setIsDeletingSelectedMangas,
  });

  const handleRestoreBatchPages = async () => {
    if (!currentSingleGroup || !onRestoreBatchPages) return;
    setRestoringBatchPages(currentSingleGroup.id);
    setRestoreBatchMessage(null);
    try {
      const count = await onRestoreBatchPages(currentSingleGroup.id, currentSingleGroup.title);
      setRestoreBatchMessage(`Restored ${count} existing batch ${count === 1 ? 'page' : 'pages'}.`);
    } catch (error) {
      setRestoreBatchMessage(error instanceof Error ? error.message : 'Could not restore batch pages.');
    } finally {
      setRestoringBatchPages(null);
    }
  };

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

  useRestoreReaderScroll({
    lastExitedReadPosition,
    setLastExitedReadPosition,
    activeMangaFilter,
    viewMode,
    expandedGroups,
    currentSingleGroupImages: currentSingleGroup?.images,
    setHighlightedImageId,
    setHighlightedMangaId,
  });

  const downloadSingle = downloadGalleryImage;

  const handleDownloadCbz = async (mangaTitle: string, images: FinishedImage[], original = false) => {
    setDownloadingCbz((prev) => ({ ...prev, [mangaTitle]: true }));
    const groupId = mangaGroups.find((group) => group.title === mangaTitle)?.id || images[0]?.groupId;
    try {
      await downloadMangaCbzArchive(mangaTitle, images, groupId, original);
    } catch (error) {
      console.error('Failed to download CBZ:', error);
      alert('Could not download CBZ archive from server.');
    } finally {
      setTimeout(() => {
        setDownloadingCbz((prev) => ({ ...prev, [mangaTitle]: false }));
      }, 1500);
    }
  };

  const {
    moveMangaGroups,
    filteredGroups,
    visibleGroups,
    galleryMangaCount,
    galleryPageCount,
    reviewCount,
  } = useGalleryGroupViews({
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
  });

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

  // Stable memoized callbacks for GalleryCard, MangaCard, and RowGroupCards.
  const {
    handleCardClick,
    handleCardDownload,
    handleCardDelete,
    handleCardEdit,
    handleCardMove,
    handleSingleGroupToggleSelect,
    handleSingleGroupReadFromHere,
    handleRowGroupReadFromHere,
    handleMangaCardRead,
    handleMangaCardSummarize,
    handleMangaCardViewSummary,
    handleMangaCardDownloadCbz,
    handleMangaCardStartRename,
    handleMangaCardDelete,
    handleMangaCardToggleSelect,
  } = useGalleryCardActions({
    onOpenPageView,
    openImageModal,
    downloadSingle,
    handleDeleteImage,
    onDeleteImage,
    onOpenPageEdit,
    setEditingImage,
    setSingleImageToMove,
    setIsMoveModalOpen,
    toggleSelectImage,
    currentSingleGroup,
    handleReadManga,
    handleSummarize,
    summaryModel,
    handleViewSummary,
    handleDownloadCbz,
    handleStartRename,
    setConfirmDeleteManga,
    mangaGroups,
    toggleMangaSeriesSelection,
  });

  const {
    handlePageDrop,
    handlePageSort,
    handleSavePageSort,
    duplicateSinglePageNames,
    displayedPageImages,
    pageSortIsDirty,
    canReorderCurrentGroup,
  } = useGalleryPageOrdering({
    currentSingleGroup,
    onReorderMangaPages,
    pageSort,
    setPageSort,
    setMangaImages,
    setPageOrderError,
    setReorderingGroupId,
    reviewOnly,
  });
  const rowGroupActions = {
    toggleGroupCollapse,
    setRenameInputValue,
    handleSaveRename,
    setRenamingManga,
    handleStartRename,
    setAssigningManga,
    handleToggleSelectAll,
    handleReadManga,
    handleSummarize,
    handleDownloadCbz,
    onDeleteManga,
    setConfirmDeleteManga,
    onMoveImage: handleCardMove,
    onReadFromHere: handleRowGroupReadFromHere,
    onClickImage: handleCardClick,
    onDownloadImage: handleCardDownload,
    onDeleteImage,
    handleCardDelete,
    onEditImage: handleCardEdit,
    onRerenderImage,
    toggleSelectImage,
  };

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

  if (initialPageViewFolder && (!isModalOpen || selectedImage?.folder !== initialPageViewFolder)) {
    return (
      <div className="fixed inset-0 z-[100] grid place-items-center bg-zinc-950 text-zinc-100" role="status" aria-label="Opening page">
        <Icon icon="carbon:renew" className="h-6 w-6 animate-spin" />
      </div>
    );
  }

  if (effectiveIsLoading && activeSummaries.length === 0) {
    return <GalleryLoadingState viewMode={viewMode} />;
  }

  if (reviewOnly && mangaGroups.length === 0 && totalImagesCount === 0 && !effectiveIsLoading) {
    return <GalleryReviewClearState onBackToGallery={() => onGalleryReviewChange?.(false)} />;
  }

  if (
    !initialPageViewFolder &&
    !initialPageEditFolder &&
    !initialReaderMangaId &&
    !initialReaderManga &&
    shouldShowEmptyLibraryState({
      mangaGroupsLength: mangaGroups.length,
      totalImagesCount,
      mangaSearchQuery,
      statusFilter,
      activeMangaFilter,
      reviewOnly,
    })
  ) {
    return <GalleryEmptyState />;
  }

  const showPageSelectionBar =
    selectedImageIds.size > 0 &&
    (Boolean(currentSingleGroup) || viewMode === 'rows');

  return (
    <div className={`space-y-6 ${showPageSelectionBar ? 'pb-36 sm:pb-28' : ''}`}>
      <GalleryToolbar
        effectiveIsLoading={effectiveIsLoading}
        totalImagesCount={totalImagesCount}
        galleryMangaCount={galleryMangaCount}
        reviewCount={reviewCount}
        reviewOnly={reviewOnly}
        onGalleryReviewChange={onGalleryReviewChange}
        statusFilter={statusFilter}
        handleStatusFilterChange={handleStatusFilterChange}
        mangaSearchInput={mangaSearchInput}
        setMangaSearchInput={setMangaSearchInput}
        handleSearchSubmit={handleSearchSubmit}
        sortBy={sortBy}
        setSortBy={setSortBy}
        setGalleryPage={setGalleryPage}
        onGallerySortChange={onGallerySortChange}
      />

      <CreateSeriesModal
        isCreateSeriesOpen={isCreateSeriesOpen}
        setIsCreateSeriesOpen={setIsCreateSeriesOpen}
        seriesModalMode={seriesModalMode}
        setSeriesModalMode={setSeriesModalMode}
        setSeriesError={setSeriesError}
        loadGalleryAllSeries={loadGalleryAllSeries}
        newSeriesTitle={newSeriesTitle}
        setNewSeriesTitle={setNewSeriesTitle}
        selectedMangaIds={selectedMangaIds}
        isCreatingSeries={isCreatingSeries}
        sortSelectedManga={sortSelectedManga}
        setCreateSeriesDraggedId={setCreateSeriesDraggedId}
        createSeriesDraggedId={createSeriesDraggedId}
        setCreateSeriesDragOverId={setCreateSeriesDragOverId}
        createSeriesDragOverId={createSeriesDragOverId}
        dropSelectedManga={dropSelectedManga}
        moveSelectedManga={moveSelectedManga}
        toggleMangaSeriesSelection={toggleMangaSeriesSelection}
        existingSeriesSearch={existingSeriesSearch}
        setExistingSeriesSearch={setExistingSeriesSearch}
        isLoadingExistingSeries={isLoadingExistingSeries}
        filteredExistingSeries={filteredExistingSeries}
        targetExistingSeriesId={targetExistingSeriesId}
        setTargetExistingSeriesId={setTargetExistingSeriesId}
        openSeriesAfterCreate={openSeriesAfterCreate}
        setOpenSeriesAfterCreate={setOpenSeriesAfterCreate}
        seriesError={seriesError}
        handleCreateSeries={handleCreateSeries}
        handleAddToExistingSeries={handleAddToExistingSeries}
      />

      {/* Single Manga Detail View */}
      {currentSingleGroup && (
        <div className="space-y-6">
          {/* Manga Header Banner */}
          <MangaDetailHeader
            currentSingleGroup={currentSingleGroup}
            closeMangaDetail={closeMangaDetail}
            renamingManga={renamingManga}
            renameInputValue={renameInputValue}
            setRenameInputValue={setRenameInputValue}
            handleSaveRename={handleSaveRename}
            setRenamingManga={setRenamingManga}
            handleStartRename={handleStartRename}
            setAssigningManga={setAssigningManga}
            summaryAvailability={summaryAvailability}
            onRestoreBatchPages={onRestoreBatchPages}
            handleRestoreBatchPages={handleRestoreBatchPages}
            restoringBatchPages={restoringBatchPages}
            handleToggleSelectAll={handleToggleSelectAll}
            selectedImageIds={selectedImageIds}
            onRerenderImages={onRerenderImages}
            requestRerender={requestRerender}
            handleReadManga={handleReadManga}
            readerLoadingTitle={readerLoadingTitle}
            readerLoadError={readerLoadError}
            handleSummarize={handleSummarize}
            summarizingTitle={summarizingTitle}
            handleDownloadCbz={handleDownloadCbz}
            downloadingCbz={downloadingCbz}
            onDeleteManga={onDeleteManga}
            setConfirmDeleteManga={setConfirmDeleteManga}
            restoreBatchMessage={restoreBatchMessage}
            reviewOnly={reviewOnly}
            handleCardEdit={handleCardEdit}
          />

          {/* Bulk Selection Action Bar inside single manga view */}
          {selectedImageIds.size > 0 && (
            <GalleryBulkSelectionBar
              selectedCount={selectedImageIds.size}
              onMove={() => {
                setSingleImageToMove(null);
                setIsMoveModalOpen(true);
              }}
              onDelete={onDeleteImage || onDeleteImages ? () => setConfirmDeleteSelectedPages(true) : undefined}
              onRerender={onRerenderImages ? () => requestRerender(currentSingleGroup.images.filter((image) => selectedImageIds.has(image.id))) : undefined}
              onClear={clearSelectedImages}
            />
          )}

          {/* Pages Grid */}
          <MangaPagesGrid
            currentSingleGroup={currentSingleGroup}
            canReorderCurrentGroup={canReorderCurrentGroup}
            pageSort={pageSort}
            reorderingGroupId={reorderingGroupId}
            handlePageSort={handlePageSort}
            handleSavePageSort={handleSavePageSort}
            pageSortIsDirty={pageSortIsDirty}
            pageOrderError={pageOrderError}
            displayedPageImages={displayedPageImages}
            setDraggedPageId={setDraggedPageId}
            setDragOverPageId={setDragOverPageId}
            draggedPageId={draggedPageId}
            dragOverPageId={dragOverPageId}
            stopPageDragAutoScroll={stopPageDragAutoScroll}
            handlePageDrop={handlePageDrop}
            duplicateSinglePageNames={duplicateSinglePageNames}
            highlightedImageId={highlightedImageId}
            selectedImageIds={selectedImageIds}
            handleSingleGroupToggleSelect={handleSingleGroupToggleSelect}
            handleCardMove={handleCardMove}
            handleSingleGroupReadFromHere={handleSingleGroupReadFromHere}
            handleCardClick={handleCardClick}
            handleCardDownload={handleCardDownload}
            onDeleteImage={onDeleteImage}
            handleCardDelete={handleCardDelete}
            handleCardEdit={handleCardEdit}
            onRerenderImage={onRerenderImage}
            pageViewState={pageViewState}
          />
        </div>
      )}

      {/* All Manga Library View */}
      {!currentSingleGroup && (
        <>
          <GalleryLibraryView
            viewMode={viewMode}
            filteredGroups={filteredGroups}
            visibleGroups={visibleGroups}
            searchQuery={mangaSearchQuery}
            statusFilter={statusFilter}
            onResetFilters={() => {
              setMangaSearchInput('');
              handleSearchSubmit('');
              handleStatusFilterChange('all');
              if (activeMangaFilter !== 'all') closeMangaDetail();
            }}
            bulkSelection={selectedImageIds.size > 0 ? {
              selectedCount: selectedImageIds.size,
              onMove: () => {
                setSingleImageToMove(null);
                setIsMoveModalOpen(true);
              },
              onDelete: onDeleteImage || onDeleteImages ? () => setConfirmDeleteSelectedPages(true) : undefined,
              onRerender: onRerenderImages ? () => requestRerender(allLoadedImages.filter((image) => selectedImageIds.has(image.id))) : undefined,
              onClear: clearSelectedImages,
            } : null}
            getCardProps={(group, index) => ({
              mangaId: group.id,
              title: group.title,
              count: group.count,
              needsReviewCount: group.needsReviewCount,
              reviewOnly,
              readProgress: getStoredMangaReadProgress(group.title, group.count),
              coverImage: group.coverImage,
              isDownloading: Boolean(downloadingCbz[group.title]),
              isReaderLoading: readerLoadingTitle === group.title,
              isPriority: index < 5,
              isSummarizing: summarizingTitle === group.title,
              hasSummary: group.hasSummary,
              onViewSummary: handleMangaCardViewSummary,
              detailLinkState: pageViewState,
              onRead: handleMangaCardRead,
              onSummarize: handleMangaCardSummarize,
              onDownloadCbz: handleMangaCardDownloadCbz,
              onStartRename: handleMangaCardStartRename,
              onDelete: onDeleteManga ? handleMangaCardDelete : undefined,
              seriesTitle: group.seriesTitle,
              isSelected: selectedMangaIds.has(group.id),
              isAssigned: Boolean(group.seriesId),
              isHighlighted: highlightedMangaId === group.id,
              onToggleSelect: handleMangaCardToggleSelect,
            })}
            getRowState={(group) => ({
              isCollapsed: !expandedGroups[group.title],
              isDownloading: Boolean(downloadingCbz[group.title]),
              isRenaming: renamingManga === group.title,
              allInGroupSelected: group.images.length > 0 && group.images.every((image) => selectedImageIds.has(image.id)),
              readProgress: getStoredMangaReadProgress(group.title, group.count),
              isRowHighlighted: highlightedMangaId === group.id,
              renameInputValue,
              selectedImageIds,
              highlightedImageId,
              readerLoadingTitle,
              readerLoadError,
              summarizingTitle,
              reviewOnly,
              pageViewState,
            })}
            rowGroupActions={rowGroupActions}
          />

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

      <GalleryOverlays
        deleteConfirmationProps={{
          confirmDeleteManga,
          deleteMangaPageCount: mangaGroups.find((group) => group.title === confirmDeleteManga)?.count || '',
          onCancelMangaDeletion: () => setConfirmDeleteManga(null),
          onConfirmMangaDeletion: () => {
            if (!confirmDeleteManga) return;
            const targetGroup = mangaGroups.find((group) => group.title === confirmDeleteManga);
            void handleDeleteMangaGroup(confirmDeleteManga, targetGroup?.images || []);
          },
          confirmDeleteSelectedPages,
          selectedPageIds: selectedImageIds,
          isDeletingSelectedPages,
          onCancelPageDeletion: () => setConfirmDeleteSelectedPages(false),
          onConfirmPageDeletion: () => void handleDeleteSelectedPages(),
          confirmDeleteSelectedMangas,
          selectedMangaIds,
          isDeletingSelectedMangas,
          onCancelMangaGroupDeletion: () => setConfirmDeleteSelectedMangas(false),
          onConfirmMangaGroupDeletion: () => void handleDeleteSelectedMangas(),
        }}
        moveToMangaProps={{
          isOpen: isMoveModalOpen,
          singleImageToMove,
          selectedPageCount: selectedImageIds.size,
          mangaGroups: moveMangaGroups,
          search: moveMangaSearch,
          onSearchChange: setMoveMangaSearch,
          targetMangaName,
          onTargetMangaNameChange: setTargetMangaName,
          maxTitleLength: MANGA_TITLE_MAX_LENGTH,
          onClose: () => setIsMoveModalOpen(false),
          onMove: handleMoveSelected,
        }}
        summaryProps={summaryState ? {
          summaryState,
          summaryCopied,
          isSummarizing: Boolean(summarizingTitle),
          detailUrl: buildMangaDetailIdUrl(
            summaryState.data?.groupId ||
              mangaGroups.find((group) => group.title === summaryState.title)?.id ||
              mangaIdForTitle(summaryState.title),
          ),
          onClose: () => setSummaryState(null),
          onCopy: () => void copySummary(),
          onResume: handleResumeSummaryJob,
          onPause: handlePauseSummaryJob,
          onStop: handleStopSummaryJob,
          onSummarize: (refreshText) => void handleSummarize(summaryState.title, true, refreshText),
        } : null}
        pageDetailProps={isModalOpen && selectedImage ? {
          image: selectedImage,
          onClose: closeImageModal,
          images: currentModalImages,
          currentIndex: Math.max(0, currentModalImages.findIndex((img) => img.id === selectedImage.id)),
          onNavigate: (index) => {
            if (currentModalImages[index]) {
              setSelectedImage(currentModalImages[index]);
              if (onOpenPageView && currentModalImages[index].folder) {
                onOpenPageView(currentModalImages[index].folder);
              }
            }
          },
          onDownload: downloadSingle,
          onDelete: onDeleteImage
            ? (toDelete) => {
                closeImageModal();
                onDeleteImage(toDelete);
              }
            : undefined,
          onEdit: selectedImage.hasTextRegions && selectedImage.sourceType !== 'original'
            ? (img) => {
                if (onOpenPageEdit && img.folder) {
                  onOpenPageEdit(img.folder);
                } else {
                  setEditingImage(img);
                }
              }
            : undefined,
          onRetry: onRetryImage && selectedImage.sourceType !== 'original' ? onRetryImage : undefined,
          onRetryFromStage: onRetryFromStage && selectedImage.sourceType !== 'original' ? onRetryFromStage : undefined,
          onRerender: onRerenderImage && selectedImage.sourceType !== 'original' && selectedImage.hasTextRegions
            ? onRerenderImage
            : undefined,
          titlePrefix: 'Gallery manga page',
        } : null}
        editorProps={editingImage ? {
          image: editingImage,
          onClose: () => {
            setEditingImage(null);
            onCloseOverlay?.();
          },
          onSave: (updated) => applyGalleryEditorSave(updated, {
            editingImage,
            mangaImages,
            selectedImage,
            reviewOnly,
            setMangaImages,
            setSelectedImage,
            setEditingImage,
            onUpdateImage,
            onCloseMangaDetail,
            onCloseOverlay,
          }),
        } : null}
        readerOpening={Boolean((initialReaderMangaId || initialReaderManga) && !readingManga)}
        readerKey={readingManga?.groupId}
        readerProps={readingManga ? {
          mangaId: readingManga.groupId,
          mangaTitle: readingManga.title,
          images: readingManga.images,
          initialPageIndex: readingManga.initialPageIndex,
          series: readingManga.series,
          isLoadingManga: Boolean(readerLoadingTitle),
          seriesError: readerLoadError,
          onSelectManga: handleSelectSeriesMember,
          onClose: handleCloseReader,
          onEditImage: handleEditReaderImage,
        } : null}
        seriesDockProps={{
          selectedCount: selectedMangaIds.size,
          isSingleMangaView: Boolean(currentSingleGroup),
          canDeleteSelection: Boolean(onDeleteManga || onDeleteMangas),
          createdSeriesToast,
          onClearSelection: () => setSelectedMangaIds(new Map()),
          onDeleteSelection: () => setConfirmDeleteSelectedMangas(true),
          onOpenSeriesManager: () => {
            setSeriesError(null);
            if (selectedMangaIds.size === 1) {
              setSeriesModalMode('add');
            } else {
              setSeriesModalMode('create');
              setNewSeriesTitle(getSuggestedSeriesTitle(selectedMangaIds));
            }
            void loadGalleryAllSeries();
            setIsCreateSeriesOpen(true);
          },
          onDismissToast: () => setCreatedSeriesToast(null),
          onOpenSeries: onOpenSeriesDetail ? (id) => {
            setCreatedSeriesToast(null);
            onOpenSeriesDetail(id);
          } : undefined,
        }}
        assignToSeriesProps={assigningManga ? {
          isOpen: Boolean(assigningManga),
          onClose: () => setAssigningManga(null),
          mangaId: assigningManga.id,
          mangaTitle: assigningManga.title,
          currentSeriesId: assigningManga.seriesId,
          currentSeriesTitle: assigningManga.seriesTitle,
          onSeriesAssigned: async () => { await onSeriesChanged?.(); },
          onSeriesRemoved: async () => { await onSeriesChanged?.(); },
        } : null}
      />

    </div>
  );
};
 
