import React, { useState, useEffect, useLayoutEffect, useRef, useCallback, useMemo } from 'react';
import { createPortal } from 'react-dom';
import { Icon } from '@iconify/react';
import type { FinishedImage, SeriesDetail, SeriesMember } from '@/types';
import { apiUrl } from '@/utils/api';
import { getMangaReadProgress } from '@/utils/resultGallery';
import { fetchReadingProgress, saveReadingProgress } from '@/utils/readingProgress';
import { getImageUrl, getReaderTitle, isPhoneOrTouch } from '@/features/reader/PageItem';
import type { ReaderMode, ReaderWidth, SinglePageFit } from '@/features/reader/PageItem';
import { ReaderWidthMenu } from '@/features/reader/ReaderWidthMenu';
import { ReaderPageStage } from '@/features/reader/ReaderPageStage';
import { getReaderPreloadIndices, getReaderPriorityIndices, getSeriesMemberNavigation } from '@/features/reader/readerUtils';
export { getReaderPreloadIndices, getReaderPriorityIndices, getSeriesMemberNavigation } from '@/features/reader/readerUtils';
export { getReaderTitle, isPhoneOrTouch } from '@/features/reader/PageItem';
export type { ReaderMode, ReaderWidth, SinglePageFit } from '@/features/reader/PageItem';

const useClientLayoutEffect = typeof window === 'undefined' ? useEffect : useLayoutEffect;

type SafariFullscreenDocument = Document & {
  webkitFullscreenElement?: Element | null;
  webkitExitFullscreen?: () => Promise<void> | void;
};

type SafariFullscreenElement = HTMLElement & {
  webkitRequestFullscreen?: () => Promise<void> | void;
};

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

  const renderPageJumpForm = (mobile = false) => (
    <form
      onSubmit={handleSubmitPageJump}
      className={mobile
        ? 'grid w-full grid-cols-[auto_minmax(0,1fr)_auto_auto] items-center gap-2 border-t border-zinc-800/80 pt-2'
        : 'flex items-center gap-1'}
      aria-label="Go to page"
    >
      <label htmlFor="reader-page-input" className={mobile ? 'text-xs font-medium text-zinc-300' : 'sr-only'}>
        {mobile ? 'Page' : 'Page number'}
      </label>
      <input
        id="reader-page-input"
        type="number"
        inputMode="numeric"
        enterKeyHint="go"
        min={1}
        max={images.length}
        value={pageInput}
        onChange={(event) => setPageInput(event.target.value)}
        className={`${mobile ? 'h-11 w-full min-w-0 text-base' : 'h-8 sm:h-10 w-11 sm:w-14 text-xs'} rounded-lg border border-zinc-700/60 bg-zinc-800 px-1 text-center font-mono text-zinc-200 focus:border-indigo-500 focus:outline-hidden focus:ring-1 focus:ring-indigo-500`}
        aria-label="Page number"
      />
      {mobile && <span className="whitespace-nowrap text-xs text-zinc-400">of {images.length}</span>}
      <button
        type="submit"
        className={mobile
          ? 'min-h-11 min-w-11 rounded-lg bg-indigo-600 px-3 text-sm font-semibold text-white shadow-sm hover:bg-indigo-500 focus-visible:outline-2 focus-visible:outline-indigo-400'
          : 'min-h-8 sm:min-h-10 rounded-lg border border-zinc-700/60 bg-zinc-800 px-2 sm:px-2.5 text-xs text-zinc-300 hover:bg-zinc-700 hover:text-white'}
        title={getReaderTitle('Go to page', touchDevice)}
      >
        Go
      </button>
    </form>
  );

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
        className={`${touchDevice ? 'fixed flex-col items-stretch gap-2' : 'absolute flex-row items-center gap-1.5 sm:gap-3'} top-0 left-0 right-0 flex justify-between px-2.5 sm:px-6 py-2 bg-zinc-900/95 border-b border-zinc-800/80 z-30 transition-all duration-200 ${
          showControls
            ? 'translate-y-0 opacity-100 visible pointer-events-auto'
            : '-translate-y-full opacity-0 invisible pointer-events-none'
        }`}
        style={{
          paddingTop: 'calc(0.5rem + env(safe-area-inset-top, 0px))',
        }}
      >
        <div className={touchDevice ? 'flex min-w-0 items-center justify-between gap-1.5' : 'contents'}>
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
              <p className={`${touchDevice ? 'hidden' : ''} text-[10px] sm:text-xs text-zinc-300 font-mono truncate`}>
                Page {currentPage} of {images.length}
              </p>
            </div>
          </div>

          {/* Center Controls: Reading Mode Toggle & Page Width */}
          <div className="flex items-center space-x-1 sm:space-x-2.5 shrink-0">
            {readerMode === 'single' && !touchDevice && (
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

            {!touchDevice && renderPageJumpForm()}

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

            <ReaderWidthMenu
              touchDevice={touchDevice}
              readerMode={readerMode}
              readerWidth={readerWidth}
              showWidthMenu={showWidthMenu}
              singlePageFit={singlePageFit}
              onToggle={() => setShowWidthMenu((prev) => !prev)}
              onClose={() => setShowWidthMenu(false)}
              onSetWidth={handleSetWidth}
              onSetSinglePageFit={handleSetSinglePageFit}
              onStartOver={handleStartOver}
            />
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
        </div>
        {touchDevice && renderPageJumpForm(true)}
      </header>

      <ReaderPageStage
        images={images}
        mangaTitle={mangaTitle}
        readerMode={readerMode}
        touchDevice={touchDevice}
        currentPage={currentPage}
        readerWidth={readerWidth}
        singlePageFit={singlePageFit}
        showControls={showControls}
        priorityPageIndices={priorityPageIndices}
        pageRefs={pageRefs}
        scrollContainerRef={scrollContainerRef}
        singlePageScrollRef={singlePageScrollRef}
        onEditImage={onEditImage}
        onScroll={handleScroll}
        onToggleControls={toggleControls}
        onJumpToPage={handleJumpToPage}
        onPreviousPage={handlePrevPage}
        onNextPage={handleNextPage}
      />

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
