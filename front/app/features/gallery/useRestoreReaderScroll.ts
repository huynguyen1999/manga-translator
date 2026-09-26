import { useEffect } from "react";
import type { Dispatch, SetStateAction } from "react";
import type { FinishedImage } from "@/types";
import type { ExitedReadPosition } from "./useMangaReaderActions";
import { mangaIdForTitle } from "@/utils/routeState";

type Options = {
  lastExitedReadPosition: ExitedReadPosition | null;
  setLastExitedReadPosition: Dispatch<SetStateAction<ExitedReadPosition | null>>;
  activeMangaFilter: string;
  viewMode: "cards" | "rows";
  expandedGroups: Record<string, boolean>;
  currentSingleGroupImages?: FinishedImage[];
  setHighlightedImageId: Dispatch<SetStateAction<string | null>>;
  setHighlightedMangaId: Dispatch<SetStateAction<string | null>>;
};

export const useRestoreReaderScroll = ({
  lastExitedReadPosition,
  setLastExitedReadPosition,
  activeMangaFilter,
  viewMode,
  expandedGroups,
  currentSingleGroupImages,
  setHighlightedImageId,
  setHighlightedMangaId,
}: Options) => {
  useEffect(() => {
    if (!lastExitedReadPosition) return;
    const { mangaTitle, mangaId, pageIndex, imageId } = lastExitedReadPosition;

    let frameId: number | null = null;
    let timeoutId: ReturnType<typeof setTimeout> | null = null;

    const performScroll = () => {
      let cardEl: HTMLElement | null = null;
      const isSingleView = activeMangaFilter === mangaTitle;
      const isRowExpanded = viewMode === "rows" && expandedGroups[mangaTitle];
      if (isSingleView || isRowExpanded) {
        if (imageId) {
          cardEl = document.querySelector<HTMLElement>(`[data-image-id="${imageId}"]`);
        }
        if (!cardEl && typeof pageIndex === "number" && pageIndex >= 0) {
          cardEl = document.querySelector<HTMLElement>(`[data-page-index="${pageIndex}"]`);
        }
      }

      if (!cardEl && mangaId) {
        cardEl = document.querySelector<HTMLElement>(`[data-manga-id="${mangaId}"]`);
      }
      if (!cardEl && mangaTitle) {
        cardEl = document.querySelector<HTMLElement>(`[data-manga-id="${mangaIdForTitle(mangaTitle)}"]`);
      }

      if (!cardEl) return;
      const prefersReducedMotion =
        typeof window !== "undefined" &&
        window.matchMedia?.("(prefers-reduced-motion: reduce)")?.matches;
      cardEl.scrollIntoView({
        behavior: prefersReducedMotion ? "auto" : "smooth",
        block: "center",
      });

      if (imageId && (isSingleView || isRowExpanded)) {
        setHighlightedImageId(imageId);
        timeoutId = setTimeout(() => {
          setHighlightedImageId((current) => (current === imageId ? null : current));
        }, 2000);
      } else if (mangaId) {
        setHighlightedMangaId(mangaId);
        timeoutId = setTimeout(() => {
          setHighlightedMangaId((current) => (current === mangaId ? null : current));
        }, 2000);
      }
      setLastExitedReadPosition(null);
    };

    frameId = requestAnimationFrame(() => {
      frameId = requestAnimationFrame(performScroll);
    });

    return () => {
      if (frameId) cancelAnimationFrame(frameId);
      if (timeoutId) clearTimeout(timeoutId);
    };
  }, [
    lastExitedReadPosition,
    activeMangaFilter,
    viewMode,
    expandedGroups,
    currentSingleGroupImages,
  ]);
};
