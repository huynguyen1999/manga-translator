import { useEffect, useRef } from "react";
import type { Dispatch, SetStateAction } from "react";
import type { FinishedImage, SeriesDetail, SeriesMember } from "@/types";
import { mangaIdForTitle } from "@/utils/routeState";
import { getStoredMangaReadProgress, sortMangaPages } from "@/utils/resultGallery";
import { fetchGroupSeries } from "@/utils/series";

export type ReadingMangaState = {
  groupId: string;
  title: string;
  images: FinishedImage[];
  initialPageIndex?: number;
  series: SeriesDetail | null;
};

export type ExitedReadPosition = {
  mangaTitle: string;
  mangaId?: string;
  pageIndex: number;
  imageId?: string;
};

type MangaGroup = { id: string; title: string; isLoaded: boolean };
type LoadMangaImages = (title: string, detail?: string, groupId?: string, force?: boolean) => Promise<FinishedImage[]>;

type MangaReaderActionsOptions = {
  mangaGroups: MangaGroup[];
  mangaImages: Record<string, FinishedImage[]>;
  loadMangaImagesIfNeeded: LoadMangaImages;
  readingManga: ReadingMangaState | null;
  setReadingManga: Dispatch<SetStateAction<ReadingMangaState | null>>;
  readerLoadingTitle: string | null;
  setReaderLoadingTitle: Dispatch<SetStateAction<string | null>>;
  setReaderLoadError: Dispatch<SetStateAction<string | null>>;
  setLastExitedReadPosition: Dispatch<SetStateAction<ExitedReadPosition | null>>;
  setEditingImage: Dispatch<SetStateAction<FinishedImage | null>>;
  onOpenPageEdit?: (folder: string) => void;
  onCloseOverlay?: () => void;
  initialReaderManga?: string | null;
  onOpenReader?: (mangaId: string, initialPageIndex?: number, replace?: boolean) => void;
};

export const useMangaReaderActions = ({
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
}: MangaReaderActionsOptions) => {
  const previousReadingManga = useRef<ReadingMangaState | null>(null);

  useEffect(() => {
    if (previousReadingManga.current && !readingManga) {
      const exited = previousReadingManga.current;
      const progress = getStoredMangaReadProgress(exited.title, exited.images.length);
      const targetIndex = progress.page ? progress.page - 1 : 0;
      const targetImage = exited.images[targetIndex];
      setLastExitedReadPosition((previous) => previous ?? {
        mangaTitle: exited.title,
        mangaId: exited.groupId,
        pageIndex: targetIndex,
        imageId: targetImage?.id,
      });
    }
    previousReadingManga.current = readingManga;
  }, [readingManga]);

  const handleReadManga = async (
    groupTitle: string,
    existingImages?: FinishedImage[],
    initialPageIndex?: number,
    requestedGroupId?: string,
    updateRoute = true,
  ) => {
    if (readerLoadingTitle) return;
    setReaderLoadingTitle(groupTitle);
    setReaderLoadError(null);

    try {
      const group = requestedGroupId
        ? mangaGroups.find((item) => item.id === requestedGroupId)
        : mangaGroups.find((item) => item.title === groupTitle);
      const groupId = requestedGroupId || group?.id || mangaIdForTitle(groupTitle);
      let images = existingImages;
      if (!group?.isLoaded || !images || images.length === 0) {
        images = await loadMangaImagesIfNeeded(groupTitle, "reader", groupId);
      }
      const resolvedTitle = groupTitle || images?.[0]?.mangaTitle || initialReaderManga || "Manga";
      if (images && images.length > 0) {
        if (updateRoute && onOpenReader) {
          onOpenReader(groupId, initialPageIndex);
        }
        setReadingManga({
          groupId,
          title: resolvedTitle,
          images: sortMangaPages(images),
          initialPageIndex,
          series: null,
        });
        void fetchGroupSeries(groupId)
          .then((series) => setReadingManga((current) =>
            current?.groupId === groupId ? { ...current, series } : current,
          ))
          .catch(() => {});
      } else {
        setReaderLoadError(groupTitle);
      }
    } finally {
      setReaderLoadingTitle(null);
    }
  };

  const handleSelectSeriesMember = async (member: SeriesMember) => {
    if (!readingManga || readerLoadingTitle) return;
    setReaderLoadingTitle(member.title);
    setReaderLoadError(null);
    try {
      const images = await loadMangaImagesIfNeeded(member.title, "reader", member.id);
      if (!images.length) {
        setReaderLoadError(member.title);
        return;
      }
      onOpenReader?.(member.id, 0, true);
      setReadingManga({
        groupId: member.id,
        title: member.title,
        images: sortMangaPages(images),
        initialPageIndex: 0,
        series: readingManga.series,
      });
    } finally {
      setReaderLoadingTitle(null);
    }
  };

  const handleCloseReader = (lastPageIndex?: number, lastImageId?: string) => {
    if (!readingManga) return;
    const finalIndex = typeof lastPageIndex === "number" && lastPageIndex >= 0
      ? lastPageIndex
      : (() => {
          const progress = getStoredMangaReadProgress(readingManga.title, readingManga.images.length);
          return progress.page ? progress.page - 1 : 0;
        })();
    const finalImageId = lastImageId || readingManga.images[finalIndex]?.id;
    setLastExitedReadPosition({
      mangaTitle: readingManga.title,
      mangaId: readingManga.groupId,
      pageIndex: finalIndex,
      imageId: finalImageId,
    });
    setReadingManga(null);
    onCloseOverlay?.();
  };

  const handleEditReaderImage = (image: FinishedImage) => {
    if (!readingManga) return;
    const imageIndex = readingManga.images.findIndex((item) => item.id === image.id);
    setLastExitedReadPosition({
      mangaTitle: readingManga.title,
      mangaId: readingManga.groupId,
      pageIndex: imageIndex >= 0 ? imageIndex : 0,
      imageId: image.id,
    });
    setReadingManga(null);
    if (image.sourceType === "original") return;
    if (onOpenPageEdit && image.folder) {
      onOpenPageEdit(image.folder);
    } else {
      setEditingImage(image);
    }
  };

  useEffect(() => {
    const members = readingManga?.series?.members;
    if (!members || members.length < 2) return;
    const index = members.findIndex((member) => member.id === readingManga.groupId);
    for (const member of [members[index - 1], members[index + 1]]) {
      if (member && !mangaImages[member.title]) {
        void loadMangaImagesIfNeeded(member.title, "reader", member.id);
      }
    }
  }, [readingManga?.groupId, readingManga?.series, mangaImages]);

  return { handleReadManga, handleSelectSeriesMember, handleCloseReader, handleEditReaderImage };
};
