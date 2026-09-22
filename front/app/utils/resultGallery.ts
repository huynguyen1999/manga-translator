import type { FinishedImage } from "@/types";

export interface MangaReadProgress {
  page: number | null;
  complete: boolean;
}

export function getMangaReadProgress(
  savedPage: string | null,
  savedPageCount: string | null,
  pageCount: number,
): MangaReadProgress {
  const page = Number(savedPage);
  if (pageCount <= 0 || Number(savedPageCount) !== pageCount || !Number.isFinite(page) || page < 1) {
    return { page: null, complete: false };
  }

  const lastPage = Math.min(page, pageCount);
  return { page: lastPage, complete: lastPage >= pageCount };
}

export function getLastReadPageIndex(
  savedPage: string | null,
  savedPageCount: string | null,
  pageCount: number,
): number | null {
  const progress = getMangaReadProgress(savedPage, savedPageCount, pageCount);
  return progress.page !== null ? progress.page - 1 : null;
}

export function mergeGalleryImages(
  loaded: FinishedImage[],
  sessionImages: FinishedImage[]
): FinishedImage[] {
  const loadedKeys = new Set(
    loaded.map((image) => image.folder ? `folder:${image.folder}` : `id:${image.id}`)
  );

  return [
    ...loaded,
    ...sessionImages.filter((image) =>
      !loadedKeys.has(image.folder ? `folder:${image.folder}` : `id:${image.id}`)
    ),
  ];
}
