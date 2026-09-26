import { useCallback, useEffect, useRef, useState, type MouseEvent as ReactMouseEvent } from 'react';

export function usePageDetailSidebarResize() {
  const [sidebarWidth, setSidebarWidth] = useState(480);
  const modalContainerRef = useRef<HTMLDivElement>(null);
  const sidebarResizeRef = useRef<{ startX: number; startWidth: number } | null>(null);
  const sidebarWidthRef = useRef(sidebarWidth);
  const sidebarResizeFrameRef = useRef<number | null>(null);
  const pendingSidebarWidthRef = useRef<number | null>(null);
  const sidebarResizeCleanupRef = useRef<(() => void) | null>(null);

  useEffect(() => () => sidebarResizeCleanupRef.current?.(), []);

  const handleResizeMouseDown = useCallback((event: ReactMouseEvent) => {
    event.preventDefault();
    sidebarResizeRef.current = { startX: event.clientX, startWidth: sidebarWidthRef.current };
    const onMouseMove = (moveEvent: MouseEvent) => {
      if (!sidebarResizeRef.current) return;
      pendingSidebarWidthRef.current = Math.max(
        280,
        Math.min(700, sidebarResizeRef.current.startWidth + sidebarResizeRef.current.startX - moveEvent.clientX),
      );
      if (sidebarResizeFrameRef.current !== null) return;
      sidebarResizeFrameRef.current = requestAnimationFrame(() => {
        sidebarResizeFrameRef.current = null;
        const nextWidth = pendingSidebarWidthRef.current;
        if (nextWidth !== null) modalContainerRef.current?.style.setProperty('--details-sidebar-width', `${nextWidth}px`);
      });
    };
    const cleanup = () => {
      document.removeEventListener('mousemove', onMouseMove);
      document.removeEventListener('mouseup', onMouseUp);
      if (sidebarResizeFrameRef.current !== null) {
        cancelAnimationFrame(sidebarResizeFrameRef.current);
        sidebarResizeFrameRef.current = null;
      }
      sidebarResizeRef.current = null;
      pendingSidebarWidthRef.current = null;
      sidebarResizeCleanupRef.current = null;
    };
    const onMouseUp = () => {
      const finalWidth = pendingSidebarWidthRef.current;
      if (finalWidth !== null) {
        sidebarWidthRef.current = finalWidth;
        setSidebarWidth(finalWidth);
        modalContainerRef.current?.style.setProperty('--details-sidebar-width', `${finalWidth}px`);
      }
      cleanup();
    };
    sidebarResizeCleanupRef.current = cleanup;
    document.addEventListener('mousemove', onMouseMove);
    document.addEventListener('mouseup', onMouseUp);
  }, []);

  return { sidebarWidth, modalContainerRef, handleResizeMouseDown };
}
