import { useState, useEffect, useCallback } from 'react';
import type { RefObject } from 'react';
import type { ReaderMode } from './PageItem';

type SafariFullscreenDocument = Document & {
  webkitFullscreenElement?: Element | null;
  webkitExitFullscreen?: () => Promise<void> | void;
};

type SafariFullscreenElement = HTMLElement & {
  webkitRequestFullscreen?: () => Promise<void> | void;
};

interface UseReaderControlsOptions {
  containerRef: RefObject<HTMLDivElement | null>;
  scrollContainerRef: RefObject<HTMLDivElement | null>;
  readerMode: ReaderMode;
  touchDevice: boolean;
  imagesLength: number;
  handleNextPage: () => void;
  handlePrevPage: () => void;
  handleJumpToPage: (page: number) => void;
  revealControls: () => void;
  hideControls: () => void;
}

export function useReaderControls({
  containerRef,
  scrollContainerRef,
  readerMode,
  touchDevice,
  imagesLength,
  handleNextPage,
  handlePrevPage,
  handleJumpToPage,
  revealControls,
  hideControls,
}: UseReaderControlsOptions) {
  const [isFullscreen, setIsFullscreen] = useState(false);

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
  }, [containerRef, hideControls]);

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

  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
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

      if (['INPUT', 'TEXTAREA', 'SELECT'].includes((e.target as HTMLElement)?.tagName)) {
        return;
      }

      if (e.key === 'ArrowRight' || e.key === 'PageDown') {
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
        handleJumpToPage(imagesLength);
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
  }, [
    containerRef,
    scrollContainerRef,
    readerMode,
    touchDevice,
    imagesLength,
    handleNextPage,
    handlePrevPage,
    handleJumpToPage,
    revealControls,
    toggleFullscreen,
  ]);

  return { isFullscreen, toggleFullscreen };
}
