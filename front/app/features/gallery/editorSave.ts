import type { Dispatch, SetStateAction } from "react";
import type { FinishedImage } from "@/types";

type ApplyGalleryEditorSaveOptions = {
  editingImage: FinishedImage | null;
  mangaImages: Record<string, FinishedImage[]>;
  selectedImage: FinishedImage | null;
  reviewOnly: boolean;
  setMangaImages: Dispatch<SetStateAction<Record<string, FinishedImage[]>>>;
  setSelectedImage: Dispatch<SetStateAction<FinishedImage | null>>;
  setEditingImage: Dispatch<SetStateAction<FinishedImage | null>>;
  onUpdateImage?: (image: FinishedImage) => void;
  onCloseMangaDetail?: () => void;
  onCloseOverlay?: () => void;
};

export function applyGalleryEditorSave(
  updated: FinishedImage,
  {
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
  }: ApplyGalleryEditorSaveOptions,
): void {
  onUpdateImage?.(updated);
  const title = (updated.mangaTitle || editingImage?.mangaTitle || "Ungrouped").trim() || "Ungrouped";
  const existing = mangaImages[title] || [];
  const next = existing
    .map((candidate) => candidate.id === updated.id || candidate.folder === updated.folder ? updated : candidate)
    .filter((candidate) => !reviewOnly || candidate.reviewStatus === "pending");
  setMangaImages((previous) => ({ ...previous, [title]: next }));
  if (selectedImage && selectedImage.id === updated.id) {
    setSelectedImage(updated);
  }
  setEditingImage(null);
  if (reviewOnly && next.length === 0) {
    onCloseMangaDetail?.();
  } else {
    onCloseOverlay?.();
  }
}
