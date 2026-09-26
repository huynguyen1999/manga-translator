import type { Dispatch, SetStateAction } from "react";
import type { FinishedImage } from "@/types";
import { sortMangaPagesForOrder, type PageSortOption } from "@/utils/resultGallery";

type MangaGroup = { id: string; title: string; images: FinishedImage[] };

type PageOrderActionsOptions = {
  currentSingleGroup: MangaGroup | null;
  onReorderMangaPages?: (groupId: string, pageIds: string[]) => Promise<void>;
  pageSort: PageSortOption;
  setPageSort: Dispatch<SetStateAction<PageSortOption>>;
  setMangaImages: Dispatch<SetStateAction<Record<string, FinishedImage[]>>>;
  setPageOrderError: Dispatch<SetStateAction<string | null>>;
  setReorderingGroupId: Dispatch<SetStateAction<string | null>>;
};

export const createPageOrderActions = ({
  currentSingleGroup,
  onReorderMangaPages,
  pageSort,
  setPageSort,
  setMangaImages,
  setPageOrderError,
  setReorderingGroupId,
}: PageOrderActionsOptions) => {
  const handlePageDrop = async (
    group: MangaGroup,
    sourceId: string,
    targetId: string,
  ) => {
    if (!onReorderMangaPages || !sourceId || sourceId === targetId) return;
    const sourceIndex = group.images.findIndex((image) => image.id === sourceId);
    const targetIndex = group.images.findIndex((image) => image.id === targetId);
    if (sourceIndex < 0 || targetIndex < 0) return;

    const previousImages = group.images;
    const reordered = [...group.images];
    const [moved] = reordered.splice(sourceIndex, 1);
    reordered.splice(targetIndex, 0, moved);
    const optimistic = reordered.map((image, index) => ({ ...image, pageOrder: index + 1 }));
    setMangaImages((previous) => ({ ...previous, [group.title]: optimistic }));
    setPageOrderError(null);
    setReorderingGroupId(group.id);
    try {
      await onReorderMangaPages(group.id, optimistic.map((image) => image.id));
    } catch (error) {
      setMangaImages((previous) => ({ ...previous, [group.title]: previousImages }));
      setPageOrderError(error instanceof Error ? error.message : "Could not save page order.");
    } finally {
      setReorderingGroupId(null);
    }
  };

  const handlePageSort = (sortMode: PageSortOption) => {
    setPageSort(sortMode);
    setPageOrderError(null);
  };

  const handleSavePageSort = async () => {
    if (!currentSingleGroup || !onReorderMangaPages || pageSort === "order") return;

    const previousImages = currentSingleGroup.images;
    const sorted = sortMangaPagesForOrder(previousImages, pageSort);
    if (sorted.every((image, index) => image.id === previousImages[index]?.id)) {
      setPageSort("order");
      return;
    }

    const optimistic = sorted.map((image, index) => ({ ...image, pageOrder: index + 1 }));
    setMangaImages((previous) => ({ ...previous, [currentSingleGroup.title]: optimistic }));
    setPageOrderError(null);
    setReorderingGroupId(currentSingleGroup.id);
    try {
      await onReorderMangaPages(currentSingleGroup.id, optimistic.map((image) => image.id));
      setPageSort("order");
    } catch (error) {
      setMangaImages((previous) => ({ ...previous, [currentSingleGroup.title]: previousImages }));
      setPageOrderError(error instanceof Error ? error.message : "Could not save page order.");
    } finally {
      setReorderingGroupId(null);
    }
  };

  return { handlePageDrop, handlePageSort, handleSavePageSort };
};
