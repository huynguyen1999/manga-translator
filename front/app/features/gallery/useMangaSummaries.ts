import { useCallback } from "react";
import type { Dispatch, SetStateAction } from "react";
import type { MangaGroupSummary } from "@/types";
import { DEFAULT_GALLERY_PAGE_SIZE, type ParsedRoute } from "@/utils/routeState";
import { apiUrl } from "@/utils/api";

type GalleryPage = {
  groups: MangaGroupSummary[];
  totalGroups: number;
  totalImages: number;
};

type MangaSummariesOptions = {
  parsedRoute: ParsedRoute;
  setMangaSummaries: Dispatch<SetStateAction<MangaGroupSummary[]>>;
  setTotalMangaCount: Dispatch<SetStateAction<number>>;
  setTotalGalleryCount: Dispatch<SetStateAction<number>>;
  setIsGalleryLoading: Dispatch<SetStateAction<boolean>>;
  galleryLoadRequestRef: { current: number };
  galleryPageCacheRef: { current: Map<string, GalleryPage> };
};

export const useMangaSummaries = ({
  parsedRoute,
  setMangaSummaries,
  setTotalMangaCount,
  setTotalGalleryCount,
  setIsGalleryLoading,
  galleryLoadRequestRef,
  galleryPageCacheRef,
}: MangaSummariesOptions) => {
  const effectiveMangaFilter = parsedRoute.overlay === "none"
    ? (parsedRoute.mangaId || parsedRoute.mangaTitle)
    : undefined;

  return useCallback(async (signal?: AbortSignal) => {
    if (parsedRoute.overlay !== "none") return;
    const requestId = ++galleryLoadRequestRef.current;
    const page = parsedRoute.galleryPage ?? 1;
    const pageSize = parsedRoute.galleryPageSize ?? DEFAULT_GALLERY_PAGE_SIZE;
    const offset = (page - 1) * pageSize;
    const mangaFilter = effectiveMangaFilter;
    const mangaQuery = mangaFilter ? `&mangaId=${encodeURIComponent(mangaFilter)}` : "";
    const searchQuery = parsedRoute.gallerySearch
      ? `&search=${encodeURIComponent(parsedRoute.gallerySearch)}`
      : "";
    const sortQuery = parsedRoute.gallerySort && parsedRoute.gallerySort !== "alpha-asc"
      ? `&sort=${encodeURIComponent(parsedRoute.gallerySort)}`
      : "";
    const statusParam = (parsedRoute.galleryStatus && parsedRoute.galleryStatus !== "all")
      ? `&status=${encodeURIComponent(parsedRoute.galleryStatus)}`
      : (parsedRoute.reviewOnly ? "&review=pending" : "");
    const cacheKey = `${page}:${pageSize}:${parsedRoute.gallerySearch || ""}:${parsedRoute.gallerySort || "date-desc"}:${parsedRoute.galleryStatus || (parsedRoute.reviewOnly ? "review" : "all")}`;
    const cached = mangaFilter ? undefined : galleryPageCacheRef.current.get(cacheKey);
    if (cached) {
      setMangaSummaries(cached.groups);
      setTotalMangaCount(cached.totalGroups);
      setTotalGalleryCount(cached.totalImages);
    } else if (!mangaFilter) {
      setIsGalleryLoading(true);
      setMangaSummaries([]);
      setTotalMangaCount(0);
      setTotalGalleryCount(0);
    } else {
      setIsGalleryLoading(true);
    }
    try {
      const response = await fetch(
        apiUrl(`/api/results/groups?limit=${pageSize}&offset=${offset}${mangaQuery}${searchQuery}${sortQuery}${statusParam}`),
        { signal },
      );
      if (response.ok) {
        const data = await response.json();
        if (requestId !== galleryLoadRequestRef.current) return;
        if (Array.isArray(data.groups)) {
          const totalGroups = typeof data.totalGroups === "number" ? data.totalGroups : data.groups.length;
          const totalImages = typeof data.totalImages === "number"
            ? data.totalImages
            : data.groups.reduce((acc: number, group: { count?: number }) => acc + (group.count || 0), 0);
          setMangaSummaries(data.groups);
          setTotalMangaCount(totalGroups);
          setTotalGalleryCount(totalImages);
          if (!mangaFilter) {
            galleryPageCacheRef.current.set(cacheKey, { groups: data.groups, totalGroups, totalImages });
          }
        }
      }
    } catch (error) {
      if (!signal?.aborted && requestId === galleryLoadRequestRef.current) {
        console.warn("Failed to load manga groups from server:", error);
      }
    } finally {
      if (!signal?.aborted && requestId === galleryLoadRequestRef.current) {
        setIsGalleryLoading(false);
      }
    }
  }, [
    effectiveMangaFilter,
    parsedRoute.galleryPage,
    parsedRoute.galleryPageSize,
    parsedRoute.gallerySearch,
    parsedRoute.gallerySort,
    parsedRoute.galleryStatus,
    parsedRoute.overlay,
    parsedRoute.reviewOnly,
  ]);
};
