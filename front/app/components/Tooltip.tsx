import React, { useState, useRef, useEffect, useCallback, useId } from "react";
import { Icon } from "@iconify/react";

export interface TooltipProps {
  content: string | React.ReactNode;
  children?: React.ReactNode;
  className?: string;
  position?: "top" | "bottom" | "auto";
}

export const Tooltip: React.FC<TooltipProps> = ({
  content,
  children,
  className = "",
  position = "auto",
}) => {
  const [isVisible, setIsVisible] = useState(false);
  const [coords, setCoords] = useState<{ top: number; left: number; placeAbove: boolean } | null>(null);
  const triggerRef = useRef<HTMLButtonElement | null>(null);
  const tooltipRef = useRef<HTMLDivElement | null>(null);
  const tooltipId = useId();

  const updatePosition = useCallback(() => {
    if (!triggerRef.current) return;
    const rect = triggerRef.current.getBoundingClientRect();
    const viewport = window.visualViewport;
    const viewportWidth = viewport?.width ?? window.innerWidth;
    const viewportHeight = viewport?.height ?? window.innerHeight;

    // Estimate tooltip height ~50px, check if there is room above
    const placeAbove =
      position === "top" ||
      (position === "auto" && rect.top > 80) ||
      (position === "bottom" && rect.bottom + 80 > viewportHeight && rect.top > 80);

    const top = placeAbove ? rect.top - 8 : rect.bottom + 8;
    // Align centered or biased towards screen bounds
    const horizontalCenter = rect.left + rect.width / 2;
    const clampedCenter = Math.max(130, Math.min(viewportWidth - 130, horizontalCenter));

    setCoords({
      top,
      left: clampedCenter,
      placeAbove,
    });
  }, [position]);

  const showTooltip = useCallback(() => {
    updatePosition();
    setIsVisible(true);
  }, [updatePosition]);

  const hideTooltip = useCallback(() => {
    setIsVisible(false);
  }, []);

  const toggleTooltip = useCallback(
    (e: React.MouseEvent) => {
      e.preventDefault();
      e.stopPropagation();
      if (isVisible) {
        hideTooltip();
      } else {
        showTooltip();
      }
    },
    [isVisible, hideTooltip, showTooltip]
  );

  // Hide on scroll (phone requirement: hide tooltip when scrolling)
  useEffect(() => {
    if (!isVisible) return;

    const handleScroll = () => {
      hideTooltip();
    };

    const handlePointerDownOutside = (e: PointerEvent) => {
      if (
        triggerRef.current &&
        !triggerRef.current.contains(e.target as Node) &&
        tooltipRef.current &&
        !tooltipRef.current.contains(e.target as Node)
      ) {
        hideTooltip();
      }
    };

    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        hideTooltip();
      }
    };

    // Listen on window scroll and touchmove (capturing phase to catch container scrolls too)
    window.addEventListener("scroll", handleScroll, { passive: true, capture: true });
    window.addEventListener("touchmove", handleScroll, { passive: true });
    window.visualViewport?.addEventListener("resize", updatePosition);
    window.visualViewport?.addEventListener("scroll", handleScroll);
    document.addEventListener("pointerdown", handlePointerDownOutside);
    document.addEventListener("keydown", handleKeyDown);

    return () => {
      window.removeEventListener("scroll", handleScroll, { capture: true });
      window.removeEventListener("touchmove", handleScroll);
      window.visualViewport?.removeEventListener("resize", updatePosition);
      window.visualViewport?.removeEventListener("scroll", handleScroll);
      document.removeEventListener("pointerdown", handlePointerDownOutside);
      document.removeEventListener("keydown", handleKeyDown);
    };
  }, [isVisible, hideTooltip, updatePosition]);

  return (
    <div className={`relative inline-flex items-center ${className}`}>
      <button
        ref={triggerRef}
        type="button"
        aria-describedby={isVisible ? tooltipId : undefined}
        onClick={toggleTooltip}
        onMouseEnter={showTooltip}
        onMouseLeave={hideTooltip}
        onFocus={showTooltip}
        onBlur={hideTooltip}
        className="inline-flex items-center justify-center text-zinc-400 hover:text-zinc-600 dark:hover:text-zinc-200 cursor-help transition-colors focus:outline-hidden rounded p-0.5"
      >
        {children || <Icon icon="carbon:information" className="h-3.5 w-3.5" />}
      </button>

      {isVisible && coords && (
        <div
          ref={tooltipRef}
          id={tooltipId}
          role="tooltip"
          style={{
            position: "fixed",
            top: coords.top,
            left: coords.left,
            transform: coords.placeAbove ? "translate(-50%, -100%)" : "translate(-50%, 0)",
          }}
          className="z-50 max-w-[260px] sm:max-w-xs px-2.5 py-1.5 text-xs font-normal leading-relaxed text-zinc-100 bg-zinc-900/95 dark:bg-zinc-800/95 border border-zinc-700/80 dark:border-zinc-600/80 rounded-lg shadow-xl backdrop-blur-xs pointer-events-none animate-in fade-in zoom-in-95 duration-100"
        >
          {content}
          {/* Arrow */}
          <div
            className={`absolute left-1/2 -translate-x-1/2 w-2 h-2 rotate-45 bg-zinc-900 dark:bg-zinc-800 border-zinc-700/80 dark:border-zinc-600/80 ${
              coords.placeAbove
                ? "bottom-[-5px] border-r border-b"
                : "top-[-5px] border-l border-t"
            }`}
          />
        </div>
      )}
    </div>
  );
};
