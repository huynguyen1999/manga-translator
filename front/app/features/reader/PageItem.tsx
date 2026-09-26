import React, { useState, useEffect, useMemo } from 'react';
import { Icon } from '@iconify/react';
import type { FinishedImage } from '@/types';
import { apiUrl, isPhoneDevice } from '@/utils/api';

export const isPhoneOrTouch = (): boolean => {
  if (typeof window === 'undefined') return false;
  const userAgent = typeof navigator === 'undefined' ? '' : navigator.userAgent;
  return isPhoneDevice(userAgent) || window.matchMedia('(pointer: coarse)').matches;
};

export function getReaderTitle(title: string, touchDevice: boolean): string | undefined {
  return touchDevice ? undefined : title;
}

export type ReaderMode = 'infinite' | 'single';
export type ReaderWidth = '60%' | '75%' | '90%' | '100%';
export type SinglePageFit = 'height' | 'width';

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
export function getImageUrl(image: FinishedImage): { url: string | null; isBlobUrl: boolean } {
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
export interface PageItemProps {
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

export const PageItem: React.FC<PageItemProps> = React.memo(
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
