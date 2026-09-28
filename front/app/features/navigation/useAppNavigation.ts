import { useCallback, useEffect, useMemo } from "react";
import { useLocation, useNavigate } from "react-router";
import {
  parseAppPath,
  getLegacyRedirect,
  getNavigationOrigin,
  validatePriorRoute,
  buildPageViewUrl,
  buildPageEditUrl,
  buildReaderIdUrl,
  buildMangaDetailIdUrl,
  buildSeriesDetailUrl,
  buildGalleryPageUrl,
  type GallerySort,
  type MangaStatusFilter,
} from "@/utils/routeState";

import { useShortcutNavigation } from "./useShortcutNavigation";

export const useAppNavigation = () => {
  const location = useLocation();
  const navigate = useNavigate();

  useEffect(() => {
    const redirectTarget = getLegacyRedirect(location.pathname, location.search);
    if (redirectTarget) {
      navigate(redirectTarget, { replace: true });
    }
  }, [location.pathname, location.search, navigate]);

  const parsedRoute = useMemo(
    () => parseAppPath(location.pathname, location.search),
    [location.pathname, location.search]
  );
  const activeView = parsedRoute.view;

  useShortcutNavigation(parsedRoute, navigate);

  const handleOpenPageView = useCallback(
    (folder: string) => {
      navigate(buildPageViewUrl(folder), {
        state: { from: getNavigationOrigin(location.pathname, location.search, location.state?.from) },
      });
    },
    [location.pathname, location.search, location.state, navigate]
  );

  const handleOpenPageEdit = useCallback(
    (folder: string) => {
      navigate(buildPageEditUrl(folder), {
        state: { from: getNavigationOrigin(location.pathname, location.search, location.state?.from) },
      });
    },
    [location.pathname, location.search, location.state, navigate]
  );

  const handleOpenReader = useCallback(
    (mangaId: string, _initialPageIndex?: number, replace = false) => {
      navigate(buildReaderIdUrl(mangaId), {
        replace,
        state: {
          from: getNavigationOrigin(location.pathname, location.search, location.state?.from),
        },
      });
    },
    [location.pathname, location.search, location.state, navigate]
  );

  const handleOpenMangaDetail = useCallback(
    (mangaId: string, reviewOnly = false) => {
      navigate(buildMangaDetailIdUrl(mangaId, reviewOnly), {
        state: { from: getNavigationOrigin(location.pathname, location.search, location.state?.from) },
      });
    },
    [location.pathname, location.search, location.state, navigate]
  );

  const updateGalleryUrl = useCallback((overrides: {
    page?: number; pageSize?: number; search?: string; sort?: GallerySort;
    reviewOnly?: boolean; status?: string; minPages?: number; maxPages?: number; replace?: boolean;
  }) => {
    navigate(buildGalleryPageUrl(
      overrides.page ?? 1,
      overrides.pageSize ?? parsedRoute.galleryPageSize,
      overrides.search ?? parsedRoute.gallerySearch,
      parsedRoute.gallerySection,
      overrides.sort ?? parsedRoute.gallerySort,
      overrides.reviewOnly ?? parsedRoute.reviewOnly,
      overrides.status !== undefined ? overrides.status : parsedRoute.galleryStatus,
      overrides.minPages !== undefined ? overrides.minPages : parsedRoute.galleryMinPages,
      overrides.maxPages !== undefined ? overrides.maxPages : parsedRoute.galleryMaxPages,
    ), overrides.replace ? { replace: true } : undefined);
  }, [navigate, parsedRoute]);

  const handleGalleryPageChange = useCallback((page: number) => updateGalleryUrl({ page }), [updateGalleryUrl]);
  const handleGalleryPageSizeChange = useCallback((pageSize: number) => updateGalleryUrl({ pageSize }), [updateGalleryUrl]);
  const handleGallerySearchChange = useCallback((search: string) => updateGalleryUrl({ search, replace: true }), [updateGalleryUrl]);
  const handleGallerySortChange = useCallback((sort: GallerySort) => updateGalleryUrl({ sort }), [updateGalleryUrl]);
  const handleGalleryReviewChange = useCallback((pending: boolean) => updateGalleryUrl({ reviewOnly: pending, status: pending ? 'review' : 'all' }), [updateGalleryUrl]);
  const handleGalleryStatusChange = useCallback((status: string) => updateGalleryUrl({ status, reviewOnly: status === 'review' || status.split(',').includes('review') }), [updateGalleryUrl]);
  const handleGalleryPageRangeChange = useCallback((minPages?: number, maxPages?: number) => updateGalleryUrl({ minPages, maxPages }), [updateGalleryUrl]);

  const handleCloseMangaDetail = useCallback(() => {
    if (location.state?.from) {
      navigate(-1);
      return;
    }
    navigate(validatePriorRoute(location.state?.from));
  }, [location.state, navigate]);

  const handleCloseOverlay = useCallback(() => {
    const priorRoute = validatePriorRoute(location.state?.from);
    navigate(priorRoute);
  }, [location.state, navigate]);

  const handleOpenSeriesDetail = useCallback((seriesId: string) => {
    navigate(buildSeriesDetailUrl(seriesId), {
      state: { from: getNavigationOrigin(location.pathname, location.search, location.state?.from) },
    });
  }, [location.pathname, location.search, location.state, navigate]);

  const handleCloseSeriesDetail = useCallback(() => {
    navigate(validatePriorRoute(location.state?.from, "/gallery?view=series"));
  }, [location.state, navigate]);

  return {
    parsedRoute,
    activeView,
    handleOpenPageView,
    handleOpenPageEdit,
    handleOpenReader,
    handleOpenMangaDetail,
    handleGalleryPageChange,
    handleGalleryPageSizeChange,
    handleGallerySearchChange,
    handleGallerySortChange,
    handleGalleryReviewChange,
    handleGalleryStatusChange,
    handleGalleryPageRangeChange,
    handleCloseMangaDetail,
    handleCloseOverlay,
    handleOpenSeriesDetail,
    handleCloseSeriesDetail,
  };
};
