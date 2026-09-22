import React, { useState, useEffect, useLayoutEffect, useRef, useCallback, useMemo } from 'react';
import { createPortal } from 'react-dom';
import { Icon } from '@iconify/react';
import type { FinishedImage, SeriesDetail, SeriesMember } from '@/types';
import { apiUrl, isPhoneDevice } from '@/utils/api';
import { getMangaReadProgress } from '@/utils/resultGallery';
import { fetchReadingProgress, saveReadingProgress } from '@/utils/readingProgress';

export const isPhoneOrTouch = (): boolean => {
  if (typeof window === 'undefined') return false;
  const userAgent = typeof navigator === 'undefined' ? '' : navigator.userAgent;
  return isPhoneDevice(userAgent) || window.matchMedia('(pointer: coarse)').matches;
};

export function getReaderTitle(title: string, touchDevice: boolean): string | undefined {
  return touchDevice ? undefined : title;
}

const useClientLayoutEffect = typeof window === 'undefined' ? useEffect : useLayoutEffect;

type SafariFullscreenDocument = Document & {
  webkitFullscreenElement?: Element | null;
  webkitExitFullscreen?: () => Promise<void> | void;
};

type SafariFullscreenElement = HTMLElement & {
  webkitRequestFullscreen?: () => Promise<void> | void;
};

export type ReaderMode = 'infinite' | 'single';
export type ReaderWidth = '60%' | '75%' | '90%' | '100%';
export type SinglePageFit = 'height' | 'width';

export function getReaderPreloadIndices(
  currentPage: number,
  totalPages: number,
  _isPhone: boolean,
): number[] {
  const currentIndex = currentPage - 1;
  const indices: number[] = [];
  const ahead = 5;

  if (currentIndex >= 0 && currentIndex < totalPages) indices.push(currentIndex);
  for (let offset = 1; offset <= ahead; offset++) {
    const idx = currentIndex + offset;
    if (idx < totalPages) indices.push(idx);
  }

  for (let offset = 1; offset <= ahead; offset++) {
    const idx = currentIndex - offset;
    if (idx >= 0) indices.push(idx);
  }

  return indices;
}

export function getReaderPriorityIndices(currentPage: number, totalPages: number): number[] {
  const currentIndex = Math.max(0, Math.min(totalPages - 1, currentPage - 1));
  const start = Math.max(0, currentIndex - 5);
  const end = Math.min(totalPages, currentIndex + 6);
  return Array.from({ length: end - start }, (_, offset) => start + offset);
}

export function getSeriesMemberNavigation(
  members: SeriesMember[],
  currentId?: string,
  currentTitle?: string,
): { index: number; previous: SeriesMember | null; next: SeriesMember | null } {
  const index = members.findIndex((member) =>
    currentId ? member.id === currentId : member.title === currentTitle,
  );
  return {
    index,
    previous: index > 0 ? members[index - 1] : null,
    next: index >= 0 && index < members.length - 1 ? members[index + 1] : null,
  };
}

interface MangaReaderModalProps {
  mangaId?: string;
  mangaTitle: string;
  images: FinishedImage[];
  initialPageIndex?: number;
  series?: SeriesDetail | null;
  onSelectManga?: (member: SeriesMember) => void | Promise<void>;
  isLoadingManga?: boolean;
  seriesError?: string | null;
  onClose: (lastPageIndex?: number, lastImageId?: string) => void;
  onEditImage?: (image: FinishedImage) => void;
}

function addRetryParam(url: string, retryCount: number): string {
  if (retryCount === 0 || url.startsWith('blob:') || url.startsWith('data:')) return url;
  return `${url}${url.includes('?') ? '&' : '?'}retry=${retryCount}`;
}

function getSourceUrl(
  source: Blob | string | null | undefined,
  fallback?: string,
): { url: string | null; isBlobUrl: boolean } {
  if (typeof source === 'string' && source) {
    return { url: apiUrl(source), isBlobUrl: false };
  }
  if (source instanceof Blob) {
    const url = URL.createObjectURL(source);
    return { url, isBlobUrl: true };
  }
  return { url: fallback || null, isBlobUrl: false };
}

// Helper to extract or create an object URL for an image result
function getImageUrl(image: FinishedImage): { url: string | null; isBlobUrl: boolean } {
  if (image.readerUrl) return { url: apiUrl(image.readerUrl), isBlobUrl: false };
  if (image.fullUrl) return { url: apiUrl(image.fullUrl), isBlobUrl: false };
  const fallback = image.folder ? apiUrl(`/result/${image.folder}/final.png`) : undefined;
  if (image.result instanceof Blob && image.result.size < 1000 && fallback) {
    return { url: fallback, isBlobUrl: false };
  }
  return getSourceUrl(image.result, fallback);
}

function getOriginalUrl(image: FinishedImage): { url: string | null; isBlobUrl: boolean } {
  return getSourceUrl(image.inputUrl, image.folder ? apiUrl(`/result/${image.folder}/input.png`) : undefined);
}

// ──────────────────────────────────────────────────────────────
// PageItem — Memoized page image component
// Prevents CLS by pre-reserving space with an aspect-ratio placeholder.
// ──────────────────────────────────────────────────────────────
interface PageItemProps {
  image: FinishedImage;
  index: number;
  readerWidth: ReaderWidth;
  onEditImage?: (image: FinishedImage) => void;
  isSinglePage?: boolean;
  singlePageFit?: SinglePageFit;
  isPriority?: boolean;
  pageRef?: (el: HTMLDivElement | null) => void;
  showControls?: boolean;
}

const PageItem: React.FC<PageItemProps> = React.memo(
  ({
    image,
    index,
    readerWidth,
    onEditImage,
    isSinglePage = false,
    singlePageFit = 'height',
    isPriority = false,
    pageRef,
    showControls = false,
  }) => {
    const touchDevice = isPhoneOrTouch();
    const rawResultUrl = useMemo(() => {
      if (image.readerUrl) return apiUrl(image.readerUrl);
      if (image.fullUrl) return apiUrl(image.fullUrl);
      if (!image.result) {
        return image.folder ? apiUrl(`/result/${image.folder}/final.png`) : null;
      }
      if (typeof image.result === 'string') {
        return apiUrl(image.result);
      }
      if (image.result instanceof Blob) {
        if (image.result.size < 1000 && image.folder) {
          return apiUrl(`/result/${image.folder}/final.png`);
        }
        return URL.createObjectURL(image.result);
      }
      return image.folder ? apiUrl(`/result/${image.folder}/final.png`) : null;
    }, [image.result, image.folder, image.readerUrl, image.fullUrl]);

    const rawInputUrl = useMemo(() => {
      if (!image.inputUrl) {
        return image.folder ? apiUrl(`/result/${image.folder}/input.png`) : null;
      }
      if (typeof image.inputUrl === 'string') {
        return apiUrl(image.inputUrl);
      }
      if (image.inputUrl instanceof Blob) {
        return URL.createObjectURL(image.inputUrl);
      }
      return image.folder ? apiUrl(`/result/${image.folder}/input.png`) : null;
    }, [image.inputUrl, image.folder]);

    const [imgUrl, setImgUrl] = useState<string | null>(rawResultUrl);
    const [isLoaded, setIsLoaded] = useState(false);
    const [imgError, setImgError] = useState(false);
    const [aspectRatio, setAspectRatio] = useState<number>(0.707);
    const [retryCount, setRetryCount] = useState(0);

    const [showOriginal, setShowOriginal] = useState(image.sourceType === 'original');
    const [originalUrl, setOriginalUrl] = useState<string | null>(rawInputUrl);
    const [originalLoaded, setOriginalLoaded] = useState(false);
    const [originalError, setOriginalError] = useState(false);
    const [originalRetryCount, setOriginalRetryCount] = useState(0);

    useEffect(() => {
      setImgUrl(rawResultUrl ? addRetryParam(rawResultUrl, retryCount) : null);
      setImgError(false);
    }, [rawResultUrl, retryCount]);

    useEffect(() => {
      setOriginalUrl(rawInputUrl ? addRetryParam(rawInputUrl, originalRetryCount) : null);
      setOriginalError(false);
    }, [rawInputUrl, originalRetryCount]);

    const handleImageLoad = async (e: React.SyntheticEvent<HTMLImageElement>) => {
      const img = e.currentTarget;
      // `load` can fire before the browser has decoded the bitmap. Keep the
      // placeholder up until the pixels are ready, otherwise fast scrolling
      // briefly exposes the black page background.
      if (typeof img.decode === 'function') {
        try {
          await img.decode();
        } catch {
          // The load event still confirms a usable resource in this case.
        }
      }
      if (img.naturalWidth > 0 && img.naturalHeight > 0) {
        setAspectRatio(img.naturalWidth / img.naturalHeight);
      }
      if (showOriginal) {
        setOriginalLoaded(true);
      } else {
        setIsLoaded(true);
      }
    };

    const containerStyle: React.CSSProperties = isSinglePage
      ? {}
      : {
          width: '100%',
          maxWidth: readerWidth,
        };

    const displayUrl = showOriginal ? originalUrl : imgUrl;
    const displayedError = showOriginal ? originalError : imgError;
    const displayedLoaded = showOriginal ? originalLoaded : isLoaded;
    const retryDisplayedImage = () => {
      if (showOriginal) {
        setOriginalError(false);
        setOriginalRetryCount((count) => count + 1);
      } else {
        setImgError(false);
        setIsLoaded(false);
        setRetryCount((count) => count + 1);
      }
    };

    if (isSinglePage) {
      return (
        <div
          ref={pageRef}
          className={`relative flex flex-col items-center justify-center w-full ${isPhoneOrTouch() ? 'min-h-[100svh]' : 'h-full'} select-none`}
        >
          <div
            className={`relative flex items-center justify-center ${
              singlePageFit === 'height'
                ? (showControls ? 'max-h-[calc(100vh-130px)]' : 'max-h-screen') + ' w-auto'
                : 'w-full'
            } transition-[max-height] duration-200`}
            style={{
              maxWidth: singlePageFit === 'width' ? readerWidth : undefined,
            }}
          >
            <div
              className={`absolute top-2 right-2 z-20 flex items-center space-x-1.5 transition-opacity duration-150 ${
                showControls ? 'opacity-100 pointer-events-auto' : 'opacity-0 pointer-events-none'
              }`}
            >
              {onEditImage && image.sourceType !== 'original' && (
                <button
                  type="button"
                  onClick={(e) => {
                    e.stopPropagation();
                    onEditImage(image);
                  }}
                  className="min-h-11 rounded-lg bg-black/65 px-3 py-2 text-xs font-semibold text-white backdrop-blur-sm hover:bg-black/80 focus-visible:outline-2 focus-visible:outline-indigo-400 flex items-center space-x-1"
              title={getReaderTitle('Edit text on this page', touchDevice)}
                  aria-label={`Edit page ${index + 1}`}
                >
                  <Icon icon="carbon:text-annotation-toggle" className="w-3.5 h-3.5" />
                  <span className="hidden sm:inline">Edit</span>
                </button>
              )}
              {image.sourceType !== 'original' && (
                <button
                  type="button"
                  onClick={(e) => {
                    e.stopPropagation();
                    setShowOriginal((value) => !value);
                  }}
                  className="min-h-11 rounded-lg bg-black/65 px-3 py-2 text-xs font-semibold text-white backdrop-blur-sm hover:bg-black/80 focus-visible:outline-2 focus-visible:outline-indigo-400"
                  aria-pressed={showOriginal}
                  aria-label={showOriginal ? 'Show translated page' : 'Peek at original page'}
                >
                  {showOriginal ? 'Translated' : 'Original'}
                </button>
              )}
            </div>

            {displayUrl && !displayedError ? (
              <img
                src={displayUrl}
                alt={`Page ${index + 1}`}
                decoding="async"
                loading={isPriority ? 'eager' : 'lazy'}
                className={`rounded-md shadow-2xl transition-opacity duration-150 ${
                  singlePageFit === 'height'
                    ? 'max-h-[calc(100vh-130px)] max-w-full w-auto h-auto object-contain'
                    : 'w-full h-auto block'
                } ${displayedLoaded ? 'opacity-100' : 'opacity-0'}`}
                onLoad={handleImageLoad}
                onError={() => {
                  if (showOriginal) {
                    if (originalUrl && /^https?:\/\//i.test(originalUrl) && typeof window !== 'undefined') {
                      try {
                        const parsed = new URL(originalUrl);
                        setOriginalUrl(parsed.pathname + parsed.search);
                        return;
                      } catch {}
                    }
                    setOriginalError(true);
                  } else {
                    if (imgUrl && /^https?:\/\//i.test(imgUrl) && typeof window !== 'undefined') {
                      try {
                        const parsed = new URL(imgUrl);
                        setImgUrl(parsed.pathname + parsed.search);
                        return;
                      } catch {}
                    }
                    if (image.folder && displayUrl && !displayUrl.includes(`/result/${image.folder}/final.png`)) {
                      setImgUrl(apiUrl(`/result/${image.folder}/final.png?t=${Date.now()}`));
                      return;
                    }
                    setImgError(true);
                  }
                }}
              />
              ) : null}

              {/* Placeholder / Loading State while decoding image */}
            {(!displayedLoaded || !displayUrl) && !displayedError && (
              <div
                className="flex items-center justify-center bg-zinc-900 border border-zinc-800 rounded-xl shadow-inner animate-pulse"
                style={{
                  width: singlePageFit === 'height' ? 'min(70vh, 550px)' : '100%',
                  aspectRatio: `${aspectRatio}`,
                }}
              >
                <div className="flex flex-col items-center justify-center space-y-3 p-6 text-center">
                  <div className="w-14 h-14 rounded-full bg-zinc-800 border border-zinc-700 flex items-center justify-center shadow">
                    <Icon icon="carbon:image" className="w-7 h-7 text-indigo-400 animate-pulse" />
                  </div>
                  <div className="flex flex-col items-center space-y-1">
                    <span className="text-lg font-bold text-zinc-100 tracking-wide">
                      Page {index + 1}
                    </span>
                    <span className="text-xs font-mono text-zinc-400">Loading page...</span>
                  </div>
                </div>
              </div>
            )}

            {displayedError && (
              <div className="flex flex-col items-center justify-center p-8 bg-zinc-900 border border-zinc-800 rounded-lg text-zinc-400">
                <Icon icon="carbon:warning" className="w-10 h-10 text-amber-500 mb-2" />
                <p className="text-sm font-medium text-zinc-300">
                  {showOriginal ? 'Original page unavailable' : `Failed to load page ${index + 1}`}
                </p>
                <button
                  type="button"
                  onClick={retryDisplayedImage}
                  className="mt-3 rounded-md bg-zinc-800 px-3 py-1.5 text-xs font-semibold text-zinc-200 hover:bg-zinc-700 focus-visible:outline-2 focus-visible:outline-indigo-400"
                >
                  Retry
                </button>
              </div>
            )}
          </div>
        </div>
      );
    }

    // Infinite Scroll Mode Item
    return (
      <div
        ref={pageRef}
        data-page-index={index}
        style={containerStyle}
        className="group relative w-full overflow-hidden bg-zinc-950 transition-[max-width] duration-150"
      >
        {/* Floating Quick Action Overlay (zero vertical height, no page separation) */}
        <div
          className={`absolute top-2 right-2 z-20 flex items-center space-x-1.5 transition-opacity duration-150 ${
            showControls ? 'opacity-100 pointer-events-auto' : 'opacity-0 pointer-events-none'
          }`}
        >
          {onEditImage && image.sourceType !== 'original' && (
            <button
              type="button"
              onClick={(e) => {
                e.stopPropagation();
                onEditImage(image);
              }}
              className="min-h-8 flex items-center space-x-1 px-2.5 py-1 rounded-md text-xs font-medium text-white bg-black/70 hover:bg-indigo-600 backdrop-blur-sm transition-colors shadow-md"
                  title={getReaderTitle('Edit text on this page', touchDevice)}
              aria-label={`Edit page ${index + 1}`}
            >
              <Icon icon="carbon:text-annotation-toggle" className="w-3.5 h-3.5" />
              <span className="hidden sm:inline">Edit</span>
            </button>
          )}
          {image.sourceType !== 'original' && (
            <button
              type="button"
              onClick={(e) => {
                e.stopPropagation();
                setShowOriginal((value) => !value);
              }}
              className="min-h-8 flex items-center space-x-1 px-2.5 py-1 rounded-md text-xs font-medium text-white bg-black/70 hover:bg-indigo-600 backdrop-blur-sm transition-colors shadow-md"
              aria-pressed={showOriginal}
              aria-label={showOriginal ? `Show translated page ${index + 1}` : `Peek at original page ${index + 1}`}
            >
              <Icon icon={showOriginal ? 'carbon:view-off' : 'carbon:view'} className="w-3.5 h-3.5" />
              <span className="hidden sm:inline">{showOriginal ? 'Translated' : 'Original'}</span>
            </button>
          )}
        </div>

        {/* Page Image Container with pre-allocated aspect ratio to eliminate layout shift */}
        <div
          className="relative w-full bg-zinc-950 flex items-center justify-center overflow-hidden"
          style={{
            minHeight: isLoaded ? undefined : '450px',
            aspectRatio: isLoaded ? undefined : `${aspectRatio}`,
          }}
        >
          {displayUrl && !displayedError ? (
            <img
              src={displayUrl}
              alt={`Page ${index + 1}`}
              decoding="async"
              loading={isPriority ? 'eager' : 'lazy'}
              className={`w-full h-auto block transition-opacity duration-150 ${
                displayedLoaded ? 'opacity-100' : 'opacity-0'
              }`}
              onLoad={handleImageLoad}
              onError={() => {
                if (showOriginal) {
                  if (originalUrl && /^https?:\/\//i.test(originalUrl) && typeof window !== 'undefined') {
                    try {
                      const parsed = new URL(originalUrl);
                      setOriginalUrl(parsed.pathname + parsed.search);
                      return;
                    } catch {}
                  }
                  setOriginalError(true);
                } else {
                  if (imgUrl && /^https?:\/\//i.test(imgUrl) && typeof window !== 'undefined') {
                    try {
                      const parsed = new URL(imgUrl);
                      setImgUrl(parsed.pathname + parsed.search);
                      return;
                    } catch {}
                  }
                  if (image.folder && displayUrl && !displayUrl.includes(`/result/${image.folder}/final.png`)) {
                    setImgUrl(apiUrl(`/result/${image.folder}/final.png?t=${Date.now()}`));
                    return;
                  }
                  setImgError(true);
                }
              }}
            />
          ) : null}

          {/* High-contrast numbered skeleton placeholder to prevent empty black regions */}
          {(!displayedLoaded || !displayUrl) && !displayedError && (
            <div className="absolute inset-0 flex flex-col items-center justify-center bg-zinc-900 border border-zinc-800 rounded-xl m-2 text-zinc-200">
              <div className="w-14 h-14 rounded-full bg-zinc-800 border border-zinc-700 flex items-center justify-center shadow mb-3">
                <Icon icon="carbon:image" className="w-7 h-7 text-indigo-400 animate-pulse" />
              </div>
              <span className="text-xl font-bold text-zinc-100 tracking-wide mb-1">
                Page {index + 1}
              </span>
              <span className="text-xs font-mono text-zinc-400">Loading page...</span>
            </div>
          )}

          {displayedError && (
            <div className="py-16 flex flex-col items-center justify-center text-zinc-400">
              <Icon icon="carbon:warning" className="w-8 h-8 text-amber-500 mb-2" />
              <span className="text-xs font-medium">
                {showOriginal ? 'Original page unavailable' : `Failed to load Page ${index + 1}`}
              </span>
              <button
                type="button"
                onClick={retryDisplayedImage}
                className="mt-3 rounded-md bg-zinc-800 px-3 py-1.5 text-xs font-semibold text-zinc-200 hover:bg-zinc-700 focus-visible:outline-2 focus-visible:outline-indigo-400"
              >
                Retry
              </button>
            </div>
          )}
        </div>
      </div>
    );
  }
);

PageItem.displayName = 'PageItem';

// ──────────────────────────────────────────────────────────────
// MangaReaderModal — Main Reader Component
// ──────────────────────────────────────────────────────────────
export const MangaReaderModal: React.FC<MangaReaderModalProps> = ({
  mangaId,
  mangaTitle,
  images,
  initialPageIndex,
  series,
  onSelectManga,
  isLoadingManga = false,
  seriesError,
  onClose,
  onEditImage,
}) => {
  const touchDevice = isPhoneOrTouch();

  // Reading Mode: 'infinite' (continuous scroll) or 'single' (one page at a time)
  const [readerMode, setReaderMode] = useState<ReaderMode>(() => {
    const saved = localStorage.getItem('manga-reader-mode');
    return saved === 'single' ? 'single' : 'infinite';
  });

  // Page Width Options: percentage of the device viewport
  const [readerWidth, setReaderWidth] = useState<ReaderWidth>(() => {
    const saved = typeof window !== 'undefined' ? localStorage.getItem('manga-reader-width') : null;
    if (saved && ['60%', '75%', '90%', '100%'].includes(saved)) {
      return saved as ReaderWidth;
    }
    return isPhoneOrTouch() ? '100%' : '90%';
  });

  // Single page fit: 'height' (entire page fits on screen) or 'width'
  const [singlePageFit, setSinglePageFit] = useState<SinglePageFit>(() => {
    const saved = localStorage.getItem('manga-reader-single-fit');
    return saved === 'width' ? 'width' : 'height';
  });

  const [currentPage, setCurrentPage] = useState<number>(() => {
    if (initialPageIndex !== undefined && initialPageIndex >= 0 && initialPageIndex < images.length) {
      return initialPageIndex + 1;
    }

    if (typeof window !== 'undefined') {
      const progress = getMangaReadProgress(
        window.localStorage.getItem(`manga-read-page-${mangaTitle}`),
        window.localStorage.getItem(`manga-read-count-${mangaTitle}`),
        images.length,
      );
      if (progress.page) return progress.page;
    }

    return 1;
  });
  const [pageInput, setPageInput] = useState(String(currentPage));
  const priorityPageIndices = useMemo(
    () => new Set(getReaderPriorityIndices(currentPage, images.length)),
    [currentPage, images.length],
  );

  const [isFullscreen, setIsFullscreen] = useState(false);
  const [showControls, setShowControls] = useState(false);
  const [isLandscape, setIsLandscape] = useState(() =>
    typeof window !== 'undefined' && window.matchMedia('(orientation: landscape)').matches,
  );
  const [showWidthMenu, setShowWidthMenu] = useState(false);

  const seriesNavigation = useMemo(
    () => series && series.members.length > 1
      ? getSeriesMemberNavigation(series.members, mangaId, mangaTitle)
      : null,
    [mangaId, mangaTitle, series],
  );

  const containerRef = useRef<HTMLDivElement>(null);
  const scrollContainerRef = useRef<HTMLDivElement>(null);
  const topProgressBarRef = useRef<HTMLDivElement>(null);
  const bottomProgressBarRef = useRef<HTMLDivElement>(null);
  const pageRefs = useRef<(HTMLDivElement | null)[]>([]);
  const singlePageScrollRef = useRef<HTMLDivElement>(null);
  const controlsHideTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const controlsVisibleRef = useRef(false);
  const previousFocusedElementRef = useRef<HTMLElement | null>(null);

  useEffect(() => {
    const mediaQuery = window.matchMedia('(orientation: landscape)');
    const syncOrientation = () => setIsLandscape(mediaQuery.matches);
    syncOrientation();
    mediaQuery.addEventListener('change', syncOrientation);
    return () => mediaQuery.removeEventListener('change', syncOrientation);
  }, []);

  const storageKey = useMemo(() => `manga-read-pos-${mangaTitle}`, [mangaTitle]);
  const pageStorageKey = useMemo(() => `manga-read-page-${mangaTitle}`, [mangaTitle]);
  const pageCountStorageKey = useMemo(() => `manga-read-count-${mangaTitle}`, [mangaTitle]);

  // Debounced save for scroll position to prevent disk I/O on scroll events
  const saveTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const remoteSaveTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const preloadedUrls = useRef<Set<string>>(new Set());
  const activePreloads = useRef<HTMLImageElement[]>([]);

  const queueRemoteProgress = useCallback(
    (page: number | null, scrollTop: number, complete: boolean) => {
      if (remoteSaveTimeoutRef.current) clearTimeout(remoteSaveTimeoutRef.current);
      remoteSaveTimeoutRef.current = setTimeout(() => {
        void saveReadingProgress(mangaId || mangaTitle, {
          page,
          pageId: page ? images[page - 1]?.id ?? null : null,
          scrollTop,
          complete,
        }).catch(() => {});
      }, 300);
    },
    [images, mangaId, mangaTitle],
  );

  useEffect(() => {
    let cancelled = false;
    void fetchReadingProgress(mangaId || mangaTitle)
      .then((remote) => {
        if (cancelled || initialPageIndex !== undefined || !remote) return;
        if (!remote.updatedAt) {
          const legacy = getMangaReadProgress(
            localStorage.getItem(pageStorageKey),
            localStorage.getItem(pageCountStorageKey),
            images.length,
          );
          if (legacy.page) {
            queueRemoteProgress(
              legacy.page,
              Number(localStorage.getItem(storageKey) || 0),
              legacy.complete,
            );
          }
        }
        if (remote.page && remote.page >= 1 && remote.page <= images.length) {
          setCurrentPage(remote.page);
          localStorage.setItem(pageStorageKey, String(remote.page));
        }
        if (remote.scrollTop) localStorage.setItem(storageKey, String(remote.scrollTop));
      })
      .catch(() => {});
    return () => {
      cancelled = true;
      if (remoteSaveTimeoutRef.current) clearTimeout(remoteSaveTimeoutRef.current);
    };
  }, [images.length, initialPageIndex, mangaId, mangaTitle, pageCountStorageKey, pageStorageKey, queueRemoteProgress, storageKey]);

  const hideControls = useCallback(() => {
    if (controlsHideTimeoutRef.current) clearTimeout(controlsHideTimeoutRef.current);
    controlsVisibleRef.current = false;
    setShowControls(false);
    setShowWidthMenu(false);
  }, []);

  const revealControls = useCallback(() => {
    if (!controlsVisibleRef.current) {
      controlsVisibleRef.current = true;
      setShowControls(true);
    }
    if (controlsHideTimeoutRef.current) clearTimeout(controlsHideTimeoutRef.current);
    controlsHideTimeoutRef.current = setTimeout(() => {
      hideControls();
    }, 2500);
  }, [hideControls]);

  const pauseControlsHide = useCallback(() => {
    if (controlsHideTimeoutRef.current) {
      clearTimeout(controlsHideTimeoutRef.current);
      controlsHideTimeoutRef.current = null;
    }
  }, []);

  const toggleControls = useCallback(() => {
    if (controlsVisibleRef.current) {
      hideControls();
    } else {
      revealControls();
    }
  }, [hideControls, revealControls]);

  useClientLayoutEffect(() => {
    const previousBodyOverflow = document.body.style.overflow;
    const previousScrollTop = window.scrollY;
    const touchDevice = isPhoneOrTouch();
    // Keep document scrolling available on touch Safari so its browser chrome can collapse.
    if (!touchDevice) document.body.style.overflow = 'hidden';
    if (touchDevice) window.scrollTo({ top: 0, left: 0, behavior: 'auto' });
    previousFocusedElementRef.current = document.activeElement instanceof HTMLElement
      ? document.activeElement
      : null;
    const focusFrame = requestAnimationFrame(() => containerRef.current?.focus());
    return () => {
      cancelAnimationFrame(focusFrame);
      if (!touchDevice) document.body.style.overflow = previousBodyOverflow;
      if (previousFocusedElementRef.current) {
        previousFocusedElementRef.current.focus({ preventScroll: true });
      }
      if (previousScrollTop > 0) {
        window.scrollTo({ top: previousScrollTop, left: 0, behavior: 'auto' });
      }
      if (controlsHideTimeoutRef.current) clearTimeout(controlsHideTimeoutRef.current);
    };
  }, []);

  // Scroll to top when changing page in single page mode
  useEffect(() => {
    if (readerMode === 'single' && singlePageScrollRef.current) {
      singlePageScrollRef.current.scrollTop = 0;
    }
  }, [currentPage, readerMode]);

  useEffect(() => {
    setPageInput(String(currentPage));
  }, [currentPage]);

  // Sync mode changes to localStorage
  const handleSetMode = (mode: ReaderMode) => {
    setReaderMode(mode);
    localStorage.setItem('manga-reader-mode', mode);

    // If switching to infinite scroll, smoothly jump to current page
    if (mode === 'infinite') {
      setTimeout(() => {
        const target = pageRefs.current[currentPage - 1];
        if (target && scrollContainerRef.current) {
          target.scrollIntoView({ behavior: 'smooth', block: 'start' });
        }
      }, 50);
    }
  };

  // Sync width changes to localStorage
  const handleSetWidth = (width: ReaderWidth) => {
    setReaderWidth(width);
    localStorage.setItem('manga-reader-width', width);
    setShowWidthMenu(false);
  };

  // Sync single page fit changes to localStorage
  const handleSetSinglePageFit = (fit: SinglePageFit) => {
    setSinglePageFit(fit);
    localStorage.setItem('manga-reader-single-fit', fit);
  };

  // Restore saved scroll position before the reader paints, avoiding a page-one flash.
  useClientLayoutEffect(() => {
    const savedPageCount = localStorage.getItem(pageCountStorageKey);
    const chapterChanged = savedPageCount && Number(savedPageCount) !== images.length;
    if (chapterChanged) {
      localStorage.removeItem(storageKey);
      localStorage.removeItem(pageStorageKey);
    }

    if (readerMode === 'infinite') {
      const savedPos = chapterChanged ? null : localStorage.getItem(storageKey);
      if (savedPos) {
        const pos = parseInt(savedPos, 10);
        const restoreScrollPosition = () => {
          if (isPhoneOrTouch()) {
            window.scrollTo({ top: pos, left: 0, behavior: 'auto' });
          } else if (scrollContainerRef.current) {
            scrollContainerRef.current.scrollTop = pos;
          }
        };
        restoreScrollPosition();
        const frame = requestAnimationFrame(restoreScrollPosition);
        return () => cancelAnimationFrame(frame);
      }
    }
  }, [images.length, pageCountStorageKey, pageStorageKey, readerMode, storageKey]);

  // High-performance scroll handler without triggering parent re-render on every pixel
  const handleScroll = useCallback(() => {
    const el = touchDevice
      ? document.scrollingElement
      : readerMode === 'infinite'
        ? scrollContainerRef.current
        : singlePageScrollRef.current;
    if (!el) return;

    hideControls();

    const { scrollTop, scrollHeight, clientHeight } = el;
    const maxScroll = scrollHeight - clientHeight;
    const pct = maxScroll > 0 ? Math.min(1, Math.max(0, scrollTop / maxScroll)) : 0;
    const pctString = `${Math.round(pct * 100)}%`;

    // Direct DOM mutation for progress bars to avoid React render churn
    if (topProgressBarRef.current) {
      topProgressBarRef.current.style.width = pctString;
    }
    if (bottomProgressBarRef.current) {
      bottomProgressBarRef.current.style.width = pctString;
    }

    // Debounce localStorage writes (every 300ms after last scroll)
    if (saveTimeoutRef.current) clearTimeout(saveTimeoutRef.current);
    saveTimeoutRef.current = setTimeout(() => {
      localStorage.setItem(storageKey, String(Math.round(scrollTop)));
      localStorage.setItem(pageCountStorageKey, String(images.length));
      queueRemoteProgress(currentPage, Math.round(scrollTop), currentPage >= images.length);
    }, 300);
  }, [currentPage, hideControls, images.length, pageCountStorageKey, queueRemoteProgress, readerMode, storageKey, touchDevice]);

  // On touch devices the document, rather than an inner div, must scroll so Safari can collapse its toolbar.
  useEffect(() => {
    if (!touchDevice) return;
    window.addEventListener('scroll', handleScroll, { passive: true });
    return () => window.removeEventListener('scroll', handleScroll);
  }, [handleScroll, touchDevice]);

  // Center-line IntersectionObserver for tracking active page without flickering or stuttering
  useEffect(() => {
    if (readerMode !== 'infinite') return;
    const root = touchDevice ? null : scrollContainerRef.current;
    if (!touchDevice && !root) return;

    let observer: IntersectionObserver;
    const observePages = () => {
      observer?.disconnect();
      // Percentage root margins resolve against width, not viewport height.
      const inset = (root?.clientHeight ?? window.innerHeight) * 0.45;
      observer = new IntersectionObserver(
        (entries) => {
          for (const entry of entries) {
            if (entry.isIntersecting) {
              const pageIndexAttr = entry.target.getAttribute('data-page-index');
              if (pageIndexAttr !== null) {
                const idx = parseInt(pageIndexAttr, 10);
                if (!isNaN(idx)) {
                  const newPage = idx + 1;
                  setCurrentPage(newPage);
                  localStorage.setItem(pageStorageKey, String(newPage));
                  localStorage.setItem(pageCountStorageKey, String(images.length));
                  queueRemoteProgress(newPage, 0, newPage >= images.length);
                }
              }
            }
          }
        },
        {
          root,
          rootMargin: `-${inset}px 0px -${inset}px 0px`,
          threshold: 0,
        }
      );

      const refs = pageRefs.current;
      refs.forEach((ref) => {
        if (ref) observer.observe(ref);
      });
    };
    observePages();
    const resizeObserver = new ResizeObserver(observePages);
    if (root) resizeObserver.observe(root);
    window.addEventListener('resize', observePages);
    return () => {
      observer.disconnect();
      resizeObserver.disconnect();
      window.removeEventListener('resize', observePages);
    };
  }, [images, pageCountStorageKey, pageStorageKey, queueRemoteProgress, readerMode, touchDevice]);

  // Save current page in single page mode
  useEffect(() => {
    if (readerMode === 'single') {
      localStorage.setItem(pageStorageKey, String(currentPage));
      localStorage.setItem(pageCountStorageKey, String(images.length));
      queueRemoteProgress(currentPage, 0, currentPage >= images.length);
    }
  }, [images.length, pageCountStorageKey, queueRemoteProgress, readerMode, currentPage, pageStorageKey]);

  // Single-page mode needs detached preloads; continuous mode uses its mounted images.
  useEffect(() => {
    if (readerMode !== 'single' || images.length === 0) return;

    const targetIndices = getReaderPreloadIndices(currentPage, images.length, isPhoneOrTouch());

    const newPreloads: HTMLImageElement[] = [];
    for (const idx of targetIndices) {
      const img = images[idx];
      if (!img) continue;
      const res = getImageUrl(img);
      if (res.url && !preloadedUrls.current.has(res.url)) {
        preloadedUrls.current.add(res.url);
        const preloadImg = new Image();
        preloadImg.decoding = 'async';
        preloadImg.src = res.url;
        newPreloads.push(preloadImg);
      }
    }
    activePreloads.current = newPreloads;
  }, [currentPage, images, readerMode]);

  const handleJumpToPage = useCallback((pageNumber: number) => {
    const clamped = Math.max(1, Math.min(images.length, pageNumber));
    setCurrentPage(clamped);

    if (readerMode === 'infinite') {
      const prefersReducedMotion =
        typeof window !== 'undefined' &&
        window.matchMedia?.('(prefers-reduced-motion: reduce)')?.matches;
      requestAnimationFrame(() => {
        pageRefs.current[clamped - 1]?.scrollIntoView({
          behavior: prefersReducedMotion ? 'auto' : 'smooth',
          block: 'start',
        });
      });
    }
  }, [images.length, readerMode]);

  const handleSubmitPageJump = (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const page = Number.parseInt(pageInput, 10);
    if (Number.isFinite(page)) handleJumpToPage(page);
  };

  // Navigation handlers for both reader modes
  const handlePrevPage = useCallback(() => {
    handleJumpToPage(currentPage - 1);
  }, [currentPage, handleJumpToPage]);

  const handleNextPage = useCallback(() => {
    handleJumpToPage(currentPage + 1);
  }, [currentPage, handleJumpToPage]);

  const handleStartOver = useCallback(() => {
    localStorage.removeItem(storageKey);
    localStorage.removeItem(pageStorageKey);
    localStorage.removeItem(pageCountStorageKey);
    setCurrentPage(1);
    setShowWidthMenu(false);
    if (readerMode === 'infinite') {
      requestAnimationFrame(() => {
        if (isPhoneOrTouch()) {
          window.scrollTo({ top: 0, left: 0, behavior: 'smooth' });
        } else {
          scrollContainerRef.current?.scrollTo({ top: 0, behavior: 'smooth' });
        }
      });
    }
    revealControls();
  }, [pageCountStorageKey, pageStorageKey, readerMode, revealControls, storageKey]);

  const handleCloseReader = useCallback(() => {
    const pageIndex = Math.max(0, Math.min(images.length - 1, currentPage - 1));
    const currentImg = images[pageIndex];
    onClose(pageIndex, currentImg?.id);
  }, [currentPage, images, onClose]);

  // Fullscreen toggle handler
  const toggleFullscreen = useCallback(() => {
    hideControls();
    const safariDocument = document as SafariFullscreenDocument;
    const fullscreenElement = containerRef.current as SafariFullscreenElement | null;
    const activeFullscreenElement = document.fullscreenElement ?? safariDocument.webkitFullscreenElement;

    if (!activeFullscreenElement) {
      const request = fullscreenElement?.requestFullscreen?.() ?? fullscreenElement?.webkitRequestFullscreen?.();
      void Promise.resolve(request)
        .then(() => {
          const orientation = screen.orientation as ScreenOrientation & {
            lock?: (orientation: string) => Promise<void>;
          };
          const lock = orientation.lock?.('landscape');
          return lock ? lock.catch(() => {}) : undefined;
        })
        .catch(() => {});
    } else {
      const exit = document.exitFullscreen?.() ?? safariDocument.webkitExitFullscreen?.();
      void Promise.resolve(exit).catch(() => {});
    }
  }, [hideControls]);

  // Sync fullscreen change events
  useEffect(() => {
    const onFullscreenChange = () => {
      const safariDocument = document as SafariFullscreenDocument;
      setIsFullscreen(Boolean(document.fullscreenElement ?? safariDocument.webkitFullscreenElement));
    };
    document.addEventListener('fullscreenchange', onFullscreenChange);
    document.addEventListener('webkitfullscreenchange', onFullscreenChange);
    return () => {
      document.removeEventListener('fullscreenchange', onFullscreenChange);
      document.removeEventListener('webkitfullscreenchange', onFullscreenChange);
    };
  }, []);

  // Keyboard navigation
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      // Focus trap within modal for Tab key navigation
      if (e.key === 'Tab' && containerRef.current) {
        const focusable = containerRef.current.querySelectorAll<HTMLElement>(
          'button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])',
        );
        const first = focusable[0];
        const last = focusable[focusable.length - 1];
        if (e.shiftKey && document.activeElement === first) {
          e.preventDefault();
          last.focus();
        } else if (!e.shiftKey && document.activeElement === last) {
          e.preventDefault();
          first.focus();
        }
        return;
      }

      // Don't intercept if user is typing in an input
      if (['INPUT', 'TEXTAREA', 'SELECT'].includes((e.target as HTMLElement)?.tagName)) {
        return;
      }

      if (e.key === 'Escape') {
        handleCloseReader();
      } else if (e.key === 'ArrowRight' || e.key === 'PageDown') {
        e.preventDefault();
        if (readerMode === 'single') {
          handleNextPage();
        } else if (touchDevice) {
          window.scrollBy({ top: window.innerHeight * 0.75, behavior: 'smooth' });
        } else if (scrollContainerRef.current) {
          scrollContainerRef.current.scrollBy({ top: window.innerHeight * 0.75, behavior: 'smooth' });
        }
      } else if (e.key === 'ArrowLeft' || e.key === 'PageUp') {
        e.preventDefault();
        if (readerMode === 'single') {
          handlePrevPage();
        } else if (touchDevice) {
          window.scrollBy({ top: -window.innerHeight * 0.75, behavior: 'smooth' });
        } else if (scrollContainerRef.current) {
          scrollContainerRef.current.scrollBy({ top: -window.innerHeight * 0.75, behavior: 'smooth' });
        }
      } else if (e.key === 'Home') {
        e.preventDefault();
        handleJumpToPage(1);
      } else if (e.key === 'End') {
        e.preventDefault();
        handleJumpToPage(images.length);
      } else if (e.key === ' ' && readerMode === 'single') {
        e.preventDefault();
        handleNextPage();
      } else if (e.key === 'f' || e.key === 'F') {
        e.preventDefault();
        toggleFullscreen();
      }
    };

    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [readerMode, handleNextPage, handlePrevPage, handleJumpToPage, images.length, handleCloseReader, revealControls, toggleFullscreen]);

  // Clean up timer and active preloads on unmount
  useEffect(() => {
    return () => {
      activePreloads.current = [];
      if (saveTimeoutRef.current) clearTimeout(saveTimeoutRef.current);
      if (controlsHideTimeoutRef.current) clearTimeout(controlsHideTimeoutRef.current);
    };
  }, []);

  const widthPresets: { label: string; value: ReaderWidth; desc: string }[] = [
    { label: 'Compact', value: '60%', desc: '60% of screen width' },
    { label: 'Standard', value: '75%', desc: '75% of screen width' },
    { label: 'Default', value: '90%', desc: '90% of screen width (Default)' },
    { label: 'Full Width', value: '100%', desc: '100% of screen width' },
  ];

  return createPortal((
    <div
      ref={containerRef}
      role="dialog"
      aria-modal="true"
      aria-labelledby="manga-reader-title"
      tabIndex={-1}
      className={`manga-reader ${touchDevice ? 'absolute min-h-[100svh] overflow-visible' : 'fixed h-dvh min-h-dvh overflow-hidden'} inset-0 z-[100] flex w-screen flex-col bg-zinc-950 text-zinc-100 select-none`}
      style={{
        position: touchDevice ? 'absolute' : 'fixed',
        inset: 0,
        bottom: touchDevice ? 'auto' : 0,
        zIndex: 100,
        width: '100vw',
        height: touchDevice ? 'auto' : '100dvh',
        minHeight: touchDevice ? '100svh' : '100dvh',
        overflow: touchDevice ? 'visible' : 'hidden',
        isolation: 'isolate',
      }}
    >
      {/* Top Persistent Thin Progress Bar (Infinite Scroll) */}
      {readerMode === 'infinite' && (
        <div
          className={`absolute top-0 left-0 right-0 h-1 z-40 bg-zinc-800 transition-opacity duration-200 ${
            showControls ? 'opacity-100' : 'opacity-0 pointer-events-none'
          }`}
        >
          <div
            ref={topProgressBarRef}
            className="h-full bg-indigo-500 will-change-[width]"
            style={{ width: `${Math.round(((currentPage - 1) / Math.max(1, images.length - 1)) * 100)}%` }}
          />
        </div>
      )}

      {/* Header Toolbar */}
      <header
        onClick={(e) => e.stopPropagation()}
        className={`${touchDevice ? 'fixed' : 'absolute'} top-0 left-0 right-0 flex items-center justify-between gap-1.5 sm:gap-3 px-2.5 sm:px-6 py-2 bg-zinc-900/95 border-b border-zinc-800/80 z-30 transition-all duration-200 ${
          showControls
            ? 'translate-y-0 opacity-100 visible pointer-events-auto'
            : '-translate-y-full opacity-0 invisible pointer-events-none'
        }`}
        style={{
          paddingTop: 'calc(0.5rem + env(safe-area-inset-top, 0px))',
        }}
      >
        {/* Title & Page Info & Series Switcher */}
        <div className="flex items-center space-x-2 sm:space-x-3 min-w-0 shrink">
          <div className="p-1.5 rounded-lg bg-indigo-600/20 text-indigo-400 shrink-0 hidden xs:block">
            <Icon icon="carbon:book" className="w-4 h-4 sm:w-5 sm:h-5" />
          </div>
          <div className="min-w-0">
            {seriesNavigation && series ? (
              <label className="flex items-center min-w-0">
                <span className="sr-only">Manga in series</span>
                <select
                  aria-label="Manga in series"
                  value={mangaId || mangaTitle}
                  disabled={isLoadingManga}
                  onChange={(event) => {
                    const member = series.members.find((entry) => entry.id === event.target.value);
                    if (member) void onSelectManga?.(member);
                  }}
                  className="max-w-[120px] xs:max-w-[160px] sm:max-w-xs md:max-w-md truncate bg-zinc-800 border border-zinc-700/80 rounded-md px-1.5 py-0.5 font-semibold text-xs text-zinc-100 focus:outline-hidden focus:ring-1 focus:ring-indigo-500"
                >
                  {series.members.map((member) => (
                    <option key={member.id} value={member.id} className="bg-zinc-900 text-zinc-100">
                      {member.position}. {member.title}
                    </option>
                  ))}
                </select>
              </label>
            ) : (
              <h2 id="manga-reader-title" className="font-semibold text-xs sm:text-sm text-zinc-100 truncate max-w-[100px] xs:max-w-[150px] sm:max-w-xs md:max-w-md" title={getReaderTitle(mangaTitle, touchDevice)}>
                {mangaTitle}
              </h2>
            )}
            <p className="text-[10px] sm:text-xs text-zinc-300 font-mono truncate">
              Page {currentPage} of {images.length}
            </p>
          </div>
        </div>

        {/* Center Controls: Reading Mode Toggle & Page Width */}
        <div className="flex items-center space-x-1 sm:space-x-2.5 shrink-0">
          {readerMode === 'single' && (
            <button
              type="button"
              onClick={() => handleJumpToPage(1)}
              className="min-h-9 sm:min-h-10 flex items-center space-x-1 px-2 sm:px-2.5 py-1 rounded-lg bg-zinc-800 hover:bg-zinc-700 text-zinc-300 hover:text-white text-xs border border-zinc-700/60 transition-colors"
              title={getReaderTitle('Go to top', touchDevice)}
              aria-label="Go to top"
            >
              <Icon icon="carbon:arrow-up" className="w-3.5 h-3.5 sm:w-4 sm:h-4" />
              <span className="hidden md:inline">Top</span>
            </button>
          )}

          <form onSubmit={handleSubmitPageJump} className="flex items-center gap-1" aria-label="Go to page">
            <label htmlFor="reader-page-input" className="sr-only">Page number</label>
            <input
              id="reader-page-input"
              type="number"
              min={1}
              max={images.length}
              value={pageInput}
              onChange={(event) => setPageInput(event.target.value)}
              className="h-8 sm:h-10 w-11 sm:w-14 rounded-lg border border-zinc-700/60 bg-zinc-800 px-1 text-center font-mono text-xs text-zinc-200 focus:border-indigo-500 focus:outline-hidden focus:ring-1 focus:ring-indigo-500"
              aria-label="Page number"
            />
            <button
              type="submit"
              className="min-h-8 sm:min-h-10 px-2 sm:px-2.5 rounded-lg bg-zinc-800 hover:bg-zinc-700 text-zinc-300 hover:text-white text-xs border border-zinc-700/60 transition-colors"
              title={getReaderTitle('Go to page', touchDevice)}
            >
              Go
            </button>
          </form>

          {/* Mode Switcher: Infinite Scroll vs Single Page */}
          <div className="flex items-center rounded-lg bg-zinc-800/90 p-0.5 border border-zinc-700/60 shadow-inner">
            <button
              type="button"
              onClick={() => handleSetMode('infinite')}
              className={`min-h-8 sm:min-h-10 flex items-center space-x-1 px-2 sm:px-2.5 py-1 text-xs font-medium rounded-md transition-all ${
                readerMode === 'infinite'
                  ? 'bg-indigo-600 text-white shadow-xs'
                  : 'text-zinc-400 hover:text-zinc-200'
              }`}
              title={getReaderTitle('Continuous infinite vertical scroll', touchDevice)}
              aria-label="Continuous scroll mode"
            >
              <Icon icon="carbon:page-break" className="w-3.5 h-3.5" />
              <span className="hidden sm:inline">Infinite Scroll</span>
            </button>
            <button
              type="button"
              onClick={() => handleSetMode('single')}
              className={`min-h-8 sm:min-h-10 flex items-center space-x-1 px-2 sm:px-2.5 py-1 text-xs font-medium rounded-md transition-all ${
                readerMode === 'single'
                  ? 'bg-indigo-600 text-white shadow-xs'
                  : 'text-zinc-400 hover:text-zinc-200'
              }`}
              title={getReaderTitle('Single page pagination mode', touchDevice)}
              aria-label="Single page mode"
            >
              <Icon icon="carbon:document" className="w-3.5 h-3.5" />
              <span className="hidden sm:inline">Single Page</span>
            </button>
          </div>

          {/* Page Width Dropdown Button */}
          <div className="relative">
            <button
              type="button"
              onClick={() => setShowWidthMenu((prev) => !prev)}
              className="min-h-8 sm:min-h-10 flex items-center space-x-1 px-2 sm:px-2.5 py-1.5 rounded-lg bg-zinc-800 hover:bg-zinc-700 text-zinc-300 hover:text-white text-xs border border-zinc-700/60 transition-colors"
              title={getReaderTitle('Change page width', touchDevice)}
              aria-label={`Change page width, currently ${readerWidth}`}
            >
              <Icon icon="carbon:fit-to-width" className="w-3.5 h-3.5 sm:w-4 sm:h-4 text-indigo-400" />
              <span className="font-mono text-xs hidden md:inline">{readerWidth}</span>
              <Icon icon="carbon:chevron-down" className="w-3 h-3 text-zinc-400 hidden sm:inline" />
            </button>

            {showWidthMenu && (
              <div
                className="absolute right-0 mt-1.5 w-44 rounded-xl bg-zinc-900 border border-zinc-700 shadow-2xl p-1 z-50 animate-in fade-in zoom-in-95"
                onMouseLeave={() => setShowWidthMenu(false)}
              >
                <div className="px-2.5 py-1.5 text-xs font-semibold text-zinc-300 uppercase tracking-wider border-b border-zinc-800">
                  Page Width
                </div>
                {widthPresets.map((preset) => (
                  <button
                    key={preset.value}
                    type="button"
                    onClick={() => handleSetWidth(preset.value)}
                    className={`w-full flex items-center justify-between px-2.5 py-1.5 rounded-lg text-xs transition-colors ${
                      readerWidth === preset.value
                        ? 'bg-indigo-600/30 text-indigo-300 font-semibold'
                        : 'text-zinc-300 hover:bg-zinc-800 hover:text-white'
                    }`}
                  >
                    <span>{preset.label}</span>
                    <span className="text-xs font-mono text-zinc-400">{preset.value}</span>
                  </button>
                ))}

                {readerMode === 'single' && (
                  <>
                    <div className="my-1 border-t border-zinc-800" />
                    <div className="px-2.5 py-1 text-xs font-semibold text-zinc-300 uppercase tracking-wider">
                      Single Page Fit
                    </div>
                    <button
                      type="button"
                      onClick={() => handleSetSinglePageFit('height')}
                      className={`w-full flex items-center justify-between px-2.5 py-1.5 rounded-lg text-xs transition-colors ${
                        singlePageFit === 'height'
                          ? 'bg-indigo-600/30 text-indigo-300 font-semibold'
                          : 'text-zinc-300 hover:bg-zinc-800 hover:text-white'
                      }`}
                    >
                      <div className="flex items-center space-x-1.5">
                        <Icon icon="carbon:fit-to-screen" className="w-3.5 h-3.5" />
                        <span>Fit Screen Height</span>
                      </div>
                    </button>
                    <button
                      type="button"
                      onClick={() => handleSetSinglePageFit('width')}
                      className={`w-full flex items-center justify-between px-2.5 py-1.5 rounded-lg text-xs transition-colors ${
                        singlePageFit === 'width'
                          ? 'bg-indigo-600/30 text-indigo-300 font-semibold'
                          : 'text-zinc-300 hover:bg-zinc-800 hover:text-white'
                      }`}
                    >
                      <div className="flex items-center space-x-1.5">
                        <Icon icon="carbon:fit-to-width" className="w-3.5 h-3.5" />
                        <span>Fit Page Width</span>
                      </div>
                    </button>
                  </>
                )}
                <div className="my-1 border-t border-zinc-800" />
                <button
                  type="button"
                  onClick={handleStartOver}
                  className="w-full flex items-center space-x-1.5 px-2.5 py-1.5 rounded-lg text-xs text-zinc-300 hover:bg-zinc-800 hover:text-white transition-colors"
                >
                  <Icon icon="carbon:rewind-10" className="w-3.5 h-3.5" />
                  <span>Start over</span>
                </button>
              </div>
            )}
          </div>
        </div>

        {/* Right Actions: Fullscreen & Close */}
        <div className="flex items-center space-x-1 sm:space-x-1.5 shrink-0">
          <button
            type="button"
            onClick={toggleFullscreen}
            className="min-h-8 min-w-8 sm:min-h-10 sm:min-w-10 p-1.5 rounded-lg text-zinc-400 hover:text-white hover:bg-zinc-800 transition-colors flex items-center justify-center"
            title={getReaderTitle(isFullscreen ? 'Exit fullscreen (F)' : 'Fullscreen (F)', touchDevice)}
            aria-label={isFullscreen ? 'Exit fullscreen' : 'Enter fullscreen'}
          >
            <Icon icon={isFullscreen ? 'carbon:minimize' : 'carbon:maximize'} className="w-4 h-4" />
          </button>

          <button
            type="button"
            onClick={handleCloseReader}
            className="min-h-8 min-w-8 sm:min-h-10 sm:min-w-10 p-1.5 rounded-lg text-zinc-400 hover:text-white hover:bg-zinc-800 transition-colors flex items-center justify-center"
            title={getReaderTitle('Close reader (Esc)', touchDevice)}
            aria-label="Close reader"
          >
            <Icon icon="carbon:close" className="w-4 h-4 sm:w-5 sm:h-5" />
          </button>
        </div>
      </header>

      {/* Main Reader Body */}
      <main className={`${touchDevice ? 'relative flex-none overflow-visible' : 'relative flex-1 h-full overflow-hidden'} w-full bg-zinc-950 flex flex-col`} aria-label="Manga pages">
        {readerMode === 'infinite' ? (
          /* ── Infinite Scroll Mode ── */
          <div
            ref={scrollContainerRef}
            onScroll={handleScroll}
            onClick={(e) => {
              const target = e.target as HTMLElement;
              if (target.closest('button, a, input, select, textarea')) return;
              toggleControls();
            }}
            className={`${touchDevice ? 'w-full overflow-visible' : 'flex-1 h-full overflow-y-auto overflow-x-hidden manga-reader-scroll'} cursor-pointer`}
            style={{ overscrollBehavior: touchDevice ? 'auto' : 'contain' }}
          >
            <div className="flex flex-col items-center w-full">
              {images.map((image, idx) => (
                <div
                  key={image.id}
                  ref={(el) => { pageRefs.current[idx] = el; }}
                  data-page-index={idx}
                  className="flex w-full justify-center bg-zinc-950"
                >
                  <PageItem
                    image={image}
                    index={idx}
                    readerWidth={readerWidth}
                    onEditImage={onEditImage}
                    isPriority={priorityPageIndices.has(idx)}
                    showControls={showControls}
                  />
                </div>
              ))}

              {/* End of Manga Banner */}
              <div className="py-12 flex flex-col items-center space-y-2 text-zinc-400">
                <Icon icon="carbon:checkmark-filled" className="w-8 h-8 text-indigo-500" />
                <p className="text-sm font-semibold text-zinc-300">End of {mangaTitle}</p>
                <p className="text-xs text-zinc-400">{images.length} pages read</p>
                <button
                  type="button"
                  onClick={(e) => {
                    e.stopPropagation();
                    handleJumpToPage(1);
                  }}
                  className="mt-2 flex items-center space-x-1.5 px-3 py-1.5 rounded-lg bg-zinc-800 hover:bg-zinc-700 text-xs text-zinc-300 transition-colors"
                >
                  <Icon icon="carbon:arrow-up" className="w-3.5 h-3.5" />
                  <span>Back to Top</span>
                </button>
              </div>
            </div>
          </div>
        ) : (
          /* ── Single Page Mode ── */
          <div
            ref={singlePageScrollRef}
            onScroll={handleScroll}
            onClick={(e) => {
              const target = e.target as HTMLElement;
              if (target.closest('button, a, input, select, textarea')) return;
              toggleControls();
            }}
            className={`${touchDevice ? 'relative flex-none w-full overflow-visible' : 'relative flex-1 h-full overflow-y-auto overflow-x-hidden manga-reader-scroll'} flex flex-col items-center justify-center p-0 sm:p-2 cursor-pointer`}
          >
            {/* Click Navigation Overlay Zones */}
            <div
              onClick={(e) => {
                e.stopPropagation();
                handlePrevPage();
              }}
              className={`absolute top-0 bottom-0 left-0 w-1/4 z-10 cursor-w-resize group/left flex items-center justify-start pl-4 transition-opacity ${
                currentPage === 1 ? 'pointer-events-none' : ''
              }`}
              title={getReaderTitle('Previous Page (ArrowLeft / A)', touchDevice)}
            >
              <div className="p-3 rounded-full bg-black/50 text-white/40 group-hover/left:text-white group-hover/left:bg-black/70 backdrop-blur-sm opacity-0 group-hover/left:opacity-100 transition-all">
                <Icon icon="carbon:chevron-left" className="w-6 h-6" />
              </div>
            </div>

            <div
              onClick={(e) => {
                e.stopPropagation();
                handleNextPage();
              }}
              className={`absolute top-0 bottom-0 right-0 w-1/4 z-10 cursor-e-resize group/right flex items-center justify-end pr-4 transition-opacity ${
                currentPage === images.length ? 'pointer-events-none' : ''
              }`}
              title={getReaderTitle('Next Page (ArrowRight / Space / D)', touchDevice)}
            >
              <div className="p-3 rounded-full bg-black/50 text-white/40 group-hover/right:text-white group-hover/right:bg-black/70 backdrop-blur-sm opacity-0 group-hover/right:opacity-100 transition-all">
                <Icon icon="carbon:chevron-right" className="w-6 h-6" />
              </div>
            </div>

            {/* Active Single Page */}
            {images[currentPage - 1] && (
              <PageItem
                key={images[currentPage - 1].id}
                image={images[currentPage - 1]}
                index={currentPage - 1}
                readerWidth={readerWidth}
                onEditImage={onEditImage}
                isSinglePage={true}
                singlePageFit={singlePageFit}
                isPriority={true}
                showControls={showControls}
              />
            )}
          </div>
        )}
      </main>

      {readerMode === 'infinite' && currentPage > 1 && (
        <button
          type="button"
          onClick={(event) => {
            event.stopPropagation();
            handleJumpToPage(1);
          }}
          className={`${touchDevice ? 'fixed' : 'absolute'} right-3 z-40 flex h-11 w-11 min-h-11 min-w-11 items-center justify-center rounded-full border border-zinc-700 bg-zinc-800/95 text-zinc-200 shadow-lg transition-all duration-200 hover:bg-zinc-700 focus-visible:outline-2 focus-visible:outline-indigo-400 sm:right-4 ${
            showControls ? 'opacity-100 pointer-events-auto scale-100' : 'opacity-0 pointer-events-none scale-90'
          }`}
          style={{
            bottom: seriesNavigation
              ? 'calc(5.75rem + env(safe-area-inset-bottom, 0px))'
              : 'calc(1rem + env(safe-area-inset-bottom, 0px))',
          }}
          title={getReaderTitle('Go to top', touchDevice)}
          aria-label="Go to top"
        >
          <Icon icon="carbon:arrow-up" className="h-5 w-5" />
        </button>
      )}

      {seriesNavigation && series && (
        <nav
          aria-label="Series navigation"
          className={`fixed bottom-0 left-0 right-0 z-40 border-t border-zinc-800/80 bg-zinc-950/95 px-3 py-2 backdrop-blur-sm transition-all duration-200 ${
            showControls
              ? 'translate-y-0 opacity-100 visible pointer-events-auto'
              : 'translate-y-full opacity-0 invisible pointer-events-none'
          }`}
          style={{
            paddingBottom: 'calc(0.5rem + env(safe-area-inset-bottom, 0px))',
          }}
          onClick={(event) => event.stopPropagation()}
        >
          <div className="mx-auto flex w-full max-w-3xl items-center gap-1.5 sm:gap-2">
            <button
              type="button"
              aria-label="Previous Manga"
              title={getReaderTitle('Previous Manga', touchDevice)}
              disabled={!seriesNavigation.previous || isLoadingManga}
              onClick={() => seriesNavigation.previous && void onSelectManga?.(seriesNavigation.previous)}
              className="flex min-h-9 sm:min-h-11 min-w-9 sm:min-w-11 shrink-0 items-center justify-center rounded-lg border border-zinc-700 bg-zinc-800 px-2 sm:px-2.5 py-1.5 text-xs font-semibold text-zinc-200 hover:bg-zinc-700 disabled:cursor-not-allowed disabled:opacity-40 sm:gap-1.5"
            >
              <Icon icon="carbon:chevron-left" className="h-4 w-4" />
              <span className="hidden sm:inline">Previous Manga</span>
            </button>
            <label className="min-w-0 flex-1 text-xs text-zinc-400">
              <span className="sr-only">Manga in series</span>
              <select
                aria-label="Manga in series"
                value={mangaId || mangaTitle}
                disabled={isLoadingManga}
                onPointerDown={pauseControlsHide}
                onFocus={pauseControlsHide}
                onBlur={revealControls}
                onChange={(event) => {
                  const member = series.members.find((entry) => entry.id === event.target.value);
                  if (member) void onSelectManga?.(member);
                }}
                className="min-h-9 sm:min-h-11 w-full min-w-0 rounded-lg border border-zinc-700 bg-zinc-800 px-2 text-xs font-semibold text-zinc-100"
              >
                {series.members.map((member) => (
                  <option key={member.id} value={member.id}>{member.position}. {member.title}</option>
                ))}
              </select>
            </label>
            <button
              type="button"
              aria-label="Next Manga"
              title={getReaderTitle('Next Manga', touchDevice)}
              disabled={!seriesNavigation.next || isLoadingManga}
              onClick={() => seriesNavigation.next && void onSelectManga?.(seriesNavigation.next)}
              className="flex min-h-9 sm:min-h-11 min-w-9 sm:min-w-11 shrink-0 items-center justify-center rounded-lg border border-zinc-700 bg-zinc-800 px-2.5 py-1.5 text-xs font-semibold text-zinc-200 hover:bg-zinc-700 disabled:cursor-not-allowed disabled:opacity-40 sm:gap-1.5"
            >
              <span className="hidden sm:inline">Next Manga</span>
              <Icon icon="carbon:chevron-right" className="h-4 w-4" />
            </button>
            <button
              type="button"
              aria-label="Close reader"
              title={getReaderTitle('Close reader', touchDevice)}
              onClick={handleCloseReader}
              className="flex min-h-9 sm:min-h-11 min-w-9 sm:min-w-11 shrink-0 items-center justify-center rounded-lg border border-zinc-700 bg-zinc-800 px-2.5 py-1.5 text-xs font-semibold text-zinc-200 hover:bg-zinc-700"
            >
              <Icon icon="carbon:close" className="h-4 w-4" />
            </button>
          </div>
          {seriesError && <p role="alert" className="mx-auto max-w-3xl pt-1 text-center text-xs text-red-400">Could not load {seriesError}</p>}
        </nav>
      )}

      {/* Footer Scrubber & Navigation Bar (single-page mode only) */}
      {readerMode === 'single' && (
        <footer
          aria-label="Reader navigation"
          onClick={(e) => e.stopPropagation()}
          className={`${touchDevice ? 'fixed' : 'absolute'} bottom-0 left-0 right-0 px-4 sm:px-6 py-2.5 bg-zinc-950 border-t border-zinc-800/80 z-30 flex items-center justify-between gap-4 transition-all duration-200 ${
            showControls
              ? 'translate-y-0 opacity-100 visible pointer-events-auto'
              : 'translate-y-full opacity-0 invisible pointer-events-none'
          }`}
          style={{
            paddingBottom: 'calc(0.625rem + env(safe-area-inset-bottom, 0px))',
            ...(seriesNavigation ? { bottom: 'calc(4.5rem + env(safe-area-inset-bottom, 0px))' } : {}),
          }}
        >
        {/* Prev Page Button */}
        <button
          type="button"
          onClick={handlePrevPage}
          disabled={currentPage <= 1}
          className="min-h-11 min-w-11 flex items-center space-x-1 px-3 py-1.5 rounded-lg bg-zinc-800 hover:bg-zinc-700 disabled:opacity-30 disabled:pointer-events-none text-xs font-medium text-zinc-200 transition-colors shrink-0"
          title={getReaderTitle('Previous Page', touchDevice)}
          aria-label="Previous page"
        >
          <Icon icon="carbon:chevron-left" className="w-4 h-4" />
          <span className="hidden sm:inline">Prev</span>
        </button>

        {/* Center Page Scrubber Slider */}
        <div className="flex-1 flex items-center space-x-3 max-w-xl mx-auto">
          <input
            type="range"
            min={1}
            max={images.length}
            value={currentPage}
            onChange={(e) => handleJumpToPage(parseInt(e.target.value, 10))}
            className="w-full h-1.5 bg-zinc-700 rounded-lg appearance-none cursor-pointer accent-indigo-500 focus:outline-hidden"
            title={getReaderTitle('Drag to scrub through pages', touchDevice)}
            aria-label="Jump to page"
          />

          <div className="flex items-center space-x-1 font-mono text-xs text-zinc-400 shrink-0">
            <span className="text-zinc-200 font-semibold">{currentPage}</span>
            <span className="text-zinc-500">/</span>
            <span>{images.length}</span>
          </div>
        </div>

        {/* Next Page Button */}
        <button
          type="button"
          onClick={handleNextPage}
          disabled={currentPage >= images.length}
          className="min-h-11 min-w-11 flex items-center space-x-1 px-3 py-1.5 rounded-lg bg-zinc-800 hover:bg-zinc-700 disabled:opacity-30 disabled:pointer-events-none text-xs font-medium text-zinc-200 transition-colors shrink-0"
          title={getReaderTitle('Next Page', touchDevice)}
          aria-label="Next page"
        >
          <span className="hidden sm:inline">Next</span>
          <Icon icon="carbon:chevron-right" className="w-4 h-4" />
        </button>
        </footer>
      )}
    </div>
  ), document.body);
};
