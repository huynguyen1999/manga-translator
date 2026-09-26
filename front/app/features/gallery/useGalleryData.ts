import { useEffect, useLayoutEffect, useRef, useState } from 'react';
import type { Dispatch, SetStateAction } from 'react';
import type { MangaGroupSummary } from '@/types';
import type { ParsedRoute } from '@/utils/routeState';
import { useServerMangaGroupTitles } from './useServerMangaGroupTitles';

type GalleryPage = {
  groups: MangaGroupSummary[];
  totalGroups: number;
  totalImages: number;
};

const useClientLayoutEffect = typeof window === 'undefined' ? useEffect : useLayoutEffect;

export function useGalleryData() {
  const [mangaSummaries, setMangaSummaries] = useState<MangaGroupSummary[]>([]);
  const serverTitles = useServerMangaGroupTitles();
  const [totalMangaCount, setTotalMangaCount] = useState(0);
  const [totalGalleryCount, setTotalGalleryCount] = useState(0);
  const [isGalleryLoading, setIsGalleryLoading] = useState(true);
  const [galleryRevision, setGalleryRevision] = useState(0);
  const galleryLoadRequestRef = useRef(0);
  const galleryPageCacheRef = useRef(new Map<string, GalleryPage>());

  return {
    mangaSummaries,
    setMangaSummaries,
    ...serverTitles,
    totalMangaCount,
    setTotalMangaCount,
    totalGalleryCount,
    setTotalGalleryCount,
    isGalleryLoading,
    setIsGalleryLoading,
    galleryRevision,
    setGalleryRevision,
    galleryLoadRequestRef,
    galleryPageCacheRef,
  };
}

export function useGalleryLoadEffect(
  activeView: string,
  parsedRoute: ParsedRoute,
  loadMangaSummaries: (signal?: AbortSignal) => Promise<void>,
  setIsGalleryLoading: Dispatch<SetStateAction<boolean>>,
) {
  useClientLayoutEffect(() => {
    if (activeView !== 'gallery') return;
    if (parsedRoute.gallerySection === 'series' || parsedRoute.overlay !== 'none') {
      setIsGalleryLoading(false);
      return;
    }
    const controller = new AbortController();
    void loadMangaSummaries(controller.signal);
    return () => controller.abort();
  }, [activeView, loadMangaSummaries, parsedRoute.gallerySection, parsedRoute.overlay]);
}
