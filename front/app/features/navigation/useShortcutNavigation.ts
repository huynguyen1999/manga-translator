import { useEffect } from "react";
import type { NavigateFunction } from "react-router";
import type { ParsedRoute } from "@/utils/routeState";

export const NAV_SHORTCUT_TARGETS = [
  "/studio",
  "/gallery",
  "/gallery?view=series",
  "/search-lab",
] as const;

export function getShortcutNavIndex(parsedRoute: ParsedRoute): number {
  if (parsedRoute.view === "studio") {
    return 0;
  }
  if (parsedRoute.view === "search") {
    return 3;
  }
  if (parsedRoute.view === "gallery") {
    if (parsedRoute.gallerySection === "series") {
      return 2;
    }
    return 1;
  }
  return 0;
}

export function useShortcutNavigation(parsedRoute: ParsedRoute, navigate: NavigateFunction) {
  useEffect(() => {
    const handleKeyDown = (event: KeyboardEvent) => {
      if (!(event.metaKey || event.ctrlKey)) return;

      const isNext = event.key === "ArrowRight" || event.key === "ArrowDown";
      const isPrev = event.key === "ArrowLeft" || event.key === "ArrowUp";
      if (!isNext && !isPrev) return;

      const activeEl = document.activeElement as HTMLElement | null;
      const activeTag = (activeEl?.tagName || "").toLowerCase();
      if (
        activeTag === "input" ||
        activeTag === "textarea" ||
        activeTag === "select" ||
        activeEl?.isContentEditable
      ) {
        return;
      }

      const currentIndex = getShortcutNavIndex(parsedRoute);
      const total = NAV_SHORTCUT_TARGETS.length;
      const nextIndex = isNext
        ? (currentIndex + 1) % total
        : (currentIndex - 1 + total) % total;

      event.preventDefault();
      navigate(NAV_SHORTCUT_TARGETS[nextIndex]);
    };

    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [navigate, parsedRoute.view, parsedRoute.gallerySection]);
}
