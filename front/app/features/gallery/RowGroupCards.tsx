import React, { useCallback, useMemo } from 'react';
import type { FinishedImage } from '@/types';
import { GalleryCard } from './GalleryCard';

interface RowGroupCardsProps {
  title: string;
  images: FinishedImage[];
  highlightedImageId: string | null;
  selectedImageIds: Set<string>;
  onToggleSelectImage: (id: string, groupImages?: FinishedImage[], shiftKey?: boolean) => void;
  onMoveImage: (image: FinishedImage) => void;
  onReadFromHere: (title: string, images: FinishedImage[], idx: number) => void;
  onClickImage: (image: FinishedImage) => void;
  onDownloadImage: (image: FinishedImage) => void;
  onDeleteImage?: (image: FinishedImage) => void;
  onEditImage?: (image: FinishedImage) => void;
  onRerenderImage?: (image: FinishedImage) => void;
  pageViewState: { from: string };
}

export const RowGroupCards: React.FC<RowGroupCardsProps> = React.memo(({
  title,
  images,
  highlightedImageId,
  selectedImageIds,
  onToggleSelectImage,
  onMoveImage,
  onReadFromHere,
  onClickImage,
  onDownloadImage,
  onDeleteImage,
  onEditImage,
  onRerenderImage,
  pageViewState,
}) => {
  const duplicateNames = useMemo(() => {
    const counts = new Map<string, number>();
    images.forEach((image) => {
      const key = image.originalName.toLocaleLowerCase();
      counts.set(key, (counts.get(key) || 0) + 1);
    });
    return new Set([...counts].filter(([, count]) => count > 1).map(([name]) => name));
  }, [images]);

  const handleToggleSelect = useCallback((id: string, shiftKey = false) => {
    onToggleSelectImage(id, images, shiftKey);
  }, [onToggleSelectImage, images]);

  const handleRead = useCallback((pageIndex: number) => {
    onReadFromHere(title, images, pageIndex);
  }, [onReadFromHere, title, images]);

  return (
    <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-5 gap-4">
      {images.map((image, idx) => (
        <GalleryCard
          key={image.id}
          image={image}
          pageIndex={idx + 1}
          showSourcePath={duplicateNames.has(image.originalName.toLocaleLowerCase())}
          isHighlighted={highlightedImageId === image.id}
          isSelected={selectedImageIds.has(image.id)}
          onToggleSelect={handleToggleSelect}
          onMoveToManga={onMoveImage}
          onReadFromHere={handleRead}
          onClick={onClickImage}
          onDownload={onDownloadImage}
          onDelete={onDeleteImage}
          onEdit={image.hasTextRegions && image.sourceType !== 'original' ? onEditImage : undefined}
          onRerender={image.hasTextRegions && image.sourceType !== 'original' ? onRerenderImage : undefined}
          pageViewState={pageViewState}
        />
      ))}
    </div>
  );
});

RowGroupCards.displayName = 'RowGroupCards';
