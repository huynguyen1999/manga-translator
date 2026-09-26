import type { Dispatch, SetStateAction } from "react";
import type { FinishedImage } from "@/types";
import type { GalleryMangaGroup } from "@/utils/resultGallery";

type GalleryBulkDeletionOptions = {
  selectedImageIds: Set<string>;
  selectedMangaIds: Map<string, string>;
  allLoadedImages: FinishedImage[];
  currentSingleGroup: Pick<GalleryMangaGroup, "images"> | null;
  finishedImages: FinishedImage[];
  mangaGroups: GalleryMangaGroup[];
  mangaImages: Record<string, FinishedImage[]>;
  activeMangaFilter: string;
  onDeleteImages?: (images: FinishedImage[]) => void | Promise<void>;
  onDeleteImage?: (image: FinishedImage) => void;
  onDeleteMangas?: (mangaList: Array<{ title: string; images: FinishedImage[] }>) => void | Promise<void>;
  onDeleteManga?: (images: FinishedImage[], mangaTitle?: string) => void | Promise<void>;
  clearSelectedImages: () => void;
  closeMangaDetail: () => void;
  setMangaImages: Dispatch<SetStateAction<Record<string, FinishedImage[]>>>;
  setSelectedMangaIds: Dispatch<SetStateAction<Map<string, string>>>;
  setConfirmDeleteSelectedPages: Dispatch<SetStateAction<boolean>>;
  setConfirmDeleteSelectedMangas: Dispatch<SetStateAction<boolean>>;
  setIsDeletingSelectedPages: Dispatch<SetStateAction<boolean>>;
  setIsDeletingSelectedMangas: Dispatch<SetStateAction<boolean>>;
};

export const createGalleryBulkDeletionActions = ({
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
}: GalleryBulkDeletionOptions) => {
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

      clearSelectedImages();
      setConfirmDeleteSelectedPages(false);
    } catch (error) {
      window.alert(error instanceof Error ? error.message : 'Could not delete selected pages.');
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

  return { handleDeleteSelectedPages, handleDeleteSelectedMangas };
};
