import React, { useEffect, useRef, useState } from "react";

export const ImageThumb = ({
  file,
  pageNum,
  isSelected,
  onClick,
}: {
  file: File;
  pageNum: number;
  isSelected: boolean;
  onClick: () => void;
}) => {
  const [url, setUrl] = useState<string | null>(null);
  useEffect(() => {
    const next = URL.createObjectURL(file);
    setUrl(next);
    return () => URL.revokeObjectURL(next);
  }, [file]);

  return (
    <button
      type="button"
      onClick={onClick}
      title={`View page ${pageNum} in preview`}
      className={`relative flex h-20 w-[58px] cursor-pointer items-center justify-center overflow-hidden rounded-md bg-zinc-100 transition-all outline-none dark:bg-zinc-800/90 ${
        isSelected
          ? "ring-2 ring-indigo-500 ring-offset-2 dark:ring-offset-zinc-900 shadow-md scale-[1.02]"
          : "hover:opacity-90"
      }`}
    >
      {url ? (
        <img
          src={url}
          alt={`Page ${pageNum}`}
          loading="lazy"
          className="pointer-events-none h-full w-full object-cover select-none"
        />
      ) : (
        <span className="font-mono text-xs font-medium text-zinc-400">p{pageNum}</span>
      )}
    </button>
  );
};

export const LargePagePreview: React.FC<{ file: File; pageNum: number; className?: string }> = ({
  file,
  pageNum,
  className = "h-full w-full object-contain select-none pointer-events-none",
}) => {
  const [url, setUrl] = useState<string | null>(null);
  useEffect(() => {
    const next = URL.createObjectURL(file);
    setUrl(next);
    return () => URL.revokeObjectURL(next);
  }, [file]);

  if (!url) {
    return (
      <div className="flex h-full min-h-[220px] w-full items-center justify-center bg-zinc-950/80 text-zinc-400">
        <span className="font-mono text-xs">Loading page {pageNum}…</span>
      </div>
    );
  }

  return (
    <img
      src={url}
      alt={`Page ${pageNum}`}
      loading="eager"
      className={className}
    />
  );
};

export interface SplitHandleProps {
  afterPage: number;
  minPage: number;
  maxPage: number;
  onUpdateBoundary: (afterPage: number, newAfterPage: number) => void;
  onRemove: (afterPage: number) => void;
  onHoverStart?: (afterPage: number) => void;
  onHoverEnd?: () => void;
  onDragMove?: (pendingPage: number) => void;
  disabled?: boolean;
}

export const SplitHandle: React.FC<SplitHandleProps> = ({
  afterPage,
  minPage,
  maxPage,
  onUpdateBoundary,
  onRemove,
  onHoverStart,
  onHoverEnd,
  onDragMove,
  disabled = false,
}) => {
  const [isDragging, setIsDragging] = useState(false);
  const [pendingPage, setPendingPage] = useState(afterPage);
  const [dragOffsetPx, setDragOffsetPx] = useState(0);
  const startXRef = useRef(0);

  const STEP_PX = 72; // thumbnail width (58px) + gap (14px)

  const handlePointerDown = (e: React.PointerEvent<HTMLDivElement>) => {
    if (disabled) return;
    if ((e.target as HTMLElement).closest("button")) return;
    e.preventDefault();
    e.currentTarget.setPointerCapture(e.pointerId);
    startXRef.current = e.clientX;
    setIsDragging(true);
    setPendingPage(afterPage);
    setDragOffsetPx(0);
    onHoverStart?.(afterPage);
  };

  const handlePointerMove = (e: React.PointerEvent<HTMLDivElement>) => {
    if (!isDragging) return;
    const deltaX = e.clientX - startXRef.current;
    const steps = Math.trunc(deltaX / STEP_PX);
    const next = Math.max(minPage, Math.min(maxPage, afterPage + steps));
    setPendingPage(next);
    setDragOffsetPx((next - afterPage) * STEP_PX);
    onDragMove?.(next);
  };

  const endDrag = () => {
    if (!isDragging) return;
    setIsDragging(false);
    setDragOffsetPx(0);
    if (pendingPage !== afterPage) {
      onUpdateBoundary(afterPage, pendingPage);
    }
  };

  const handleKeyDown = (e: React.KeyboardEvent<HTMLDivElement>) => {
    if (disabled) return;
    const step = e.shiftKey ? 5 : 1;
    let next: number | null = null;
    if (e.key === "ArrowLeft") {
      next = Math.max(minPage, afterPage - step);
    } else if (e.key === "ArrowRight") {
      next = Math.min(maxPage, afterPage + step);
    }
    if (next !== null && next !== afterPage) {
      e.preventDefault();
      onUpdateBoundary(afterPage, next);
      onHoverStart?.(next);
    }
  };

  return (
    <div
      role="slider"
      tabIndex={disabled ? -1 : 0}
      aria-label={`Story boundary after page ${afterPage}`}
      aria-valuemin={minPage}
      aria-valuemax={maxPage}
      aria-valuenow={isDragging ? pendingPage : afterPage}
      aria-valuetext={`Boundary after page ${isDragging ? pendingPage : afterPage}`}
      onPointerDown={handlePointerDown}
      onPointerMove={handlePointerMove}
      onPointerUp={endDrag}
      onPointerCancel={endDrag}
      onKeyDown={handleKeyDown}
      onMouseEnter={() => !isDragging && onHoverStart?.(afterPage)}
      onMouseLeave={() => !isDragging && onHoverEnd?.()}
      style={{
        transform: isDragging ? `translateX(${dragOffsetPx}px)` : undefined,
      }}
      className={`group/handle relative z-20 -mt-2 flex h-24 w-1.5 shrink-0 cursor-ew-resize flex-col items-center justify-between rounded-full bg-indigo-600 select-none touch-none dark:bg-indigo-500 ${
        isDragging
          ? "scale-105 shadow-lg ring-4 ring-indigo-400/50"
          : "shadow-xs transition-transform duration-75 hover:bg-indigo-500 hover:ring-2 hover:ring-indigo-300 dark:hover:ring-indigo-700"
      }`}
    >
      {/* Top grip indicator */}
      <span className="pointer-events-none absolute -top-4 left-1/2 -translate-x-1/2 rotate-90 select-none font-mono text-[9px] font-bold tracking-tighter text-zinc-400 dark:text-zinc-500">
        ⋮⋮
      </span>

      {/* Remove button */}
      <button
        type="button"
        tabIndex={-1}
        onClick={(e) => {
          e.stopPropagation();
          onRemove(afterPage);
        }}
        title={`Remove split after page ${afterPage}`}
        className="absolute -bottom-5 left-1/2 flex h-4.5 w-4.5 -translate-x-1/2 cursor-pointer items-center justify-center rounded-full border border-zinc-300 bg-white text-[11px] font-bold text-zinc-500 opacity-0 shadow-xs transition-all group-hover/handle:opacity-100 hover:border-rose-500 hover:bg-rose-500 hover:text-white dark:border-zinc-700 dark:bg-zinc-800 dark:text-zinc-400 dark:hover:border-rose-500 dark:hover:bg-rose-500 dark:hover:text-white"
      >
        ×
      </button>

      {/* Floating tooltip */}
      {isDragging && (
        <div className="pointer-events-none absolute -bottom-11 left-1/2 z-30 -translate-x-1/2 whitespace-nowrap rounded-md border border-zinc-700 bg-zinc-900 px-2 py-1 font-mono text-[11px] text-zinc-100 shadow-xl dark:border-zinc-700 dark:bg-zinc-800">
          after page {pendingPage}
        </div>
      )}
    </div>
  );
};
