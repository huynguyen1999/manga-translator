import { useCallback } from 'react';
import type { Dispatch, SetStateAction } from 'react';
import type { FinishedImage } from '@/types';

type SingleMangaGroup = { title: string; images: FinishedImage[] } | null;
type ReadManga = (
  title: string,
  images?: FinishedImage[],
  pageIndex?: number,
  groupId?: string,
  updateRoute?: boolean,
) => Promise<void>;

interface GalleryCardActionsOptions {
  onOpenPageView?: (folder: string) => void;
  openImageModal: (image: FinishedImage) => void;
  downloadSingle: (image: FinishedImage) => void | Promise<void>;
  handleDeleteImage: (image: FinishedImage) => void;
  onDeleteImage?: (image: FinishedImage) => void;
  onOpenPageEdit?: (folder: string) => void;
  setEditingImage: Dispatch<SetStateAction<FinishedImage | null>>;
  setSingleImageToMove: Dispatch<SetStateAction<FinishedImage | null>>;
  setIsMoveModalOpen: Dispatch<SetStateAction<boolean>>;
  toggleSelectImage: (id: string, images: FinishedImage[], shiftKey?: boolean) => void;
  currentSingleGroup: SingleMangaGroup;
  handleReadManga: ReadManga;
  handleSummarize: (title: string) => void | Promise<void>;
  summaryModel: string;
  handleViewSummary: (title: string) => void | Promise<void>;
  handleDownloadCbz: (title: string, images: FinishedImage[]) => void | Promise<void>;
  handleStartRename: (title: string) => void;
  setConfirmDeleteManga: Dispatch<SetStateAction<string | null>>;
  mangaGroups: Array<{ id: string; title: string }>;
  toggleMangaSeriesSelection: (groupId: string) => void;
}

export function useGalleryCardActions({
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
}: GalleryCardActionsOptions) {
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

  return {
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
  };
}
