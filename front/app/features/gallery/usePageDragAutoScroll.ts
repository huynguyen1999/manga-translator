import { useCallback, useEffect, useRef, type Dispatch, type SetStateAction } from 'react';

import { calculateDragAutoScrollSpeed } from '@/utils/resultGallery';

export function usePageDragAutoScroll(
  draggedPageId: string | null,
  setDraggedPageId: Dispatch<SetStateAction<string | null>>,
  setDragOverPageId: Dispatch<SetStateAction<string | null>>,
): () => void {
  const pageDragAutoScrollFrameRef = useRef<number | null>(null);
  const pageDragAutoScrollSpeedRef = useRef<number>(0);

  const stopPageDragAutoScroll = useCallback(() => {
    pageDragAutoScrollSpeedRef.current = 0;
    if (pageDragAutoScrollFrameRef.current !== null) {
      cancelAnimationFrame(pageDragAutoScrollFrameRef.current);
      pageDragAutoScrollFrameRef.current = null;
    }
  }, []);

  const runPageDragAutoScroll = useCallback(() => {
    const speed = pageDragAutoScrollSpeedRef.current;
    if (speed === 0) {
      pageDragAutoScrollFrameRef.current = null;
      return;
    }
    window.scrollBy(0, speed);
    pageDragAutoScrollFrameRef.current = requestAnimationFrame(runPageDragAutoScroll);
  }, []);

  const startPageDragAutoScroll = useCallback(() => {
    if (pageDragAutoScrollFrameRef.current === null) {
      pageDragAutoScrollFrameRef.current = requestAnimationFrame(runPageDragAutoScroll);
    }
  }, [runPageDragAutoScroll]);

  useEffect(() => {
    if (!draggedPageId) {
      stopPageDragAutoScroll();
      return;
    }

    const handleDragOver = (e: DragEvent) => {
      const speed = calculateDragAutoScrollSpeed(e.clientY, window.innerHeight);
      if (speed !== 0) {
        pageDragAutoScrollSpeedRef.current = speed;
        startPageDragAutoScroll();
      } else {
        stopPageDragAutoScroll();
      }
    };

    const handleDragEnd = () => {
      stopPageDragAutoScroll();
      setDraggedPageId(null);
      setDragOverPageId(null);
    };

    window.addEventListener('dragover', handleDragOver, { passive: true });
    window.addEventListener('dragend', handleDragEnd);
    window.addEventListener('drop', handleDragEnd);

    return () => {
      stopPageDragAutoScroll();
      window.removeEventListener('dragover', handleDragOver);
      window.removeEventListener('dragend', handleDragEnd);
      window.removeEventListener('drop', handleDragEnd);
    };
  }, [draggedPageId, startPageDragAutoScroll, stopPageDragAutoScroll]);

  useEffect(() => stopPageDragAutoScroll, [stopPageDragAutoScroll]);

  return stopPageDragAutoScroll;
}
