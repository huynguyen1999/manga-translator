import type { Dispatch, SetStateAction } from "react";
import type { FinishedImage } from "@/types";

type MovePagesOptions = {
  singleImageToMove: FinishedImage | null;
  selectedImageIds: Set<string>;
  allLoadedImages: FinishedImage[];
  onUpdateMangaTitle?: (pageIds: string[], newTitle: string, oldTitle?: string, groupId?: string, folders?: string[]) => void;
  clearSelectedImages: () => void;
  setMangaImages: Dispatch<SetStateAction<Record<string, FinishedImage[]>>>;
  setSingleImageToMove: Dispatch<SetStateAction<FinishedImage | null>>;
  setIsMoveModalOpen: Dispatch<SetStateAction<boolean>>;
  setTargetMangaName: Dispatch<SetStateAction<string>>;
};

export const createGalleryMoveActions = ({
  singleImageToMove,
  selectedImageIds,
  allLoadedImages,
  onUpdateMangaTitle,
  clearSelectedImages,
  setMangaImages,
  setSingleImageToMove,
  setIsMoveModalOpen,
  setTargetMangaName,
}: MovePagesOptions) => ({
  handleMoveSelected: (targetTitle: string) => {
    const cleanTitle = targetTitle.trim() || "Ungrouped";
    const targetImages = singleImageToMove
      ? [singleImageToMove]
      : allLoadedImages.filter((image) => selectedImageIds.has(image.id));
    const pageIds = targetImages.map((image) => image.id).filter(Boolean);
    const folders = targetImages.map((image) => image.folder || image.id).filter(Boolean);

    if (pageIds.length > 0) {
      onUpdateMangaTitle?.(pageIds, cleanTitle, undefined, undefined, folders);
      setMangaImages((previous) => {
        const next = { ...previous };
        const movedIds = new Set(targetImages.map((image) => image.id));
        Object.keys(next).forEach((title) => {
          next[title] = next[title].filter((image) => !movedIds.has(image.id));
        });
        if (next[cleanTitle]) {
          const updatedTargetImages = targetImages.map((image) => ({ ...image, mangaTitle: cleanTitle }));
          next[cleanTitle] = [...updatedTargetImages, ...next[cleanTitle]];
        }
        return next;
      });
    }

    clearSelectedImages();
    setSingleImageToMove(null);
    setIsMoveModalOpen(false);
    setTargetMangaName("");
  },
});
