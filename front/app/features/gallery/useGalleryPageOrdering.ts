import { useMemo, type Dispatch, type SetStateAction } from 'react';
import type { FinishedImage } from '@/types';
import { sortMangaPagesForOrder, type PageSortOption } from '@/utils/resultGallery';
import { createPageOrderActions } from './pageOrderActions';

type MangaGroup = { id: string; title: string; images: FinishedImage[] };

interface GalleryPageOrderingOptions {
  currentSingleGroup: MangaGroup | null;
  onReorderMangaPages?: (groupId: string, pageIds: string[]) => Promise<void>;
  pageSort: PageSortOption;
  setPageSort: Dispatch<SetStateAction<PageSortOption>>;
  setMangaImages: Dispatch<SetStateAction<Record<string, FinishedImage[]>>>;
  setPageOrderError: Dispatch<SetStateAction<string | null>>;
  setReorderingGroupId: Dispatch<SetStateAction<string | null>>;
  reviewOnly: boolean;
}

export function useGalleryPageOrdering({
  currentSingleGroup,
  onReorderMangaPages,
  pageSort,
  setPageSort,
  setMangaImages,
  setPageOrderError,
  setReorderingGroupId,
  reviewOnly,
}: GalleryPageOrderingOptions) {
  const { handlePageDrop, handlePageSort, handleSavePageSort } = createPageOrderActions({
    currentSingleGroup,
    onReorderMangaPages,
    pageSort,
    setPageSort,
    setMangaImages,
    setPageOrderError,
    setReorderingGroupId,
  });

  const duplicateSinglePageNames = useMemo(() => {
    if (!currentSingleGroup) return new Set<string>();
    const counts = new Map<string, number>();
    currentSingleGroup.images.forEach((image) => {
      const key = image.originalName.toLocaleLowerCase();
      counts.set(key, (counts.get(key) || 0) + 1);
    });
    return new Set([...counts].filter(([, count]) => count > 1).map(([name]) => name));
  }, [currentSingleGroup]);

  const displayedPageImages = useMemo(() => {
    if (!currentSingleGroup) return [];
    return sortMangaPagesForOrder(currentSingleGroup.images, pageSort);
  }, [currentSingleGroup, pageSort]);

  const pageSortIsDirty = Boolean(
    currentSingleGroup &&
    pageSort !== 'order' &&
    displayedPageImages.some((image, index) => image.id !== currentSingleGroup.images[index]?.id),
  );

  return {
    handlePageDrop,
    handlePageSort,
    handleSavePageSort,
    duplicateSinglePageNames,
    displayedPageImages,
    pageSortIsDirty,
    canReorderCurrentGroup: Boolean(onReorderMangaPages && !reviewOnly),
  };
}
