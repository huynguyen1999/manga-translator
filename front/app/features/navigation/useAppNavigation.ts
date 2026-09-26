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

  const handleGalleryPageChange = useCallback(
    (page: number) => {
      navigate(buildGalleryPageUrl(
        page,
        parsedRoute.galleryPageSize,
        parsedRoute.gallerySearch,
        parsedRoute.gallerySection,
        parsedRoute.gallerySort,
        parsedRoute.reviewOnly,
        parsedRoute.galleryStatus,
      ));
    },
    [navigate, parsedRoute.galleryPageSize, parsedRoute.gallerySearch, parsedRoute.gallerySection, parsedRoute.gallerySort, parsedRoute.reviewOnly, parsedRoute.galleryStatus]
  );

  const handleGalleryPageSizeChange = useCallback(
    (pageSize: number) => {
      navigate(buildGalleryPageUrl(
        1,
        pageSize,
        parsedRoute.gallerySearch,
        parsedRoute.gallerySection,
        parsedRoute.gallerySort,
        parsedRoute.reviewOnly,
        parsedRoute.galleryStatus,
      ));
    },
    [navigate, parsedRoute.gallerySearch, parsedRoute.gallerySection, parsedRoute.gallerySort, parsedRoute.reviewOnly, parsedRoute.galleryStatus]
  );

  const handleGallerySearchChange = useCallback(
    (search: string) => {
      navigate(buildGalleryPageUrl(
        1,
        parsedRoute.galleryPageSize,
        search,
        parsedRoute.gallerySection,
        parsedRoute.gallerySort,
        parsedRoute.reviewOnly,
        parsedRoute.galleryStatus,
      ), { replace: true });
    },
    [navigate, parsedRoute.galleryPageSize, parsedRoute.gallerySection, parsedRoute.gallerySort, parsedRoute.reviewOnly, parsedRoute.galleryStatus]
  );

  const handleGallerySortChange = useCallback(
    (sort: GallerySort) => {
      navigate(buildGalleryPageUrl(
        1,
        parsedRoute.galleryPageSize,
        parsedRoute.gallerySearch,
        parsedRoute.gallerySection,
        sort,
        parsedRoute.reviewOnly,
        parsedRoute.galleryStatus,
      ));
    },
    [navigate, parsedRoute.galleryPageSize, parsedRoute.gallerySearch, parsedRoute.gallerySection, parsedRoute.reviewOnly, parsedRoute.galleryStatus]
  );

  const handleGalleryReviewChange = useCallback((pending: boolean) => {
    navigate(buildGalleryPageUrl(
      1,
      parsedRoute.galleryPageSize,
      parsedRoute.gallerySearch,
      parsedRoute.gallerySection,
      parsedRoute.gallerySort,
      pending,
      pending ? 'review' : 'all',
    ));
  }, [navigate, parsedRoute.galleryPageSize, parsedRoute.gallerySearch, parsedRoute.gallerySection, parsedRoute.gallerySort]);

  const handleGalleryStatusChange = useCallback((status: MangaStatusFilter) => {
    navigate(buildGalleryPageUrl(
      1,
      parsedRoute.galleryPageSize,
      parsedRoute.gallerySearch,
      parsedRoute.gallerySection,
      parsedRoute.gallerySort,
      status === 'review',
      status,
    ));
  }, [navigate, parsedRoute.galleryPageSize, parsedRoute.gallerySearch, parsedRoute.gallerySection, parsedRoute.gallerySort]);

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
    handleCloseMangaDetail,
    handleCloseOverlay,
    handleOpenSeriesDetail,
    handleCloseSeriesDetail,
  };
};
