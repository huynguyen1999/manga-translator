import React from 'react';
import { Icon } from '@iconify/react';
import type { FinishedImage } from '@/types';
import { useGalleryThumbnail } from './useGalleryThumbnail';

// ──────────────────────────────────────────────────────────────
// Manga Group Thumbnail — first-page cover shown in group header
// ──────────────────────────────────────────────────────────────
export const MangaGroupThumbnail: React.FC<{ image: FinishedImage }> = React.memo(({ image }) => {
  const { src, hasError, isLoaded, handleLoad, handleError } = useGalleryThumbnail(image, "cover");

  if (!src || hasError) {
    return (
      <div className="w-9 h-12 rounded-md bg-zinc-200 dark:bg-zinc-700 flex items-center justify-center shrink-0">
        <Icon icon="carbon:image" className="w-4 h-4 text-zinc-400" />
      </div>
    );
  }

  return (
    <img
      src={src}
      alt="cover"
      className="w-9 h-12 rounded-md object-cover shadow-sm border border-zinc-200 dark:border-zinc-700 shrink-0"
      loading={isLoaded ? 'eager' : 'lazy'}
      decoding="async"
      onLoad={handleLoad}
      onError={handleError}
    />
  );
});

MangaGroupThumbnail.displayName = 'MangaGroupThumbnail';
