import { useEffect, type Dispatch, type SetStateAction } from 'react';
import type { FinishedImage } from '@/types';
import { apiUrl } from '@/utils/api';

interface GalleryPageModalRoutesOptions {
  initialPageViewFolder?: string | null;
  initialPageEditFolder?: string | null;
  finishedImages: FinishedImage[];
  mangaImages: Record<string, FinishedImage[]>;
  selectedImage: FinishedImage | null;
  isModalOpen: boolean;
  editingImage: FinishedImage | null;
  setSelectedImage: Dispatch<SetStateAction<FinishedImage | null>>;
  setIsModalOpen: Dispatch<SetStateAction<boolean>>;
  setEditingImage: Dispatch<SetStateAction<FinishedImage | null>>;
}

const mapResultToFinishedImage = (data: any): FinishedImage => ({
  id: data.id || data.folder,
  groupId: data.groupId || null,
  originalName: (data.originalName && data.originalName !== 'Unknown') ? data.originalName : `${data.folder}.png`,
  pageOrder: data.pageOrder ?? null,
  sourcePath: data.sourcePath ?? null,
  result: data.resultUrl ? apiUrl(data.resultUrl) : apiUrl(`/result/${data.folder}/final.png`),
  thumbnailUrl: data.thumbnailUrl ? apiUrl(data.thumbnailUrl) : (data.folder ? apiUrl(`/result/${data.folder}/thumbnail.webp`) : null),
  batchPreviewUrl: data.batchPreviewUrl ? apiUrl(data.batchPreviewUrl) : (data.folder ? apiUrl(`/result/${data.folder}/batch.webp`) : null),
  coverUrl: data.coverUrl ? apiUrl(data.coverUrl) : null,
  detailPreviewUrl: data.detailPreviewUrl ? apiUrl(data.detailPreviewUrl) : (data.folder ? apiUrl(`/result/${data.folder}/preview.webp`) : null),
  readerUrl: data.readerUrl ? apiUrl(data.readerUrl) : (data.folder ? apiUrl(`/result/${data.folder}/reader.webp`) : null),
  fullUrl: data.fullUrl ? apiUrl(data.fullUrl) : (data.resultUrl ? apiUrl(data.resultUrl) : null),
  sourceType: data.sourceType === 'original' ? 'original' : 'translated',
  inputUrl: data.inputUrl ? apiUrl(data.inputUrl) : apiUrl(`/result/${data.folder}/input.png`),
  inpaintedUrl: data.inpaintedUrl ? apiUrl(data.inpaintedUrl) : null,
  textRegionsUrl: data.textRegionsUrl ? apiUrl(data.textRegionsUrl) : null,
  bubbleMaskUrl: data.bubbleMaskUrl ? apiUrl(data.bubbleMaskUrl) : null,
  hasTextRegions: data.hasTextRegions ?? Boolean(data.textRegionsUrl),
  folder: data.folder,
  mangaTitle: data.mangaTitle || 'Ungrouped',
  finishedAt: data.finishedAt ? new Date(data.finishedAt) : new Date(),
  startedAt: data.startedAt ? new Date(data.startedAt) : null,
  durationMs: data.durationMs ?? null,
  settings: data.settings || {},
});

export function useGalleryPageModalRoutes({
  initialPageViewFolder, initialPageEditFolder, finishedImages, mangaImages, selectedImage,
  isModalOpen, editingImage, setSelectedImage, setIsModalOpen, setEditingImage,
}: GalleryPageModalRoutesOptions) {
  useEffect(() => {
    if (!initialPageViewFolder) {
      if (isModalOpen) { setIsModalOpen(false); setSelectedImage(null); }
      return;
    }
    if (selectedImage?.folder === initialPageViewFolder && isModalOpen) return;
    const found = (finishedImages || []).find((img) => img.folder === initialPageViewFolder);
    if (found) { setSelectedImage(found); setIsModalOpen(true); return; }
    for (const imgs of Object.values(mangaImages)) {
      const match = imgs.find((img) => img.folder === initialPageViewFolder);
      if (match) { setSelectedImage(match); setIsModalOpen(true); return; }
    }
    let isCancelled = false;
    fetch(apiUrl(`/api/results/${encodeURIComponent(initialPageViewFolder)}`))
      .then((res) => (res.ok ? res.json() : null))
      .then((data) => {
        if (isCancelled || !data) return;
        setSelectedImage(mapResultToFinishedImage(data));
        setIsModalOpen(true);
      })
      .catch((err) => console.error('Failed to load page for modal:', err));
    return () => { isCancelled = true; };
  }, [initialPageViewFolder, finishedImages, mangaImages]);

  useEffect(() => {
    if (!initialPageEditFolder) {
      if (editingImage) setEditingImage(null);
      return;
    }
    if (editingImage?.folder === initialPageEditFolder) return;
    const found = (finishedImages || []).find((img) => img.folder === initialPageEditFolder);
    if (found) { setEditingImage(found.sourceType === 'original' ? null : found); return; }
    for (const imgs of Object.values(mangaImages)) {
      const match = imgs.find((img) => img.folder === initialPageEditFolder);
      if (match) { setEditingImage(match.sourceType === 'original' ? null : match); return; }
    }
    let isCancelled = false;
    fetch(apiUrl(`/api/results/${encodeURIComponent(initialPageEditFolder)}`))
      .then((res) => (res.ok ? res.json() : null))
      .then((data) => {
        if (isCancelled || !data) return;
        const img = mapResultToFinishedImage(data);
        setEditingImage(img.sourceType === 'original' ? null : img);
      })
      .catch((err) => console.error('Failed to load page for edit:', err));
    return () => { isCancelled = true; };
  }, [initialPageEditFolder, finishedImages, mangaImages]);
}
