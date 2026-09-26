import { useCallback, useEffect, type Dispatch, type SetStateAction } from 'react';
import { apiUrl } from '@/utils/api';
import { buildPageViewUrl } from '@/utils/routeState';
import type { FinishedImage } from '@/types';
import type { PageDetailViewMode } from './PageDetailHeader';
import type { ResolvedImageUrls } from '@/utils/pageDetailTiming';

interface PageDetailActionOptions {
  image: FinishedImage;
  viewMode: PageDetailViewMode;
  isOriginal: boolean;
  resolvedImageUrls: ResolvedImageUrls;
  images: FinishedImage[];
  currentIndex?: number;
  onClose: () => void;
  onNavigate?: (index: number) => void;
  onDownload?: (image: FinishedImage) => void;
  onRetry?: (image: FinishedImage) => void | Promise<void>;
  onRetryFromStage?: (image: FinishedImage, stageId: string) => void | Promise<void>;
  onRerender?: (image: FinishedImage) => void | Promise<void>;
  isRetrying: boolean;
  setIsRetrying: Dispatch<SetStateAction<boolean>>;
  retryStatus: 'queued' | 'error' | null;
  setRetryStatus: Dispatch<SetStateAction<'queued' | 'error' | null>>;
  isRerendering: boolean;
  setIsRerendering: Dispatch<SetStateAction<boolean>>;
  rerenderStatus: 'queued' | 'error' | null;
  setRerenderStatus: Dispatch<SetStateAction<'queued' | 'error' | null>>;
  setCopyLinkStatus: Dispatch<SetStateAction<'copied' | 'error' | null>>;
  setZoomLevel: Dispatch<SetStateAction<number>>;
}

export function usePageDetailActions({
  image,
  viewMode,
  isOriginal,
  resolvedImageUrls,
  images,
  currentIndex,
  onClose,
  onNavigate,
  onDownload,
  onRetry,
  onRetryFromStage,
  onRerender,
  isRetrying,
  setIsRetrying,
  retryStatus,
  setRetryStatus,
  isRerendering,
  setIsRerendering,
  rerenderStatus,
  setRerenderStatus,
  setCopyLinkStatus,
  setZoomLevel,
}: PageDetailActionOptions) {
  const handleDownload = useCallback(() => {
    if (onDownload) {
      onDownload(image);
      return;
    }
    const targetSource = viewMode === 'original' || viewMode === 'speech-bubbles'
      ? resolvedImageUrls.originalUrl
      : viewMode === 'inpainted'
      ? resolvedImageUrls.inpaintedUrl
      : resolvedImageUrls.resultUrl;
    if (!targetSource) return;
    const isBlob = targetSource instanceof Blob;
    const url = isBlob ? URL.createObjectURL(targetSource) : apiUrl(targetSource);
    const anchor = document.createElement('a');
    anchor.href = url;
    const prefix = isOriginal || viewMode === 'original' || viewMode === 'speech-bubbles' ? 'original' : viewMode === 'inpainted' ? 'inpainted' : 'translated';
    const safeName = (image.originalName && image.originalName !== 'Unknown') ? image.originalName : `${image.folder || 'page'}.png`;
    anchor.download = `${prefix}_${safeName}`;
    document.body.appendChild(anchor);
    anchor.click();
    document.body.removeChild(anchor);
    if (isBlob) setTimeout(() => URL.revokeObjectURL(url), 1000);
  }, [image, onDownload, resolvedImageUrls.resultUrl, resolvedImageUrls.originalUrl, resolvedImageUrls.inpaintedUrl, viewMode, isOriginal]);

  const handleCopyLink = useCallback(async () => {
    if (!image.folder) return;
    const url = new URL(buildPageViewUrl(image.folder), window.location.origin).href;
    let copied = false;
    if (navigator.clipboard?.writeText) {
      try {
        await navigator.clipboard.writeText(url);
        copied = true;
      } catch {
        // Fall back to selection-based copying when clipboard access is denied.
      }
    }
    try {
      if (!copied) {
        const textarea = document.createElement('textarea');
        textarea.value = url;
        textarea.style.position = 'fixed';
        textarea.style.opacity = '0';
        document.body.appendChild(textarea);
        textarea.select();
        try {
          copied = document.execCommand('copy');
        } finally {
          document.body.removeChild(textarea);
        }
      }
      if (!copied) throw new Error('Clipboard copy failed');
      setCopyLinkStatus('copied');
    } catch {
      setCopyLinkStatus('error');
    }
  }, [image.folder, setCopyLinkStatus]);

  const handleRetry = useCallback(async () => {
    if (!onRetry || isRetrying || retryStatus === 'queued') return;
    setIsRetrying(true);
    setRetryStatus(null);
    try {
      await onRetry(image);
      setRetryStatus('queued');
    } catch {
      setRetryStatus('error');
    } finally {
      setIsRetrying(false);
    }
  }, [image, isRetrying, onRetry, retryStatus, setIsRetrying, setRetryStatus]);

  const queueRetryFromStage = useCallback(async (stageId: string) => {
    if (!onRetryFromStage) return;
    setRetryStatus(null);
    try {
      await onRetryFromStage(image, stageId);
      setRetryStatus('queued');
    } catch (error) {
      setRetryStatus('error');
      throw error;
    }
  }, [image, onRetryFromStage, setRetryStatus]);

  const handleRerender = useCallback(async () => {
    if (!onRerender || isRerendering || rerenderStatus === 'queued') return;
    setIsRerendering(true);
    setRerenderStatus(null);
    try {
      await onRerender(image);
      setRerenderStatus('queued');
    } catch {
      setRerenderStatus('error');
    } finally {
      setIsRerendering(false);
    }
  }, [image, isRerendering, onRerender, rerenderStatus, setIsRerendering, setRerenderStatus]);

  const activeIndex = currentIndex ?? (images.length > 0 ? images.findIndex((item) => item.id === image.id) : -1);
  const hasPrev = images.length > 1 && activeIndex > 0;
  const hasNext = images.length > 1 && activeIndex !== -1 && activeIndex < images.length - 1;

  const handlePrev = useCallback(() => {
    if (images.length <= 1) return;
    const prevIdx = activeIndex <= 0 ? images.length - 1 : activeIndex - 1;
    onNavigate?.(prevIdx);
  }, [activeIndex, images.length, onNavigate]);

  const handleNext = useCallback(() => {
    if (images.length <= 1) return;
    const nextIdx = activeIndex >= images.length - 1 ? 0 : activeIndex + 1;
    onNavigate?.(nextIdx);
  }, [activeIndex, images.length, onNavigate]);

  useEffect(() => {
    const handleKeyDown = (event: KeyboardEvent) => {
      const activeTag = (document.activeElement?.tagName || '').toLowerCase();
      if (activeTag === 'input' || activeTag === 'textarea' || activeTag === 'select') return;
      if (event.key === 'Escape') {
        event.preventDefault();
        onClose();
        return;
      }
      if (images.length > 1) {
        if (event.key === 'ArrowLeft') {
          event.preventDefault();
          handlePrev();
          return;
        }
        if (event.key === 'ArrowRight') {
          event.preventDefault();
          handleNext();
          return;
        }
      }
      if (event.key === '+' || event.key === '=') {
        event.preventDefault();
        setZoomLevel((prev) => Math.min(3, prev + 0.25));
      } else if (event.key === '-' || event.key === '_') {
        event.preventDefault();
        setZoomLevel((prev) => Math.max(0.5, prev - 0.25));
      } else if (event.key === '0') {
        event.preventDefault();
        setZoomLevel(1);
      }
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [handleNext, handlePrev, images.length, onClose, setZoomLevel]);

  return {
    activeIndex,
    hasPrev,
    hasNext,
    handleDownload,
    handleCopyLink,
    handleRetry,
    queueRetryFromStage,
    handleRerender,
    handlePrev,
    handleNext,
  };
}
