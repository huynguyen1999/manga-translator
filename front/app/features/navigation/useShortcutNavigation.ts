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
  if (parsedRoute.view === "studio") return 0;
  if (parsedRoute.view === "search") return 3;
  if (parsedRoute.view === "gallery") {
    return parsedRoute.gallerySection === "series" ? 2 : 1;
  }
  return 0;
}

export function getShortcutNavigationDirection(
  event: Pick<KeyboardEvent, "key" | "metaKey" | "ctrlKey">
): "next" | "prev" | null {
  if (!(event.metaKey || event.ctrlKey)) return null;
  if (event.key === "ArrowRight") return "next";
  if (event.key === "ArrowLeft") return "prev";
  return null;
}

export function useShortcutNavigation(parsedRoute: ParsedRoute, navigate: NavigateFunction) {
  useEffect(() => {
    const handleKeyDown = (event: KeyboardEvent) => {
      const direction = getShortcutNavigationDirection(event);
      if (!direction) return;

      const activeEl = document.activeElement as HTMLElement | null;
      const activeTag = (activeEl?.tagName || "").toLowerCase();
      if (activeTag === "input" || activeTag === "textarea" || activeTag === "select" || activeEl?.isContentEditable) {
        return;
      }

      const currentIndex = getShortcutNavIndex(parsedRoute);
      const total = NAV_SHORTCUT_TARGETS.length;
      const nextIndex = direction === "next"
        ? (currentIndex + 1) % total
        : (currentIndex - 1 + total) % total;

      event.preventDefault();
      navigate(NAV_SHORTCUT_TARGETS[nextIndex]);
    };

    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [navigate, parsedRoute.view, parsedRoute.gallerySection]);
}
