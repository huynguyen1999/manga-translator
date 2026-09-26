import { useEffect, useRef, useState } from 'react';
import type { FinishedImage, MangaGroupSummary } from '@/types';
import { apiUrl } from '@/utils/api';

export function useMangaImages(
  activeSummaries: MangaGroupSummary[],
  reviewOnly: boolean,
  galleryRevision: number,
) {
  const [expandedGroups, setExpandedGroups] = useState<Record<string, boolean>>({});
  const [mangaImages, setMangaImages] = useState<Record<string, FinishedImage[]>>({});
  const [loadingManga, setLoadingManga] = useState<Record<string, boolean>>({});
  const mangaImageLoadsRef = useRef<Partial<Record<string, Promise<FinishedImage[]>>>>({});

  useEffect(() => {
    setMangaImages({});
    mangaImageLoadsRef.current = {};
    setExpandedGroups({});
  }, [reviewOnly]);

  useEffect(() => {
    if (galleryRevision === 0) return;
    setMangaImages({});
    mangaImageLoadsRef.current = {};
  }, [galleryRevision]);

  const loadMangaImagesIfNeeded = async (title: string, detail?: string, requestedGroupId?: string, force = false): Promise<FinishedImage[]> => {
    if (mangaImages[title] && !force) {
      return mangaImages[title];
    }
    if (mangaImageLoadsRef.current[title] && !force) return mangaImageLoadsRef.current[title];

    setLoadingManga((prev) => ({ ...prev, [title]: true }));
    const request = (async () => {
      try {
      const groupId = requestedGroupId || activeSummaries.find((summary) => summary.title === title)?.id;
      const groupFilter = groupId || title;
      const detailParam = detail ? `&detail=${encodeURIComponent(detail)}` : '';
      const reviewParam = reviewOnly ? '&review=pending' : '';
      const rawItems: any[] = [];
      let offset = 0;
      let hasNextPage = true;
      while (hasNextPage) {
        const res = await fetch(
          apiUrl(
            `/api/results/list?groupId=${encodeURIComponent(groupFilter)}&sort=alpha&limit=500&offset=${offset}${detailParam}${reviewParam}`,
          ),
        );
        if (!res.ok) break;
        const data = await res.json();
        if (!Array.isArray(data.items)) break;
        rawItems.push(...data.items);
        hasNextPage = data.nextOffset !== null && data.nextOffset !== undefined;
        offset = Number(data.nextOffset || 0);
      }
      if (rawItems.length > 0) {
          const items: FinishedImage[] = rawItems.map((item: any) => ({
            id: item.id || item.folder,
            originalName: (item.originalName && item.originalName !== 'Unknown') ? item.originalName : `${item.folder}.png`,
            pageOrder: item.pageOrder ?? null,
            sourcePath: item.sourcePath ?? null,
            result: item.resultUrl
              ? apiUrl(item.resultUrl)
              : item.folder
              ? apiUrl(`/result/${item.folder}/final.png`)
              : "",
            thumbnailUrl: item.thumbnailUrl
              ? apiUrl(item.thumbnailUrl)
              : (item.folder ? apiUrl(`/result/${item.folder}/thumbnail.webp`) : null),
            batchPreviewUrl: item.batchPreviewUrl ? apiUrl(item.batchPreviewUrl) : null,
            coverUrl: item.coverUrl ? apiUrl(item.coverUrl) : null,
            detailPreviewUrl: item.detailPreviewUrl ? apiUrl(item.detailPreviewUrl) : null,
            readerUrl: item.readerUrl ? apiUrl(item.readerUrl) : null,
            fullUrl: item.fullUrl ? apiUrl(item.fullUrl) : (item.resultUrl ? apiUrl(item.resultUrl) : null),
            sourceType: item.sourceType === 'original' ? 'original' : 'translated',
            inputUrl: item.inputUrl
              ? apiUrl(item.inputUrl)
              : (item.folder ? apiUrl(`/result/${item.folder}/input.png`) : null),
            inpaintedUrl: item.inpaintedUrl ? apiUrl(item.inpaintedUrl) : null,
            textRegionsUrl: item.textRegionsUrl ? apiUrl(item.textRegionsUrl) : null,
            bubbleMaskUrl: item.bubbleMaskUrl ? apiUrl(item.bubbleMaskUrl) : null,
            hasTextRegions: item.hasTextRegions ?? Boolean(item.textRegionsUrl),
            reviewStatus: item.reviewStatus || (item.needsReview ? 'pending' : 'not_required'),
            reviewedAt: item.reviewedAt || null,
            folder: item.folder,
            groupId: item.groupId || groupId || null,
            mangaTitle: item.mangaTitle || title,
            seriesId: item.seriesId || null,
            seriesTitle: item.seriesTitle || null,
            finishedAt: item.finishedAt ? new Date(item.finishedAt) : new Date(),
            startedAt: item.startedAt ? new Date(item.startedAt) : null,
            durationMs: item.durationMs ?? null,
            settings: item.settings || {},
          }));
          setMangaImages((prev) => ({
            ...prev,
            [title]: items,
          }));
          return items;
      }
      } catch (err) {
        console.error(`Failed to fetch images for manga "${title}":`, err);
      } finally {
        delete mangaImageLoadsRef.current[title];
        setLoadingManga((prev) => ({ ...prev, [title]: false }));
      }
      return [];
    })();
    mangaImageLoadsRef.current[title] = request;
    return request;
  };

  return {
    expandedGroups,
    setExpandedGroups,
    mangaImages,
    setMangaImages,
    loadingManga,
    loadMangaImagesIfNeeded,
  };
}
