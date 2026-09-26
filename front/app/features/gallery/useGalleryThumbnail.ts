import { useCallback, useEffect, useRef, useState } from 'react';
import type { FinishedImage } from '@/types';
import {
  getGalleryFallbackUrl,
  getGalleryThumbnailUrl,
  loadedThumbnailUrls,
} from '@/utils/resultGallery';

export function useGalleryThumbnail(image: FinishedImage | null | undefined, variant: "detail" | "cover" = "detail") {
  const initialUrl = getGalleryThumbnailUrl(image, variant);
  const [src, setSrc] = useState<string | null>(() => initialUrl);
  const [hasError, setHasError] = useState(false);
  const [isLoaded, setIsLoaded] = useState(() => Boolean(initialUrl && loadedThumbnailUrls.has(initialUrl)));
  const retryCountRef = useRef(0);
  const retryTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    const nextSrc = getGalleryThumbnailUrl(image, variant);
    setSrc(nextSrc);
    setHasError(false);
    setIsLoaded(Boolean(nextSrc && loadedThumbnailUrls.has(nextSrc)));
    retryCountRef.current = 0;
    return () => {
      if (retryTimeoutRef.current) clearTimeout(retryTimeoutRef.current);
    };
  }, [image?.thumbnailUrl, image?.detailPreviewUrl, image?.coverUrl, image?.folder, image?.result, variant]);

  const handleLoad = useCallback(() => {
    if (src) {
      loadedThumbnailUrls.add(src);
      loadedThumbnailUrls.add(src.split('?')[0]);
    }
    setIsLoaded(true);
    setHasError(false);
    retryCountRef.current = 0;
  }, [src]);

  const handleError = useCallback(() => {
    if (!image) {
      setHasError(true);
      return;
    }

    // If this image already succeeded previously, keep the decoded image in memory
    if (src && (loadedThumbnailUrls.has(src) || loadedThumbnailUrls.has(src.split('?')[0]))) {
      return;
    }

    // Keep cover failures on small derivatives instead of loading final.png.
    const fallback = getGalleryFallbackUrl(image, variant);
    if (fallback && src && fallback !== src) {
      setSrc(fallback);
      return;
    }

    if (variant === "cover") {
      setHasError(true);
      return;
    }

    // Transient network or scroll abort retry
    if (retryCountRef.current < 2) {
      retryCountRef.current += 1;
      if (retryTimeoutRef.current) clearTimeout(retryTimeoutRef.current);
      retryTimeoutRef.current = setTimeout(() => {
        setSrc((prev) => {
          if (!prev) return prev;
          const clean = prev.split('?')[0];
          return `${clean}?retry=${Date.now()}`;
        });
      }, 400 * retryCountRef.current);
    } else {
      setHasError(true);
    }
  }, [image, src, variant]);

  return { src, hasError, isLoaded, handleLoad, handleError };
}
