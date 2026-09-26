import { useCallback, useState } from 'react';
import type { FinishedImage, StudioFile, TranslationSettings } from '@/types';
import { buildStudioPreviewImage, type StudioPreviewOptions } from '@/features/studio/buildPreviewImage';

interface StudioLightboxOptions {
  folderMapRef: { current: Map<string, string> };
  settings: Partial<TranslationSettings>;
}

export function useStudioLightbox({ folderMapRef, settings }: StudioLightboxOptions) {
  const [selectedImageForModal, setSelectedImageForModal] = useState<FinishedImage | null>(null);
  const [selectedImageRetry, setSelectedImageRetry] = useState<(() => void | Promise<void>) | null>(null);
  const [selectedModalImages, setSelectedModalImages] = useState<FinishedImage[]>([]);
  const [selectedModalIndex, setSelectedModalIndex] = useState(-1);

  const handleOpenLightbox = (
    file: File | StudioFile | string,
    result: Blob | File | string | null,
    onRetry?: () => void | Promise<void>,
    sourceType?: FinishedImage['sourceType'],
    options?: StudioPreviewOptions,
  ) => {
    const finishedItem = buildStudioPreviewImage({
      file,
      result,
      sourceType,
      options,
      folderMap: folderMapRef.current,
      settings,
    });
    if (!finishedItem) return;
    setSelectedImageForModal(finishedItem);
    setSelectedModalImages(options?.images ?? []);
    setSelectedModalIndex(options?.currentIndex ?? -1);
    setSelectedImageRetry(options?.images?.length ? null : () => onRetry ?? null);
  };

  const closeStudioViewer = useCallback(() => {
    setSelectedImageForModal(null);
    setSelectedImageRetry(null);
    setSelectedModalImages([]);
    setSelectedModalIndex(-1);
  }, []);

  return {
    selectedImageForModal,
    setSelectedImageForModal,
    selectedImageRetry,
    setSelectedImageRetry,
    selectedModalImages,
    setSelectedModalImages,
    selectedModalIndex,
    setSelectedModalIndex,
    handleOpenLightbox,
    closeStudioViewer,
  };
}
