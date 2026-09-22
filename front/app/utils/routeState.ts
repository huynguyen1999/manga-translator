export type AppView = 'studio' | 'gallery' | 'pipeline' | 'search';
export type OverlayType = 'none' | 'reader' | 'viewer' | 'editor';
export type GallerySection = 'manga' | 'series';
export type GallerySort = 'alpha-asc' | 'alpha-desc' | 'date-asc' | 'date-desc';
export type MangaStatusFilter = 'all' | 'original' | 'translated' | 'summarized' | 'review';
export const DEFAULT_GALLERY_PAGE_SIZE = 25;
export const GALLERY_PAGE_SIZE_OPTIONS = [25, 50, 75, 100] as const;

export interface ParsedRoute {
  view: AppView;
  overlay: OverlayType;
  folder?: string;
  mangaId?: string;
  mangaTitle?: string;
  seriesId?: string;
  gallerySection?: GallerySection;
  galleryPage?: number;
  galleryPageSize?: number;
  gallerySearch?: string;
  gallerySort?: GallerySort;
  galleryStatus?: MangaStatusFilter;
  reviewOnly?: boolean;
  rawPath: string;
}

export function mangaIdForTitle(mangaTitle: string): string {
  const cleanTitle = mangaTitle.trim() || 'Ungrouped';
  let hash = 2166136261;
  for (const byte of new TextEncoder().encode(cleanTitle)) {
    hash = Math.imul(hash ^ byte, 16777619) >>> 0;
  }
  return `manga-${hash.toString(16)}`;
}

const isMangaId = (value: string): boolean =>
  value.startsWith('manga-') || /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(value);

/**
 * Parses the current pathname and search query string into structured view and overlay state.
 */
export function parseAppPath(pathname: string, search = ''): ParsedRoute {
  const cleanPath = (pathname || '/').replace(/\/+$/, '') || '/';
  const query = new URLSearchParams(search.startsWith('?') ? search.slice(1) : search);

  // Pipeline lab
  if (cleanPath === '/search-lab') {
    return { view: 'search', overlay: 'none', rawPath: cleanPath };
  }
  if (cleanPath === '/pipeline-lab') {
    return {
      view: 'pipeline',
      overlay: 'none',
      rawPath: cleanPath,
    };
  }

  // Reader overlay: /read?manga=...
  if (cleanPath === '/read') {
    const manga = query.get('manga')?.trim() || '';
    return {
      view: 'gallery',
      overlay: 'reader',
      ...(isMangaId(manga) ? { mangaId: manga } : { mangaTitle: manga || undefined }),
      rawPath: cleanPath,
    };
  }

  // Page Editor overlay: /gallery/pages/:folder/edit
  const pageEditMatch = cleanPath.match(/^\/gallery\/pages\/([^/]+)\/edit$/);
  if (pageEditMatch) {
    return {
      view: 'gallery',
      overlay: 'editor',
      folder: decodeURIComponent(pageEditMatch[1]),
      rawPath: cleanPath,
    };
  }

  // Page Viewer/Compare overlay: /gallery/pages/:folder
  const pageViewMatch = cleanPath.match(/^\/gallery\/pages\/([^/]+)$/);
  if (pageViewMatch) {
    return {
      view: 'gallery',
      overlay: 'viewer',
      folder: decodeURIComponent(pageViewMatch[1]),
      rawPath: cleanPath,
    };
  }

  // Manga Detail view: /gallery/manga/:mangaId (legacy title paths still parse)
  const seriesDetailMatch = cleanPath.match(/^\/gallery\/series\/([^/]+)$/);
  if (seriesDetailMatch) {
    return {
      view: 'gallery',
      overlay: 'none',
      seriesId: decodeURIComponent(seriesDetailMatch[1]),
      gallerySection: 'series',
      rawPath: cleanPath,
    };
  }

  // Manga Detail view: /gallery/manga/:mangaId (legacy title paths still parse)
  const mangaDetailMatch = cleanPath.match(/^\/gallery\/manga\/(.+)$/);
  if (mangaDetailMatch) {
    let routeValue = mangaDetailMatch[1];
    try {
      routeValue = decodeURIComponent(routeValue);
    } catch {
      // keep the raw route value
    }
    if (isMangaId(routeValue)) {
      return {
        view: 'gallery',
        overlay: 'none',
        mangaId: routeValue,
        reviewOnly: query.get('review') === 'pending',
        rawPath: cleanPath,
      };
    }

    return {
      view: 'gallery',
      overlay: 'none',
      mangaTitle: routeValue.trim() || undefined,
      reviewOnly: query.get('review') === 'pending',
      rawPath: cleanPath,
    };
  }

  // Primary view: Gallery
  if (cleanPath === '/gallery') {
    const mangaTitle = query.get('manga')?.trim();
    const page = Number(query.get('page'));
    const pageSize = Number(query.get('pageSize'));
    const gallerySearch = query.get('search')?.trim();
    const sort = query.get('sort');
    const gallerySort: GallerySort = sort === 'alpha-desc' || sort === 'date-asc' || sort === 'date-desc'
      ? sort
      : 'date-desc';
    const statusParam = query.get('status')?.trim();
    const validStatus: MangaStatusFilter | undefined = (
      statusParam === 'all' || statusParam === 'original' || statusParam === 'translated' || statusParam === 'summarized' || statusParam === 'review'
    ) ? statusParam : undefined;
    const reviewOnly = query.get('review') === 'pending' || validStatus === 'review';
    const galleryStatus: MangaStatusFilter | undefined = reviewOnly ? 'review' : (validStatus && validStatus !== 'all' ? validStatus : undefined);
    const gallerySection: GallerySection = query.get('view') === 'series' ? 'series' : 'manga';
    return {
      view: 'gallery',
      overlay: 'none',
      gallerySection,
      mangaTitle: mangaTitle || undefined,
      galleryPage: Number.isInteger(page) && page > 0 ? page : 1,
      galleryPageSize: Number.isInteger(pageSize) && pageSize > 0 && pageSize <= 500
        ? pageSize
        : DEFAULT_GALLERY_PAGE_SIZE,
      gallerySearch: gallerySearch || undefined,
      gallerySort,
      galleryStatus,
      reviewOnly,
      rawPath: cleanPath,
    };
  }

  // Primary view: Studio (default)
  return {
    view: 'studio',
    overlay: 'none',
    rawPath: cleanPath,
  };
}

/**
 * Checks if the given URL requires a redirect from root or legacy parameter URLs.
 * e.g.:
 *  - `/` -> `/studio`
 *  - `/?view=gallery` -> `/gallery`
 *  - `/?view=gallery&manga=...` -> `/gallery/manga/...`
 *  - `/gallery?manga=...` -> `/gallery/manga/...`
 *  - `/?view=studio` -> `/studio`
 */
export function getLegacyRedirect(pathname: string, search = ''): string | null {
  const cleanPath = (pathname || '/').replace(/\/+$/, '') || '/';
  const query = new URLSearchParams(search.startsWith('?') ? search.slice(1) : search);

  if (cleanPath === '/') {
    const view = query.get('view');
    const manga = query.get('manga')?.trim();
    if (view === 'gallery') {
      return manga ? buildMangaDetailUrl(manga) : '/gallery';
    }
    if (view === 'studio') return '/studio';

    return '/studio';
  }

  // Redirect legacy /gallery?manga=... to dedicated endpoint /gallery/manga/:mangaId
  if (cleanPath === '/gallery') {
    const manga = query.get('manga')?.trim();
    if (manga) {
      return buildMangaDetailUrl(manga);
    }
  }

  return null;
}

/**
 * Validates the prior route for explicit modal close.
 * Only internal base routes (/gallery, /gallery/manga/..., /pipeline-lab, /studio) are allowed. Fallbacks to /gallery.
 */
export function validatePriorRoute(from?: string | null, fallback = '/gallery'): string {
  if (!from || typeof from !== 'string') return fallback;
  const clean = from.trim();
  if (clean.startsWith('/studio')) return '/studio';
  if (clean.startsWith('/pipeline-lab')) return '/pipeline-lab';
  if (clean === '/search-lab') return '/search-lab';
  if (clean.startsWith('/gallery/manga/')) return clean;
  if (clean.startsWith('/gallery/series/')) return clean;
  if (clean === '/gallery' || clean.startsWith('/gallery?')) return clean;
  if (clean.startsWith('/gallery') && !clean.includes('/pages/')) return '/gallery';
  return fallback;
}

/** Returns the base route that an overlay should return to when it closes. */
export function getNavigationOrigin(
  pathname: string,
  search = '',
  from?: string | null,
): string {
  if (parseAppPath(pathname, search).overlay !== 'none') {
    return validatePriorRoute(from);
  }
  return validatePriorRoute(`${pathname}${search}`);
}

export function buildReaderUrl(mangaTitle: string): string {
  const clean = mangaTitle.trim();
  if (!clean) return '/gallery';
  return buildReaderIdUrl(mangaIdForTitle(clean));
}

export function buildReaderIdUrl(mangaId: string): string {
  const id = mangaId.trim();
  if (!id) return '/gallery';
  return `/read?manga=${encodeURIComponent(id)}`;
}

export function buildPageViewUrl(folder: string): string {
  return `/gallery/pages/${encodeURIComponent(folder)}`;
}

export function buildPageEditUrl(folder: string): string {
  return `/gallery/pages/${encodeURIComponent(folder)}/edit`;
}

export function buildMangaDetailUrl(mangaTitle: string, reviewOnly = false): string {
  const clean = mangaTitle.trim();
  if (!clean) return '/gallery';
  return buildMangaDetailIdUrl(mangaIdForTitle(clean), reviewOnly);
}

export function buildMangaDetailIdUrl(mangaId: string, reviewOnly = false): string {
  const id = mangaId.trim();
  if (!id) return '/gallery';
  return `/gallery/manga/${encodeURIComponent(id)}${reviewOnly ? '?review=pending' : ''}`;
}

export function buildSeriesDetailUrl(seriesId: string): string {
  const id = seriesId.trim();
  if (!id) return '/gallery?view=series';
  return `/gallery/series/${encodeURIComponent(id)}`;
}

export function buildGalleryPageUrl(
  page: number,
  pageSize = DEFAULT_GALLERY_PAGE_SIZE,
  search = '',
  section: GallerySection = 'manga',
  sort: GallerySort = 'date-desc',
  reviewOnly = false,
  status?: MangaStatusFilter,
): string {
  const params = new URLSearchParams();
  if (section === 'series') params.set('view', 'series');
  if (page > 1) params.set('page', String(page));
  if (pageSize !== DEFAULT_GALLERY_PAGE_SIZE) params.set('pageSize', String(pageSize));
  if (search.trim()) params.set('search', search.trim());
  if (sort !== 'date-desc') params.set('sort', sort);
  const effectiveStatus = reviewOnly ? 'review' : (status && status !== 'all' ? status : undefined);
  if (effectiveStatus) {
    params.set('status', effectiveStatus);
    if (effectiveStatus === 'review') {
      params.set('review', 'pending');
    }
  }
  const query = params.toString();
  return query ? `/gallery?${query}` : '/gallery';
}
